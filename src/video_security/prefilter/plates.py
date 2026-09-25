from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from video_security.config import Config
from video_security.prefilter.ocr import (
    LEVEL_ACCURATE,
    OCRResult,
    median_stack,
    sharpness_luma,
    upscale_crop,
    vision_ocr,
)
from video_security.prefilter.vehicles import crop_vehicle

_OCR_FN = Callable[[np.ndarray], list[OCRResult]]


def normalize_plate(text: str) -> str:
    return re.sub(r"[^A-Z0-9\u3040-\u30ff\u4e00-\u9faf]", "", text.upper())


def plate_crop_rect(
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    pad: list[float],
) -> tuple[int, int, int, int]:
    """JP-aware plate crop rect in pixels.

    bbox is the OCR text bbox normalized to the vehicle crop, Vision
    bottom-left y origin. pad is [left, up, down, right] multipliers of
    bbox dims — JP plates stack prefecture/class rows above the text
    row, so up/left extend further than down/right.
    """
    if len(pad) != 4:
        raise ValueError("plate_crop_pad must be [left, up, down, right]")
    bx, by, bw, bh = bbox
    left, up, down, right = pad
    top = 1.0 - by - bh
    x1 = max(0.0, (bx - left * bw) * width)
    y1 = max(0.0, (top - up * bh) * height)
    x2 = min(float(width), (bx + bw * (1 + right)) * width)
    y2 = min(float(height), (top + bh * (1 + down)) * height)
    return int(x1), int(y1), int(x2), int(y2)


def plate_like(text: str) -> bool:
    norm = normalize_plate(text)
    if not 3 <= len(norm) <= 12:
        return False
    return any(c.isdigit() for c in norm) and any(c.isalpha() for c in norm)


def vehicle_window(
    frame_shape: tuple[int, ...],
    bbox: tuple[int, int, int, int],
    margin: float = 0.15,
) -> tuple[int, int, int, int]:
    """Full-frame pixel window crop_vehicle produces for a track bbox."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    mx = int(round(w * margin))
    my = int(round(h * margin))
    h_img, w_img = frame_shape[:2]
    return (
        max(0, x1 - mx),
        max(0, y1 - my),
        min(w_img, x2 + mx),
        min(h_img, y2 + my),
    )


def plate_src_rect(
    frame_shape: tuple[int, ...],
    track_bbox: tuple[int, int, int, int],
    best_bbox: tuple[float, float, float, float],
    pad: list[float],
) -> tuple[int, int, int, int]:
    """Padded plate rect in full-frame pixels, top-left origin.

    best_bbox is the OCR text bbox normalized to the vehicle crop (any
    uniform upscale), Vision bottom-left y origin.
    """
    if len(pad) != 4:
        raise ValueError("plate_crop_pad must be [left, up, down, right]")
    vx1, vy1, vx2, vy2 = vehicle_window(frame_shape, track_bbox)
    vw = vx2 - vx1
    vh = vy2 - vy1
    bx, by, bw, bh = best_bbox
    left, up, down, right = pad
    top = 1.0 - by - bh
    px1 = vx1 + max(0.0, bx - left * bw) * vw
    py1 = vy1 + max(0.0, top - up * bh) * vh
    px2 = vx1 + min(1.0, bx + bw * (1 + right)) * vw
    py2 = vy1 + min(1.0, top + bh * (1 + down)) * vh
    return int(px1), int(py1), int(px2), int(py2)


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
    sharpness: dict[int, float] = {}
    vehicle_crops: dict[int, np.ndarray] = {}
    for frame_number in sorted(track_bboxes):
        image = images_by_frame.get(frame_number)
        if image is None:
            continue
        crop = crop_vehicle(image, track_bboxes[frame_number])
        vehicle_crops[frame_number] = crop
        up = upscale_crop(crop)
        crop_sharp, _luma = sharpness_luma(up)
        sharpness[frame_number] = crop_sharp
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
    max_sharp = max(sharpness.values(), default=1.0)
    if max_sharp <= 0.0:
        max_sharp = 1.0

    def vote_weight(frame_number: int) -> float:
        return 0.5 + 0.5 * (sharpness.get(frame_number, 0.0) / max_sharp)

    winner_norm = max(
        groups,
        key=lambda n: (
            len(groups[n]),
            sum(c * vote_weight(f) for _, c, f, _b in groups[n]),
        ),
    )
    reads = groups[winner_norm]
    if len(reads) < config.prefilter.plate_min_votes:
        return None
    raw, conf, frame, bbox = max(reads, key=lambda r: r[1])
    stacked_read = _night_stack_read(
        reads, vehicle_crops, ocr_fn, config
    )
    if stacked_read is not None:
        raw, conf, bbox = stacked_read
    return PlateRead(
        track_id=track_id,
        raw_text=raw,
        norm_text=winner_norm,
        confidence=conf,
        best_frame=frame,
        votes=len(reads),
        best_bbox=bbox,
    )


def _night_stack_read(
    reads: list[tuple[str, float, int, tuple[float, float, float, float] | None]],
    vehicle_crops: dict[int, np.ndarray],
    ocr_fn: _OCR_FN,
    config: Config,
) -> tuple[str, float, tuple[float, float, float, float] | None] | None:
    night_luma = config.prefilter.night_luma_threshold
    frames = sorted({f for _r, _c, f, _b in reads if f in vehicle_crops})
    if not frames:
        return None
    reference = vehicle_crops[frames[0]]
    _sharp, ref_luma = sharpness_luma(upscale_crop(reference))
    if ref_luma >= night_luma:
        return None
    crops = [vehicle_crops[f] for f in frames]
    stacked = upscale_crop(median_stack(crops))
    best: tuple[str, float, tuple[float, float, float, float] | None] | None = None
    for r in ocr_fn(stacked):
        if r.confidence < config.prefilter.ocr_min_conf:
            continue
        if not plate_like(r.text):
            continue
        if best is None or r.confidence > best[1]:
            best = (r.text, r.confidence, r.bbox)
    return best
