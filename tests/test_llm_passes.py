from __future__ import annotations

import numpy as np

from tests.mock_ollama import MockOllama
from video_security.config import Config
from video_security.llm.detail import detail_events
from video_security.llm.ollama import OllamaClient
from video_security.llm.prompts import PROMPT_VERSION, TRIAGE_SCHEMA
from video_security.llm.triage import LLMEvent, TriageResult, triage_events


def _make_event(id_: int, priority: float, score: float, **kw: object) -> LLMEvent:
    defaults: dict[str, object] = {
        "id": id_,
        "event_type": "intrusion",
        "start_sec": 0.0,
        "end_sec": 1.0,
        "detector_score": score,
        "priority": priority,
        "keyframes": [np.zeros((100, 200, 3), dtype=np.uint8)],
        "transcript_window": "",
    }
    defaults.update(kw)
    return LLMEvent(**defaults)  # type: ignore[arg-type]


def test_triage_basic() -> None:
    mock = MockOllama(
        response_text=(
            '{"relevant": true, "event_type": "intrusion", '
            '"description": "person", "confidence": "high"}'
        )
    )
    with mock.start() as m:
        events = [
            _make_event(1, 0.9, 0.8),
            _make_event(2, 0.6, 0.6, event_type="loitering", transcript_window="hello"),
        ]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = triage_events(events, Config(), client)

        assert len(results) == 2
        r0 = results[0]
        assert r0.event_id == 1
        assert r0.relevant is True
        assert r0.event_type == "intrusion"
        assert r0.description == "person"
        assert r0.confidence == "high"
        assert r0.model_digest
        assert r0.prompt_version == PROMPT_VERSION
        assert r0.analysis_type == "triage"

        tags_requests = [r for r in m.requests if r["path"].startswith("/api/tags")]
        assert len(tags_requests) >= 1

        post_requests = [r for r in m.requests if r["path"] == "/api/generate"]
        first_post = post_requests[0]
        assert "images" in first_post["body"]
        assert len(first_post["body"]["images"]) > 0
        assert first_post["body"]["format"] == TRIAGE_SCHEMA


def test_triage_max_events_breaker() -> None:
    mock = MockOllama()
    with mock.start() as m:
        events = [_make_event(i, float(i), float(i)) for i in range(1, 6)]
        config = Config()
        config.engine.max_llm_events = 2
        client = OllamaClient(m.base_url, timeout_s=10)
        results = triage_events(events, config, client)
        assert len(results) == 2
        assert {r.event_id for r in results} == {5, 4}


def test_triage_no_keyframes() -> None:
    mock = MockOllama()
    with mock.start() as m:
        events = [_make_event(1, 0.9, 0.8, keyframes=[])]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = triage_events(events, Config(), client)
        assert len(results) == 1
        post_requests = [r for r in m.requests if r["path"] == "/api/generate"]
        assert "images" not in post_requests[0]["body"]


def test_triage_error_skips() -> None:
    mock = MockOllama(mode="fail500")
    with mock.start() as m:
        events = [_make_event(1, 0.9, 0.8)]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = triage_events(events, Config(), client)
        assert results == []


def test_detail_basic() -> None:
    mock = MockOllama(
        response_text=(
            '{"relevant": true, "event_type": "intrusion", '
            '"description": "x", "evidence_rationale": "y", '
            '"recommended_action": "escalate", "confidence": "high"}'
        )
    )
    with mock.start() as m:
        event = _make_event(
            1,
            0.9,
            0.8,
            keyframes=[np.zeros((100, 200, 3), dtype=np.uint8) for _ in range(3)],
        )
        triaged = [
            TriageResult(
                event_id=1,
                relevant=True,
                event_type="intrusion",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            )
        ]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = detail_events(triaged, [event], Config(), client)
        assert len(results) == 1
        dr = results[0]
        assert dr.event_id == 1
        assert dr.relevant is True
        assert dr.event_type == "intrusion"
        assert dr.description == "x"
        assert dr.evidence_rationale == "y"
        assert dr.recommended_action == "escalate"
        assert dr.confidence == "high"
        assert dr.analysis_type == "detail"
        assert dr.prompt_version == PROMPT_VERSION
        post_requests = [r for r in m.requests if r["path"] == "/api/generate"]
        assert len(post_requests) == 1
        assert len(post_requests[0]["body"]["images"]) == 3


def test_detail_only_relevant() -> None:
    mock = MockOllama()
    with mock.start() as m:
        triaged = [
            TriageResult(
                event_id=1,
                relevant=True,
                event_type="",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            ),
            TriageResult(
                event_id=2,
                relevant=False,
                event_type="",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            ),
        ]
        events = [_make_event(1, 0.9, 0.8), _make_event(2, 0.6, 0.6)]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = detail_events(triaged, events, Config(), client)
        assert len(results) == 1
        assert results[0].event_id == 1


def test_tile_image() -> None:
    from video_security.llm.detail import tile_image

    img = np.zeros((100, 200, 3), dtype=np.uint8)
    tiles = tile_image(img, overlap=0.15)
    assert len(tiles) == 4
    for t in tiles:
        assert 100 <= t.shape[1] <= 116
        assert 50 <= t.shape[0] <= 65
    assert np.array_equal(tiles[0][5, 5], img[5, 5])


def test_detail_escalation_ladder_low() -> None:
    mock = MockOllama(
        response_text=(
            '{"relevant": true, "event_type": "intrusion", '
            '"description": "x", "evidence_rationale": "y", '
            '"recommended_action": "log_only", "confidence": "low"}'
        )
    )
    with mock.start() as m:
        event = _make_event(1, 0.9, 0.8)
        triaged = [
            TriageResult(
                event_id=1,
                relevant=True,
                event_type="intrusion",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            )
        ]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = detail_events(triaged, [event], Config(), client)
        post_requests = [r for r in m.requests if r["path"] == "/api/generate"]
        assert len(post_requests) == 5
        assert len(results) == 1
        assert results[0].tiled is False
        assert results[0].confidence == "low"


def test_detail_escalation_ladder_high() -> None:
    mock = MockOllama(
        response_text=(
            '{"relevant": true, "event_type": "intrusion", '
            '"description": "x", "evidence_rationale": "y", '
            '"recommended_action": "log_only", "confidence": "high"}'
        )
    )
    with mock.start() as m:
        event = _make_event(1, 0.9, 0.8)
        triaged = [
            TriageResult(
                event_id=1,
                relevant=True,
                event_type="intrusion",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            )
        ]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = detail_events(triaged, [event], Config(), client)
        post_requests = [r for r in m.requests if r["path"] == "/api/generate"]
        assert len(post_requests) == 1
        assert len(results) == 1
        assert results[0].tiled is False
        assert results[0].confidence == "high"


def test_detail_error_skips() -> None:
    mock = MockOllama(mode="fail500")
    with mock.start() as m:
        event = _make_event(1, 0.9, 0.8)
        triaged = [
            TriageResult(
                event_id=1,
                relevant=True,
                event_type="intrusion",
                description="",
                confidence="",
                raw_response="{}",
                model_digest="d",
                prompt_version="v",
            )
        ]
        client = OllamaClient(m.base_url, timeout_s=10)
        results = detail_events(triaged, [event], Config(), client)
        assert results == []