from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from video_security.config import Config
from video_security.llm.ollama import OllamaClient, OllamaError, encode_image_jpeg
from video_security.llm.prompts import DETAIL_SCHEMA, PROMPT_VERSION, build_detail_prompt
from video_security.llm.triage import LLMEvent, TriageResult


@dataclass
class DetailResult:
    event_id: int
    relevant: bool
    event_type: str
    description: str
    evidence_rationale: str
    recommended_action: str
    confidence: str
    tiled: bool
    raw_response: str
    model_digest: str
    prompt_version: str
    analysis_type: str = "detail"


_CONF_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def tile_image(image: np.ndarray, overlap: float = 0.15) -> list[np.ndarray]:
    h, w = image.shape[:2]
    half_h = h / 2
    half_w = w / 2
    expand_h = int(overlap * half_h)
    expand_w = int(overlap * half_w)
    tiles: list[np.ndarray] = []
    for row in range(2):
        for col in range(2):
            y_start = max(0, int(row * half_h - expand_h))
            y_end = min(h, int((row + 1) * half_h + expand_h))
            x_start = max(0, int(col * half_w - expand_w))
            x_end = min(w, int((col + 1) * half_w + expand_w))
            tiles.append(image[y_start:y_end, x_start:x_end])
    return tiles


def detail_events(
    triaged: list[TriageResult],
    events: list[LLMEvent],
    config: Config,
    client: OllamaClient,
) -> list[DetailResult]:
    relevant_triaged = [t for t in triaged if t.relevant]
    if not relevant_triaged:
        return []
    events_by_id = {e.id: e for e in events}
    model = config.llm_detail.model
    digest = client.model_digest(model)
    results: list[DetailResult] = []
    for t in relevant_triaged:
        event = events_by_id.get(t.event_id)
        if event is None:
            continue
        keyframes = event.keyframes[:3]
        images = [encode_image_jpeg(k) for k in keyframes] if keyframes else None
        prompt = build_detail_prompt(
            event.event_type,
            event.detector_score,
            event.start_sec,
            event.end_sec,
            event.transcript_window,
            tiled=False,
        )
        try:
            data = client.generate_json(
                model,
                prompt,
                images,
                DETAIL_SCHEMA,
                num_ctx=config.llm_detail.num_ctx,
            )
        except OllamaError:
            continue
        confidence = str(data.get("confidence", "low"))
        tiled = False
        if confidence == "low" and keyframes:
            best_keyframe = keyframes[0]
            tiles = tile_image(best_keyframe)
            best_confidence = confidence
            best_tile_data: dict[str, Any] | None = None
            for tile in tiles:
                tile_prompt = build_detail_prompt(
                    event.event_type,
                    event.detector_score,
                    event.start_sec,
                    event.end_sec,
                    event.transcript_window,
                    tiled=True,
                )
                try:
                    tdata = client.generate_json(
                        model,
                        tile_prompt,
                        [encode_image_jpeg(tile)],
                        DETAIL_SCHEMA,
                        num_ctx=config.llm_detail.num_ctx,
                    )
                except OllamaError:
                    continue
                tconf = str(tdata.get("confidence", "low"))
                if _CONF_RANK.get(tconf, 0) > _CONF_RANK.get(best_confidence, 0):
                    best_tile_data = tdata
                    best_confidence = tconf
            if best_tile_data is not None:
                data = best_tile_data
                confidence = str(data.get("confidence", "low"))
                tiled = True
        results.append(
            DetailResult(
                event_id=t.event_id,
                relevant=bool(data.get("relevant", False)),
                event_type=str(data.get("event_type", "none")),
                description=str(data.get("description", "")),
                evidence_rationale=str(data.get("evidence_rationale", "")),
                recommended_action=str(data.get("recommended_action", "log_only")),
                confidence=confidence,
                tiled=tiled,
                raw_response=json.dumps(data),
                model_digest=digest,
                prompt_version=PROMPT_VERSION,
            )
        )
    return results