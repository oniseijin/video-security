from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.golden import golden_clip
from video_security.config import Config
from video_security.ingest.frames import iter_frames
from video_security.prefilter.vehicles import load_detector, track_vehicles

pytestmark = pytest.mark.benchmark

DECODE_FPS_FLOOR = 15.0
YOLO_FPS_FLOOR = 4.0


def test_decode_throughput(tmp_path: Path) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    config = Config()
    start = time.monotonic()
    frames = list(iter_frames(clip, config))
    elapsed = time.monotonic() - start
    fps = 100.0 / elapsed
    print(f"\ndecode: {fps:.1f} fps (100-frame clip, {len(frames)} kept)")
    assert fps >= DECODE_FPS_FLOOR


def test_yolo_throughput(tmp_path: Path) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    config = Config()
    frames = list(iter_frames(clip, config))
    detector = load_detector(config)
    start = time.monotonic()
    for frame in frames:
        detector(frame.image)
    elapsed = time.monotonic() - start
    fps = len(frames) / elapsed
    print(f"\nyolo: {fps:.1f} fps on kept frames")
    assert fps >= YOLO_FPS_FLOOR


def test_full_prefilter_throughput(tmp_path: Path) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    config = Config()
    frames = list(iter_frames(clip, config))
    start = time.monotonic()
    _tracks, _dets = track_vehicles(frames, config)
    elapsed = time.monotonic() - start
    fps = len(frames) / elapsed
    print(f"\nfull prefilter (decode kept + yolo + track): {fps:.1f} fps")
    assert fps >= 2.0
