from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np

from video_security.config import Config
from video_security.ingest.frames import FrameData
from video_security.prefilter.ocr import OCRResult, vision_ocr

_OCR_FN = Callable[[np.ndarray], list[OCRResult]]

_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


@dataclass
class SceneText:
    frame_number: int
    timestamp_sec: float
    text: str
    text_kind: str
    confidence: float


def classify_text(text: str) -> str:
    if _TIME_RE.search(text) or _DATE_RE.search(text):
        return "timestamp"
    return "other"


def sample_scene_text(
    frames: Iterable[FrameData],
    config: Config,
    ocr_fn: _OCR_FN | None = None,
) -> list[SceneText]:
    ocr_fn = ocr_fn or vision_ocr
    interval = config.prefilter.scene_text_sample_sec
    out: list[SceneText] = []
    seen_windows: set[int] = set()
    for frame in frames:
        window = int(frame.timestamp_sec // interval)
        if window in seen_windows:
            continue
        seen_windows.add(window)
        for r in ocr_fn(frame.image):
            if r.confidence < config.prefilter.ocr_min_conf:
                continue
            text = r.text.strip()
            if not text:
                continue
            out.append(
                SceneText(
                    frame_number=frame.frame_number,
                    timestamp_sec=frame.timestamp_sec,
                    text=text,
                    text_kind=classify_text(text),
                    confidence=r.confidence,
                )
            )
    return out
