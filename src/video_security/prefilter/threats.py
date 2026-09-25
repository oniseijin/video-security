from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from video_security.config import CameraOverrides, Config, ThreatConfig
from video_security.ingest.frames import FrameData
from video_security.prefilter.vehicles import FrameDetections

PERSON_CLASS = 0

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass
class ThreatEvent:
    event_type: str
    start_sec: float
    end_sec: float
    track_id: int | None
    detector_score: float
    priority: float
    frame_numbers: list[int] = field(default_factory=list)


def parse_after_hours(ranges: list[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for r in ranges:
        m = re.fullmatch(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", r.strip())
        if not m:
            raise ValueError(f"Invalid after-hours range: {r!r}")
        h1, m1 = m.group(1).split(":")
        h2, m2 = m.group(2).split(":")
        start = int(h1) * 60 + int(m1)
        end = int(h2) * 60 + int(m2)
        if start == end:
            raise ValueError(f"Empty after-hours range: {r!r}")
        out.append((start, end))
    return out


def in_after_hours(minute_of_day: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start < end:
            if start <= minute_of_day < end:
                return True
        elif start <= minute_of_day or minute_of_day < end:
            return True
    return False


def _frame_score(
    has_person: bool,
    motion_score: float,
    boosted: bool,
    threat: ThreatConfig,
) -> float:
    if not has_person:
        return 0.0
    score = threat.person_weight
    score += threat.motion_weight * min(1.0, motion_score * 10.0)
    if boosted:
        score += threat.time_weight
    return score


def _classify(
    duration: float,
    any_after_hours: bool,
    night: bool,
    threat: ThreatConfig,
) -> str:
    if any_after_hours or night:
        return "intrusion"
    if duration >= threat.loiter_min_sec:
        return "loitering"
    return "suspicious_behavior"


def _person_frame_counts(
    frame_dets: list[FrameDetections],
    conf_floor: float,
) -> dict[int | None, int]:
    counts: dict[int | None, int] = {}
    for fd in frame_dets:
        persons = [d for d in fd.detections if d.class_id == PERSON_CLASS]
        if not persons:
            continue
        if conf_floor > 0.0 and max(p.conf for p in persons) < conf_floor:
            continue
        for p in persons:
            counts[p.track_id] = counts.get(p.track_id, 0) + 1
    return counts


def detect_threat_events(
    frame_dets: list[FrameDetections],
    frames_by_number: dict[int, FrameData],
    config: Config,
    camera: CameraOverrides | None = None,
    recording_start_local: datetime | None = None,
) -> list[ThreatEvent]:
    camera = camera or CameraOverrides()
    threat = config.threat
    after_hours_cfg = camera.after_hours if camera.after_hours else threat.after_hours
    ranges = parse_after_hours(after_hours_cfg)

    counts = _person_frame_counts(frame_dets, threat.person_conf_floor)
    min_frames = max(0, threat.min_person_frames)

    flagged: list[tuple[int, float, int | None, float, bool, bool]] = []
    for fd in frame_dets:
        frame = frames_by_number.get(fd.frame_number)
        if frame is None:
            continue
        persons = [d for d in fd.detections if d.class_id == PERSON_CLASS]
        if not persons:
            continue
        minute_of_day = -1
        if recording_start_local is not None:
            local = recording_start_local + timedelta(seconds=frame.timestamp_sec)
            minute_of_day = local.hour * 60 + local.minute
        after = minute_of_day >= 0 and in_after_hours(minute_of_day, ranges)
        night = frame.lighting in ("night", "ir")
        score = _frame_score(True, frame.motion_score, after or night, threat)
        if score < threat.score_threshold:
            continue
        best = max(persons, key=lambda p: p.conf)
        if best.track_id is not None and counts.get(best.track_id, 0) < min_frames:
            continue
        flagged.append((fd.frame_number, frame.timestamp_sec, best.track_id, score, after, night))

    events: list[ThreatEvent] = []
    current: list[tuple[int, float, int | None, float, bool, bool]] = []
    for item in flagged:
        if not current:
            current = [item]
            continue
        last = current[-1]
        same_track = last[2] == item[2]
        gap_ok = item[1] - last[1] <= threat.merge_gap_sec
        if same_track and gap_ok:
            current.append(item)
        else:
            events.append(_build_event(current, threat))
            current = [item]
    if current:
        events.append(_build_event(current, threat))
    return events


def _build_event(
    items: list[tuple[int, float, int | None, float, bool, bool]],
    threat: ThreatConfig,
) -> ThreatEvent:
    start = items[0][1]
    end = items[-1][1]
    track_id = items[0][2]
    duration = end - start
    any_after = any(item[4] for item in items)
    any_night = any(item[5] for item in items)
    event_type = _classify(duration, any_after, any_night, threat)
    priority = threat.priority.get(event_type, 0.5)
    return ThreatEvent(
        event_type=event_type,
        start_sec=start,
        end_sec=end,
        track_id=track_id,
        detector_score=max(i[3] for i in items),
        priority=priority,
        frame_numbers=[i[0] for i in items],
    )
