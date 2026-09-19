from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest

from tests.golden import GOLDEN_CAR_RECTS, golden_clip, scaled_rect
from video_security.config import Config
from video_security.ingest.frames import FrameData, iter_frames
from video_security.prefilter.plates import extract_plate_reads
from video_security.prefilter.scenetext import sample_scene_text


@pytest.fixture
def golden_frames(tmp_path: Path) -> Iterable[FrameData]:
    cfg = Config()
    clip = golden_clip(tmp_path / "g.mp4")
    return list(iter_frames(clip, cfg))


def _vehicle_bboxes_and_images(
    frames: list[FrameData], w: int, h: int
) -> tuple[dict[int, tuple[int, int, int, int]], dict[int, np.ndarray]]:
    bboxes: dict[int, tuple[int, int, int, int]] = {}
    images: dict[int, np.ndarray] = {}
    for f in frames:
        r = GOLDEN_CAR_RECTS[f.frame_number]
        if r[0] > 20 and r[2] < 620:
            bboxes[f.frame_number] = scaled_rect(r, w, h)
            images[f.frame_number] = f.image
    return bboxes, images


def test_golden_plate_read(golden_frames: list[FrameData]) -> None:
    if sys.platform != "darwin":
        pytest.skip("Vision macOS only")
    h, w = golden_frames[0].image.shape[:2]
    bboxes, images = _vehicle_bboxes_and_images(golden_frames, w, h)
    assert len(bboxes) >= 10
    result = extract_plate_reads(bboxes, images, 1, Config())
    assert result is not None
    assert result.norm_text == "YOLO42"
    assert result.votes >= 2


def test_golden_scene_text(golden_frames: list[FrameData]) -> None:
    if sys.platform != "darwin":
        pytest.skip("Vision macOS only")
    texts = sample_scene_text(golden_frames, Config())
    assert isinstance(texts, list)
    assert len({s.timestamp_sec for s in texts}) <= 4
