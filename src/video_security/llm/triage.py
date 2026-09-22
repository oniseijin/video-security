from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from video_security.config import Config
from video_security.llm import LLMClient
from video_security.llm.ollama import OllamaError, encode_image_jpeg
from video_security.llm.prompts import PROMPT_VERSION, TRIAGE_SCHEMA, build_triage_prompt


@dataclass
class LLMEvent:
    id: int
    event_type: str
    start_sec: float
    end_sec: float
    detector_score: float
    priority: float
    keyframes: list[np.ndarray]
    transcript_window: str = ""
    evidence_summary: str = ""


@dataclass
class TriageResult:
    event_id: int
    relevant: bool
    event_type: str
    description: str
    confidence: str
    raw_response: str
    model_digest: str
    prompt_version: str
    analysis_type: str = "triage"


def triage_events(
    events: list[LLMEvent],
    config: Config,
    client: LLMClient,
) -> list[TriageResult]:
    sorted_events = sorted(
        events, key=lambda e: (e.priority, e.detector_score), reverse=True
    )
    capped = sorted_events[: config.engine.max_llm_events]
    model = config.llm_triage.model
    digest = client.model_digest(model)
    results: list[TriageResult] = []
    for event in capped:
        keyframe = event.keyframes[0] if event.keyframes else None
        images = [encode_image_jpeg(keyframe)] if keyframe is not None else None
        prompt = build_triage_prompt(
            event.event_type,
            event.detector_score,
            event.start_sec,
            event.transcript_window,
            evidence_summary=event.evidence_summary,
        )
        try:
            data = client.generate_json(
                model,
                prompt,
                images,
                TRIAGE_SCHEMA,
                num_ctx=config.llm_triage.num_ctx,
            )
        except OllamaError:
            continue
        results.append(
            TriageResult(
                event_id=event.id,
                relevant=bool(data.get("relevant", False)),
                event_type=str(data.get("event_type", "none")),
                description=str(data.get("description", "")),
                confidence=str(data.get("confidence", "low")),
                raw_response=json.dumps(data),
                model_digest=digest,
                prompt_version=PROMPT_VERSION,
            )
        )
    return results