from __future__ import annotations

import sqlite3
import struct
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from tests.mock_ollama import MockOllama
from video_security.db import connect, init_db
from video_security.llm.embeddings import (
    embed_query,
    get_all_embeddings,
    semantic_search,
)
from video_security.llm.ollama import OllamaClient, OllamaError


@pytest.fixture
def seeded_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "test.db"
    conn = connect(str(db_path))
    init_db(conn)
    conn.executescript(
        """
        INSERT INTO jobs (id, video_path, video_hash, status, recording_start_utc,
                          import_id, created_at)
        VALUES
        (1, '/tmp/clips/NORMAL/front/a.mp4', 'h1', 'done',
         '2025-09-22 02:57:00', 'imp-1', '2025-09-22 03:00:00');
        INSERT INTO clips (job_id, clip_id, filename, channel, priority,
                           recording_start_utc, duration_sec, lighting, has_audio)
        VALUES
        (1, 0, 'a.mp4', 'front', 0.3, '2025-09-22 02:57:00', 120.0, 'day', 1);
        INSERT INTO events (id, job_id, event_type, start_sec, end_sec, clip_id,
                            track_id, keyframes_json, faces_json, detector_score,
                            priority, status, llm_result_id)
        VALUES
         (10, 1, 'intrusion', 32.0, 33.0, 0, NULL, '[]', NULL, 0.9, 0.9, 'detailed', NULL),
         (11, 1, 'loitering', 48.0, 50.0, 0, NULL, '[]', NULL, 0.5, 0.5, 'detailed', NULL),
         (12, 1, 'hard_brake', 60.0, 61.0, 0, NULL, '[]', NULL, 0.7, 0.7, 'detailed', 1);
        INSERT INTO analysis_results (id, job_id, event_id, model_digest, prompt_version,
                                    analysis_type, raw_response, confidence, retry_count,
                                    tiled_results)
        VALUES (1, 1, 12, 'sha256:abc', 'v1', 'detail',
                '{"description": "car stopped abruptly at intersection"}',
                NULL, 0, NULL);
        """
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def mock_ollama() -> Iterator[MockOllama]:
    mock = MockOllama(models=["nomic-embed-text"])
    with mock:
        yield mock


def test_embed_query_returns_list(mock_ollama: MockOllama) -> None:
    client = OllamaClient(base_url=mock_ollama.base_url)
    result = embed_query(client, "nomic-embed-text", "test text")
    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], float)


def test_embed_query_missing_model_raises() -> None:
    client = OllamaClient(base_url="http://127.0.0.1:19999")
    with pytest.raises(OllamaError):
        embed_query(client, "nomic-embed-text", "test")


def test_get_all_embeddings_empty(seeded_db: sqlite3.Connection) -> None:
    rows = get_all_embeddings(seeded_db)
    assert rows == []


def test_semantic_search_empty(seeded_db: sqlite3.Connection) -> None:
    q = list(np.random.default_rng(42).random(768).astype(np.float32))
    results = semantic_search(seeded_db, q)
    assert results == []


def test_semantic_search_returns_top(seeded_db: sqlite3.Connection) -> None:
    rng = np.random.default_rng(42)
    e1 = rng.random(768).astype(np.float32)
    e2 = rng.random(768).astype(np.float32)
    e3 = rng.random(768).astype(np.float32)
    bl1 = struct.pack("768f", *e1)
    bl2 = struct.pack("768f", *e2)
    bl3 = struct.pack("768f", *e3)
    conn = seeded_db
    conn.execute(
        "INSERT OR REPLACE INTO event_embeddings (event_id, embedding, model) "
        "VALUES (?, ?, 'nomic-embed-text')",
        (10, bl1),
    )
    conn.execute(
        "INSERT OR REPLACE INTO event_embeddings (event_id, embedding, model) "
        "VALUES (?, ?, 'nomic-embed-text')",
        (11, bl2),
    )
    conn.execute(
        "INSERT OR REPLACE INTO event_embeddings (event_id, embedding, model) "
        "VALUES (?, ?, 'nomic-embed-text')",
        (12, bl3),
    )
    conn.commit()

    q = e1.tolist()
    results = semantic_search(conn, q)
    assert len(results) == 3
    assert results[0]["event_id"] == 10
    assert results[0]["score"] == pytest.approx(1.0, abs=0.01)
    assert results[2]["event_id"] != 10


def test_semantic_search_respects_top_k(seeded_db: sqlite3.Connection) -> None:
    rng = np.random.default_rng(42)
    conn = seeded_db
    existing_events = [10, 11, 12]
    for eid in existing_events:
        vec = rng.random(768).astype(np.float32)
        bl = struct.pack("768f", *vec)
        conn.execute(
            "INSERT OR REPLACE INTO event_embeddings (event_id, embedding, model) "
            "VALUES (?, ?, 'nomic-embed-text')",
            (eid, bl),
        )
    conn.commit()
    q = list(rng.random(768).astype(np.float32))
    results = semantic_search(conn, q, top_k=2)
    assert len(results) == 2
    results_full = semantic_search(conn, q, top_k=10)
    assert len(results_full) == 3


def test_event_10_snippet(seeded_db: sqlite3.Connection) -> None:
    from video_security.llm.embeddings import _event_text

    snippet = _event_text(seeded_db, 10)
    assert snippet == "intrusion"


def test_event_12_snippet_llm(seeded_db: sqlite3.Connection) -> None:
    from video_security.llm.embeddings import _event_text

    snippet = _event_text(seeded_db, 12)
    assert snippet == "car stopped abruptly at intersection"