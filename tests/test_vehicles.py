from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from video_security.config import CameraOverrides, Config
from video_security.ingest.frames import FrameData
from video_security.prefilter.vehicles import (
    VEHICLE_CLASSES,
    Detection,
    crop_vehicle,
    load_detector,
    track_vehicles,
)


def test_crop_vehicle() -> None:
    img = np.zeros((100, 200, 3), dtype=np.uint8)

    crop = crop_vehicle(img, (40, 50, 80, 120), margin=0.15)
    h, w = crop.shape[:2]
    assert h == 60
    assert w == 52

    crop2 = crop_vehicle(img, (0, 0, 10, 10), margin=0.15)
    assert crop2.shape[0] == 12
    assert crop2.shape[1] == 12


def test_track_vehicles_mock_detector() -> None:
    frame0 = FrameData(
        frame_number=0,
        timestamp_sec=0.0,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=0.1,
        mog2_ratio=0.1,
        dhash=0,
        lighting="day",
        keep_reason="motion",
    )
    frame0.image[0, 0, 0] = 0
    frame10 = FrameData(
        frame_number=10,
        timestamp_sec=1.0,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=0.1,
        mog2_ratio=0.1,
        dhash=0,
        lighting="day",
        keep_reason="motion",
    )
    frame10.image[0, 0, 0] = 10
    frame20 = FrameData(
        frame_number=20,
        timestamp_sec=2.0,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=0.1,
        mog2_ratio=0.1,
        dhash=0,
        lighting="day",
        keep_reason="motion",
    )
    frame20.image[0, 0, 0] = 20

    canned: dict[int, list[Detection]] = {
        0: [Detection(track_id=1, class_id=2, bbox=(10, 100, 60, 160), conf=0.9)],
        10: [
            Detection(track_id=1, class_id=2, bbox=(110, 100, 160, 160), conf=0.9),
            Detection(track_id=2, class_id=0, bbox=(200, 200, 230, 290), conf=0.8),
        ],
        20: [
            Detection(track_id=1, class_id=2, bbox=(110, 100, 160, 160), conf=0.9),
            Detection(track_id=2, class_id=0, bbox=(200, 200, 230, 290), conf=0.8),
        ],
    }

    def detector(img: np.ndarray) -> list[Detection]:
        return canned[int(img[0, 0, 0])]

    cfg = Config()
    tracks, frame_dets = track_vehicles(
        [frame0, frame10, frame20], cfg, detector=detector
    )

    assert len(tracks) == 2
    track1 = next(t for t in tracks if t.track_id == 1)
    assert track1.first_frame == 0
    assert track1.last_frame == 20
    assert track1.direction == "E"
    assert set(track1.bboxes.keys()) == {0, 10, 20}

    track2 = next(t for t in tracks if t.track_id == 2)
    assert track2.direction is None
    assert track2.weaving_score is None


def test_weaving_score() -> None:
    cfg = Config()
    frames = [
        FrameData(
            frame_number=i,
            timestamp_sec=i / 10.0,
            image=np.zeros((240, 320, 3), dtype=np.uint8),
            motion_score=0.1,
            mog2_ratio=0.1,
            dhash=0,
            lighting="day",
            keep_reason="motion",
        )
        for i in range(10)
    ]

    def detector(img: np.ndarray) -> list[Detection]:
        return [Detection(track_id=1, class_id=2, bbox=(25, 100, 75, 160), conf=0.9)]

    tracks, _ = track_vehicles(frames, cfg, detector=detector)
    assert len(tracks) == 1

    bboxes_alt: dict[int, tuple[int, int, int, int]] = {}
    for i in range(10):
        cx = 25 if i % 2 == 0 else 75
        bboxes_alt[i] = (cx - 25, 100, cx + 25, 160)

    bboxes_lin: dict[int, tuple[int, int, int, int]] = {}
    for i in range(10):
        cx = 25 + i * 10
        bboxes_lin[i] = (cx - 25, 100, cx + 25, 160)

    def compute_weaving(
        bboxes: dict[int, tuple[int, int, int, int]],
    ) -> float:
        frame_indices = sorted(bboxes.keys())
        center_xs = [
            (bboxes[fi][0] + bboxes[fi][2]) / 2.0 for fi in frame_indices
        ]
        widths = [bboxes[fi][2] - bboxes[fi][0] for fi in frame_indices]
        mean_width = float(np.mean(widths))
        slope, intercept = np.polyfit(
            np.array(frame_indices, dtype=np.float64),
            np.array(center_xs, dtype=np.float64),
            1,
        )
        residuals = np.array(center_xs) - (
            slope * np.array(frame_indices) + intercept
        )
        return float(np.std(residuals) / mean_width)

    assert compute_weaving(bboxes_alt) > 0.05
    assert compute_weaving(bboxes_lin) < 0.05


def test_single_frame_track_dropped() -> None:
    frame = FrameData(
        frame_number=0,
        timestamp_sec=0.0,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=0.1,
        mog2_ratio=0.1,
        dhash=0,
        lighting="day",
        keep_reason="motion",
    )

    def detector(img: np.ndarray) -> list[Detection]:
        return [Detection(track_id=99, class_id=2, bbox=(10, 100, 60, 160), conf=0.9)]

    cfg = Config()
    tracks, _ = track_vehicles([frame], cfg, detector=detector)
    assert all(t.track_id != 99 for t in tracks)


def test_camera_class_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_model = Mock()
    result_mock = Mock()
    boxes_mock = Mock()
    boxes_mock.id = None
    result_mock.boxes = boxes_mock
    mock_model.track.return_value = [result_mock]
    monkeypatch.setattr(
        "video_security.prefilter.vehicles.YOLO", Mock(return_value=mock_model)
    )

    cfg = Config()
    camera = CameraOverrides(yolo_classes=[0])
    detector = load_detector(cfg, camera)
    detector(np.zeros((100, 100, 3), dtype=np.uint8))
    mock_model.track.assert_called_once()
    _, kwargs = mock_model.track.call_args
    assert kwargs["persist"] is True
    assert kwargs["classes"] == [0]
    assert kwargs["conf"] == 0.25
    assert kwargs["tracker"] == "bytetrack.yaml"
    assert kwargs["verbose"] is False


def test_real_yolo_golden(tmp_path: Path) -> None:
    from tests.golden import golden_clip
    from video_security.ingest.frames import iter_frames

    cfg = Config()
    clip = golden_clip(tmp_path / "g.mp4")
    try:
        frames = list(iter_frames(clip, cfg))
    except Exception as e:
        if isinstance(e, OSError):
            pytest.skip(f"Frame extraction failed: {e}")
        raise

    try:
        tracks, frame_dets = track_vehicles(frames, cfg)
    except Exception as e:
        pytest.skip(f"YOLO model download/load failed: {e}")
        return

    vehicle_tracks = [t for t in tracks if t.class_id in VEHICLE_CLASSES]
    assert vehicle_tracks
    assert any(t.class_id == 0 for t in tracks)
    assert frame_dets

    for t in vehicle_tracks:
        assert t.direction == "E"
        assert t.first_frame >= 0