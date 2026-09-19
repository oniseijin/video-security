from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from video_security.config import Config
from video_security.ingest.frames import FrameData
from video_security.prefilter.threats import (
    detect_threat_events,
    in_after_hours,
    parse_after_hours,
)
from video_security.prefilter.vehicles import Detection, FrameDetections


def _frame(number: int, ts: float, motion: float = 0.05, lighting: str = "day") -> FrameData:
    return FrameData(
        frame_number=number,
        timestamp_sec=ts,
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        motion_score=motion,
        mog2_ratio=motion,
        dhash=0,
        lighting=lighting,
        keep_reason="motion",
    )


def _fd(number: int, ts: float, track_ids: list[int]) -> FrameDetections:
    return FrameDetections(
        frame_number=number,
        timestamp_sec=ts,
        detections=[
            Detection(track_id=t, class_id=0, bbox=(10, 10, 40, 90), conf=0.8)
            for t in track_ids
        ],
    )


def test_parse_after_hours() -> None:
    assert parse_after_hours(["22:00-06:00"]) == [(1320, 360)]
    assert parse_after_hours(["09:00-17:00"]) == [(540, 1020)]
    with pytest.raises(ValueError):
        parse_after_hours(["bad"])
    with pytest.raises(ValueError):
        parse_after_hours(["10:00-10:00"])


def test_in_after_hours_wrap() -> None:
    ranges = parse_after_hours(["22:00-06:00"])
    assert in_after_hours(23 * 60, ranges)
    assert in_after_hours(2 * 60, ranges)
    assert not in_after_hours(12 * 60, ranges)
    ranges = parse_after_hours(["09:00-17:00"])
    assert in_after_hours(9 * 60, ranges)
    assert not in_after_hours(18 * 60, ranges)


def test_no_person_no_event() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0), 1: _frame(1, 0.5)}
    fds = [
        FrameDetections(0, 0.0, [Detection(9, 2, (0, 0, 50, 50), 0.9)]),
        FrameDetections(1, 0.5, []),
    ]
    assert detect_threat_events(fds, frames, config) == []


def test_day_person_suspicious_behavior() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.05), 5: _frame(5, 2.5, motion=0.05)}
    fds = [_fd(0, 0.0, [1]), _fd(5, 2.5, [1])]
    events = detect_threat_events(fds, frames, config)
    assert len(events) == 1
    assert events[0].event_type == "suspicious_behavior"
    assert events[0].priority == 0.5
    assert events[0].track_id == 1
    assert events[0].frame_numbers == [0, 5]


def test_night_person_intrusion() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.05, lighting="night")}
    fds = [_fd(0, 0.0, [1])]
    events = detect_threat_events(fds, frames, config)
    assert len(events) == 1
    assert events[0].event_type == "intrusion"
    assert events[0].priority == 0.9


def test_after_hours_wall_clock() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.05)}
    fds = [_fd(0, 0.0, [1])]
    events = detect_threat_events(
        fds, frames, config, recording_start_local=datetime(2026, 1, 1, 23, 30, 0)
    )
    assert len(events) == 1
    assert events[0].event_type == "intrusion"


def test_daytime_wall_clock_not_intrusion() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.05)}
    fds = [_fd(0, 0.0, [1])]
    events = detect_threat_events(
        fds, frames, config, recording_start_local=datetime(2026, 1, 1, 12, 0, 0)
    )
    assert len(events) == 1
    assert events[0].event_type == "suspicious_behavior"


def test_low_motion_no_flag() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.0)}
    fds = [_fd(0, 0.0, [1])]
    score_needed = config.threat.person_weight
    if score_needed >= config.threat.score_threshold:
        pytest.skip("config makes quiet person flaggable")
    assert detect_threat_events(fds, frames, config) == []


def test_track_split_on_gap() -> None:
    config = Config()
    config.threat.merge_gap_sec = 5
    frames = {
        0: _frame(0, 0.0, motion=0.05),
        10: _frame(10, 100.0, motion=0.05),
    }
    fds = [_fd(0, 0.0, [1]), _fd(10, 100.0, [1])]
    events = detect_threat_events(fds, frames, config)
    assert len(events) == 2


def test_track_split_on_different_track() -> None:
    config = Config()
    frames = {0: _frame(0, 0.0, motion=0.05), 1: _frame(1, 0.5, motion=0.05)}
    fds = [_fd(0, 0.0, [1]), _fd(1, 0.5, [2])]
    events = detect_threat_events(fds, frames, config)
    assert len(events) == 2
    assert events[0].track_id == 1
    assert events[1].track_id == 2


def test_loitering_classification() -> None:
    config = Config()
    config.threat.loiter_min_sec = 60
    n = 25
    frames = {i: _frame(i, i * 3.0, motion=0.05) for i in range(n)}
    fds = [_fd(i, i * 3.0, [1]) for i in range(n)]
    events = detect_threat_events(fds, frames, config)
    assert len(events) == 1
    assert events[0].event_type == "loitering"
    assert events[0].priority == 0.6
    assert events[0].end_sec - events[0].start_sec >= 60


def test_camera_after_hours_override() -> None:
    from video_security.config import CameraOverrides

    config = Config()
    camera = CameraOverrides(after_hours=["00:00-23:59"])
    frames = {0: _frame(0, 0.0, motion=0.05)}
    fds = [_fd(0, 0.0, [1])]
    events = detect_threat_events(
        fds, frames, config, camera=camera, recording_start_local=datetime(2026, 1, 1, 12, 0, 0)
    )
    assert len(events) == 1
    assert events[0].event_type == "intrusion"
