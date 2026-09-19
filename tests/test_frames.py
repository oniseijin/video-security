from __future__ import annotations

import dataclasses
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.media import OSD_FILTER, make_clip, make_concat_clip
from video_security.config import CameraOverrides, Config
from video_security.ingest.frames import (
    IngestError,
    dhash_hamming,
    iter_frames,
    probe_video,
    save_keyframe,
)


@pytest.fixture
def config() -> Config:
    return Config()


def test_static(config: Config, tmp_path: Path) -> None:
    path = make_clip(tmp_path / "static", "color=c=gray:s=320x240")
    frames = list(iter_frames(path, config))
    assert len(frames) == 1
    assert frames[0].keep_reason == "first"
    assert frames[0].lighting == "day"


def test_moving(config: Config, tmp_path: Path) -> None:
    path = make_clip(tmp_path / "moving", "testsrc2=s=320x240")
    frames = list(iter_frames(path, config))
    assert len(frames) >= 30
    motion_frames = [f for f in frames if f.keep_reason == "motion"]
    assert len(motion_frames) >= 30


def test_heartbeat(tmp_path: Path) -> None:
    cfg = dataclasses.replace(Config())
    cfg.engine.heartbeat_sec = 3
    path = make_clip(tmp_path / "heartbeat", "color=c=gray:s=320x240")
    frames = list(iter_frames(path, cfg))
    assert len(frames) == 4
    expected_times = [0.0, 3.0, 6.0, 9.0]
    for f, et in zip(frames, expected_times, strict=True):
        assert f.keep_reason in ("first", "heartbeat")
        assert abs(f.timestamp_sec - et) < 0.15


def test_scene_change(config: Config, tmp_path: Path) -> None:
    path = make_concat_clip(
        tmp_path / "scene",
        "color=c=blue:size=320x240:rate=10:duration=5",
        "testsrc2=s=320x240:rate=10:duration=5",
    )
    frames = list(iter_frames(path, config))
    reasons = {f.keep_reason for f in frames}
    assert "scene_change" in reasons


def test_osd_no_mask(tmp_path: Path) -> None:
    cfg = dataclasses.replace(Config())
    cfg.prefilter.decode_width = 320
    path = make_clip(tmp_path / "osd_nomask", "color=c=gray:s=320x240", vf=OSD_FILTER)
    frames = list(iter_frames(path, cfg))
    assert len(frames) >= 30


def test_osd_masked(tmp_path: Path) -> None:
    cfg = dataclasses.replace(Config())
    cfg.prefilter.decode_width = 320
    path = make_clip(tmp_path / "osd_mask", "color=c=gray:s=320x240", vf=OSD_FILTER)
    cam = CameraOverrides(osd_mask=[[0, 0, 320, 40]])
    frames = list(iter_frames(path, cfg, camera=cam))
    assert len(frames) < 30


def test_lighting_dark(config: Config, tmp_path: Path) -> None:
    path = make_clip(
        tmp_path / "dark",
        "testsrc2=s=320x240",
        vf="eq=brightness=-0.7",
    )
    frames = list(iter_frames(path, config))
    lightings = {f.lighting for f in frames}
    assert not lightings <= {"day"}


def test_probe_video(tmp_path: Path) -> None:
    path = make_clip(tmp_path / "probe1", "testsrc2=s=320x240")
    info = probe_video(path)
    assert info.width == 320
    assert info.height == 240
    assert info.fps == 10.0
    assert info.total_frames == 100
    assert not info.has_audio

    import subprocess as sp

    path2 = tmp_path / "probe_audio.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "testsrc2=s=320x240:d=10:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:d=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path2),
    ]
    sp.run(cmd, check=True, capture_output=True)
    info2 = probe_video(path2)
    assert info2.has_audio


def test_dhash_hamming_zero() -> None:
    x = 0xDEADBEEF
    assert dhash_hamming(x, x) == 0


def test_save_keyframe(tmp_path: Path) -> None:
    img = np.full((240, 320, 3), 128, dtype=np.uint8)
    out = tmp_path / "frames" / "kf.jpg"
    save_keyframe(img, out, max_width=100)
    loaded = cv2.imread(str(out))
    assert loaded is not None
    h2, w2 = loaded.shape[:2]
    assert w2 == 100


def test_garbage_file(config: Config, tmp_path: Path) -> None:
    p = tmp_path / "garbage.mp4"
    p.write_bytes(b"not a video")
    with pytest.raises(IngestError):
        list(iter_frames(p, config))