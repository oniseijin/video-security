from __future__ import annotations

import sys
from collections.abc import Callable

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from video_security.config import Config
from video_security.ingest.frames import FrameData
from video_security.prefilter.ocr import (
    LEVEL_ACCURATE,
    OCRResult,
    upscale_crop,
    vision_ocr,
)
from video_security.prefilter.plates import (
    extract_plate_reads,
    normalize_plate,
    plate_crop_rect,
    plate_like,
    plate_src_rect,
)
from video_security.prefilter.scenetext import (
    classify_text,
    sample_scene_text,
)


def test_plate_crop_rect_jp_layout() -> None:
    # JP plate: text row anchored lower-right of the vehicle crop; the
    # prefecture/class rows live above-left of the OCR text bbox.
    x1, y1, x2, y2 = plate_crop_rect(
        (0.55, 0.15, 0.35, 0.25), 100, 50, [0.5, 0.6, 0.25, 0.2]
    )
    assert x1 == 37  # (0.55 - 0.5*0.35)*100
    assert y1 == 22  # top = 0.6; (0.6 - 0.6*0.25)*50
    assert x2 == 97  # (0.55 + 0.35*1.2)*100
    assert y2 == 45  # (0.6 + 0.25*1.25)*50


def test_plate_crop_rect_clamps_to_crop() -> None:
    x1, y1, x2, y2 = plate_crop_rect(
        (0.02, 0.75, 0.3, 0.2), 100, 50, [0.5, 0.6, 0.25, 0.2]
    )
    assert (x1, y1) == (0, 0)
    assert x2 == 38  # (0.02 + 0.3*1.2)*100
    assert y2 == 15  # top = 0.05; (0.05 + 0.2*1.25)*50


def test_plate_crop_rect_rejects_bad_pad() -> None:
    with pytest.raises(ValueError):
        plate_crop_rect((0.1, 0.1, 0.2, 0.2), 100, 50, [0.1, 0.2, 0.3])


def test_plate_src_rect_vehicle_window_and_y_origin() -> None:
    # 640x480 frame, track bbox (100,100)-(300,250): crop_vehicle adds 15%
    # margin → window (70, 78, 330, 272), vw=260, vh=194
    x1, y1, x2, y2 = plate_src_rect(
        (480, 640), (100, 100, 300, 250), (0.3, 0.2, 0.4, 0.2),
        [0.5, 0.6, 0.25, 0.2],
    )
    assert x1 == 96  # 70 + (0.3 - 0.5*0.4)*260
    assert y1 == 171  # 78 + (0.6 - 0.6*0.2)*194
    assert x2 == 272  # 70 + (0.3 + 0.4*1.2)*260
    assert y2 == 242  # 78 + (0.6 + 0.2*1.25)*194


def test_plate_src_rect_clamps_into_vehicle_window() -> None:
    x1, y1, x2, y2 = plate_src_rect(
        (480, 640), (100, 100, 300, 250), (0.0, 0.0, 1.0, 1.0),
        [0.5, 0.6, 0.25, 0.2],
    )
    assert (x1, y1, x2, y2) == (70, 78, 330, 272)


def test_plate_src_rect_rejects_bad_pad() -> None:
    with pytest.raises(ValueError):
        plate_src_rect((480, 640), (100, 100, 300, 250), (0.1, 0.1, 0.2, 0.2), [0.1])


def _frame(ts: float, number: int) -> FrameData:
    return FrameData(
        frame_number=number,
        timestamp_sec=ts,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=0.1,
        mog2_ratio=0.1,
        dhash=0,
        lighting="day",
        keep_reason="motion",
    )


def _text_image() -> np.ndarray:
    img = Image.new("RGB", (400, 100), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 48
        )
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 25), "YOLO42", fill=(255, 255, 255), font=font)
    return np.array(img)[:, :, ::-1].copy()


def test_vision_ocr_synthetic() -> None:
    if sys.platform != "darwin":
        pytest.skip("Vision macOS only")
    results = vision_ocr(_text_image(), level=LEVEL_ACCURATE)
    if not results:
        pytest.skip("Vision OCR unavailable")
    assert any("YOLO42" in r.text.replace(" ", "") for r in results)
    assert all(0.0 <= r.confidence <= 1.0 for r in results)


def test_vision_ocr_garbage() -> None:
    noise = np.random.default_rng(0).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    assert isinstance(vision_ocr(noise, level=LEVEL_ACCURATE), list)


def test_upscale_crop() -> None:
    small = np.zeros((100, 50, 3), dtype=np.uint8)
    up = upscale_crop(small)
    assert up.shape == (400, 200, 3)
    big = np.zeros((100, 300, 3), dtype=np.uint8)
    assert upscale_crop(big).shape == (200, 600, 3)


def test_normalize_plate() -> None:
    assert normalize_plate("ab-123 ") == "AB123"
    assert normalize_plate("よ 12") == "よ12"
    assert normalize_plate("x y 9") == "XY9"
    assert normalize_plate("習志野 れ 12-08") == "習志野れ1208"


def test_plate_like() -> None:
    assert plate_like("AB123")
    assert not plate_like("ABC")
    assert not plate_like("123")
    assert not plate_like("TOOLONGPLATE12345")
    assert plate_like("習志野れ12-08")
    assert not plate_like("習志野")
    assert plate_like("よ12")


def _sequencer_ocr(
    frames: list[int], reads_by_frame: dict[int, list[OCRResult]]
) -> Callable[[np.ndarray], list[OCRResult]]:
    state = {"idx": 0}

    def ocr_fn(image: np.ndarray) -> list[OCRResult]:
        if state["idx"] < len(frames):
            f = frames[state["idx"]]
            state["idx"] += 1
            return reads_by_frame.get(f, [])
        return []

    return ocr_fn


def test_extract_plate_reads_consensus() -> None:
    reads_by_frame = {
        0: [OCRResult("Y0L042", 0.4, (0, 0, 1, 1))],
        1: [OCRResult("YOLO42", 0.9, (0, 0, 1, 1))],
        2: [OCRResult("YOLO42", 0.8, (0, 0, 1, 1))],
        3: [OCRResult("ABC", 0.9, (0, 0, 1, 1))],
    }
    ocr_fn = _sequencer_ocr(sorted(reads_by_frame), reads_by_frame)
    bboxes = {f: (10, 10, 100, 100) for f in reads_by_frame}
    images = {f: np.zeros((240, 320, 3), dtype=np.uint8) for f in reads_by_frame}
    result = extract_plate_reads(bboxes, images, 7, Config(), ocr_fn=ocr_fn)
    assert result is not None
    assert result.norm_text == "YOLO42"
    assert result.votes == 2
    assert result.confidence == 0.9
    assert result.best_frame == 1
    assert result.track_id == 7
    assert result.raw_text == "YOLO42"


def test_extract_plate_reads_min_votes() -> None:
    single = {0: [OCRResult("YOLO42", 0.9, (0, 0, 1, 1))]}
    ocr_fn = _sequencer_ocr(list(single), single)
    result = extract_plate_reads(
        {0: (10, 10, 100, 100)},
        {0: np.zeros((240, 320, 3), dtype=np.uint8)},
        1,
        Config(),
        ocr_fn=ocr_fn,
    )
    assert result is None


def test_classify_text() -> None:
    assert classify_text("recorded 14:23:01") == "timestamp"
    assert classify_text("2024-01-15 cam") == "timestamp"
    assert classify_text("hello world") == "other"


def test_sample_scene_text() -> None:
    config = Config()
    frames = [_frame(t, i) for i, t in enumerate([0.0, 5.0, 35.0, 40.0, 60.0, 65.0])]
    ocr_results = [OCRResult("14:23:01", 0.9, (0, 0, 1, 1))]
    out = sample_scene_text(frames, config, ocr_fn=lambda img: list(ocr_results))
    assert len(out) == 3
    assert [s.timestamp_sec for s in out] == [0.0, 35.0, 60.0]
    assert all(s.text_kind == "timestamp" for s in out)
    assert out[0].frame_number == 0


def test_sample_scene_text_conf_filter() -> None:
    config = Config()
    frames = [_frame(0.0, 0)]
    ocr_results = [
        OCRResult("keep", 0.9, (0, 0, 1, 1)),
        OCRResult("drop", 0.1, (0, 0, 1, 1)),
    ]
    out = sample_scene_text(frames, config, ocr_fn=lambda img: list(ocr_results))
    assert [s.text for s in out] == ["keep"]
