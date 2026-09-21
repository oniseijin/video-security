from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_security.config import Config
from video_security.prefilter.ocr import OCRResult, median_stack, sharpness_luma
from video_security.prefilter.plates import (
    PlateRead,
    _night_stack_read,
    extract_plate_reads,
)
from video_security.prefilter.vehicles import crop_vehicle


def _flat(w: int = 200, h: int = 150, val: int = 120) -> np.ndarray:
    return np.full((h, w, 3), val, dtype=np.uint8)


def _noisy(w: int = 200, h: int = 150, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _noisy_bright(w: int = 200, h: int = 150, seed: int = 7) -> np.ndarray:
    return np.clip(_noisy(w, h, seed).astype(np.int32) + 90, 0, 255).astype(np.uint8)


def _jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_sharpness_luma() -> None:
    s_flat, l_flat = sharpness_luma(_flat())
    s_noisy, l_noisy = sharpness_luma(_noisy())
    assert s_noisy > s_flat * 10
    assert 100 < l_flat < 140
    assert l_noisy > 100


def test_median_stack_aligns() -> None:
    a = _flat(val=10)
    b = _flat(val=250)
    c = _flat(val=60)
    stacked = median_stack([a, b, c])
    assert stacked.shape == a.shape
    assert 30 < int(stacked.mean()) < 90


def test_choose_keyframes_prefers_sharp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from video_security.pipeline import _choose_keyframe_numbers

    cache: OrderedDict[int, bytes] = OrderedDict()
    frames = [1, 2, 3, 4, 5]
    for fn in frames:
        img = _noisy(seed=fn) if fn in (2, 4) else _flat()
        cache[fn] = _jpeg(img)
    cache[9] = _jpeg(_flat())
    chosen = _choose_keyframe_numbers(frames, cache, 2)
    assert chosen == [2, 4]
    single = _choose_keyframe_numbers([9], cache, 2)
    assert single == [9]


def test_choose_keyframes_luma_penalty() -> None:
    from video_security.pipeline import _choose_keyframe_numbers

    cache: OrderedDict[int, bytes] = OrderedDict()
    cache[1] = _jpeg(_noisy(seed=1))
    cache[2] = _jpeg(_flat(val=10))
    chosen = _choose_keyframe_numbers([1, 2], cache, 1)
    assert chosen == [1]


def _bbox_full() -> tuple[int, int, int, int]:
    return (0, 0, 200, 150)


def test_extract_plate_reads_vote_weighting() -> None:
    calls: list[np.ndarray] = []

    def ocr(img: np.ndarray) -> list[OCRResult]:
        calls.append(img)
        mean = float(img.mean())
        if mean > 160:
            return [OCRResult(text="AAA111", confidence=0.9, bbox=(0.1, 0.1, 0.5, 0.2))]
        return [OCRResult(text="BBB222", confidence=0.9, bbox=(0.1, 0.1, 0.5, 0.2))]

    images = {
        1: _noisy_bright(seed=1),
        2: _noisy_bright(seed=2),
        3: _flat(val=60),
        4: _flat(val=60),
    }
    cfg = Config()
    read = extract_plate_reads(
        {1: _bbox_full(), 2: _bbox_full(), 3: _bbox_full(), 4: _bbox_full()},
        images,
        5,
        cfg,
        ocr_fn=ocr,
    )
    assert read is not None
    assert read.norm_text == "AAA111"
    assert read.votes == 2
    _ = calls


def test_night_stack_read_returns_stacked_ocr() -> None:
    cfg = Config()
    dark = {1: _flat(val=15), 2: _flat(val=25), 3: _flat(val=18)}
    reads: list[tuple[str, float, int, tuple[float, float, float, float] | None]] = [
        ("XX-11", 0.4, 1, (0.1, 0.1, 0.4, 0.2)),
        ("XX-11", 0.4, 2, (0.1, 0.1, 0.4, 0.2)),
        ("XX-11", 0.4, 3, (0.1, 0.1, 0.4, 0.2)),
    ]

    def ocr(img: np.ndarray) -> list[OCRResult]:
        assert img.shape[0] >= 300
        return [OCRResult(text="XX11", confidence=0.95, bbox=(0.2, 0.2, 0.5, 0.2))]

    out = _night_stack_read(reads, dark, ocr, cfg)
    assert out is not None
    assert out[0] == "XX11"
    assert out[1] == 0.95


def test_night_stack_read_skips_day() -> None:
    cfg = Config()
    bright = {1: _flat(val=200), 2: _flat(val=210)}
    reads: list[tuple[str, float, int, tuple[float, float, float, float] | None]] = [
        ("YY-22", 0.5, 1, None),
        ("YY-22", 0.5, 2, None),
    ]

    def ocr(img: np.ndarray) -> list[OCRResult]:
        raise AssertionError("ocr must not run for day frames")

    assert _night_stack_read(reads, bright, ocr, cfg) is None


def test_crop_vehicle_roi() -> None:
    img = _noisy(seed=3)
    crop = crop_vehicle(img, (10, 10, 60, 40))
    assert 0 < crop.shape[0] <= 45
    assert 0 < crop.shape[1] <= 70
    _ = Path
    _ = PlateRead
