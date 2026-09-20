from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from video_security.config import Config
from video_security.prefilter.ocr import LEVEL_ACCURATE, OCRResult, upscale_crop, vision_ocr
from video_security.prefilter.vehicles import crop_vehicle

_OCR_FN = Callable[[np.ndarray], list[OCRResult]]


def normalize_plate(text: str) -> str:
    return re.sub(r"[^A-Z0-9\u3040-\u30ff\u4e00-\u9faf]", "", text.upper())


def plate_like(text: str) -> bool:
    norm = normalize_plate(text)
    if not 3 <= len(norm) <= 12:
        return False
    return any(c.isdigit() for c in norm) and any(c.isalpha() for c in norm)


@dataclass
class PlateRead:
    track_id: int
    raw_text: str
    norm_text: str
    confidence: float
    best_frame: int
    votes: int
    best_bbox: tuple[float, float, float, float] | None = None


def extract_plate_reads(
    track_bboxes: dict[int, tuple[int, int, int, int]],
    images_by_frame: dict[int, np.ndarray],
    track_id: int,
    config: Config,
    ocr_fn: _OCR_FN | None = None,
) -> PlateRead | None:
    ocr_fn = ocr_fn or (
        lambda img: vision_ocr(img, level=LEVEL_ACCURATE, languages=["ja-JP", "en-US"])
    )
    groups: dict[str, list[tuple[str, float, int, tuple[float, float, float, float] | None]]] = (
        defaultdict(list)
    )
    for frame_number in sorted(track_bboxes):
        image = images_by_frame.get(frame_number)
        if image is None:
            continue
        crop = crop_vehicle(image, track_bboxes[frame_number])
        up = upscale_crop(crop)
        for r in ocr_fn(up):
            if r.confidence < config.prefilter.ocr_min_conf:
                continue
            if not plate_like(r.text):
                continue
            groups[normalize_plate(r.text)].append(
                (r.text, r.confidence, frame_number, r.bbox)
            )
    if not groups:
        return None
    winner_norm = max(
        groups, key=lambda n: (len(groups[n]), sum(c for _, c, _f, _b in groups[n]))
    )
    reads = groups[winner_norm]
    if len(reads) < config.prefilter.plate_min_votes:
        return None
    raw, conf, frame, bbox = max(reads, key=lambda r: r[1])
    return PlateRead(
        track_id=track_id,
        raw_text=raw,
        norm_text=winner_norm,
        confidence=conf,
        best_frame=frame,
        votes=len(reads),
        best_bbox=bbox,
    )
