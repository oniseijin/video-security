from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from video_security.adapters import ClipInfo
from video_security.config import Config, GsensConfig

MODES = {"NORMAL", "EVENT", "MANUAL", "PARKING", "PICTURE"}
GFORCE_EVENT_PRIORITY = {"hard_brake": 0.8, "hard_corner": 0.6, "impact": 0.9}
GFORCE_MERGE_GAP_SEC = 2.0

_FILENAME_RE = re.compile(r"^(\d{12})\.MP4$", re.IGNORECASE)


@dataclasses.dataclass
class GpsSample:
    time_sec: float
    lat: float | None = None
    lon: float | None = None
    speed_kmh: float | None = None
    bearing: float | None = None
    ax: float | None = None
    ay: float | None = None
    az: float | None = None


@dataclasses.dataclass
class GForceEvent:
    event_type: str
    start_sec: float
    end_sec: float
    peak_g: float


def parse_filename(filename: str, timezone: str = "Asia/Tokyo") -> datetime | None:
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        local = datetime.strptime(m.group(1), "%y%m%d%H%M%S").replace(
            tzinfo=ZoneInfo(timezone)
        )
    except ValueError:
        return None
    return local.astimezone(UTC)


def _find_dir_ci(parent: Path, name: str) -> Path | None:
    for child in parent.iterdir():
        if child.is_dir() and child.name.lower() == name.lower():
            return child
    return None


def _classify(path: Path, source: Path) -> tuple[str, str, Path] | None:
    parts = path.relative_to(source).parts
    mode_idx = -1
    mode = ""
    for i, part in enumerate(parts):
        if part.upper() in MODES:
            mode_idx = i
            mode = part.upper()
    if mode_idx < 0 or mode == "PICTURE":
        return None
    channel = "rear" if any(p.upper() == "REAR" for p in parts[:mode_idx]) else "front"
    card_root = source.joinpath(*parts[:mode_idx])
    return mode, channel, card_root


def _nmea_for(card_root: Path, mode: str, basename: str) -> Path | None:
    system = _find_dir_ci(card_root, "System")
    if system is None:
        return None
    nmea_dir = _find_dir_ci(system, "NMEA")
    if nmea_dir is None:
        return None
    mode_dir = _find_dir_ci(nmea_dir, mode)
    if mode_dir is None:
        return None
    cand = mode_dir / (basename + ".NMEA")
    return cand if cand.exists() else None


@dataclasses.dataclass
class MazdaCx8Adapter:
    config: Config
    name: str = "mazda_cx8"

    def discover_clips(self, source: Path) -> list[ClipInfo]:
        from video_security.adapters.generic import find_videos

        tz = self.config.adapter_mazda_cx8.timezone
        priority = self.config.adapter_mazda_cx8.priority
        clips: list[ClipInfo] = []
        for p in find_videos(source):
            classified = _classify(p, source)
            if classified is None:
                continue
            mode, channel, card_root = classified
            stem = p.stem
            start_utc = parse_filename(p.name, tz)
            pair: Path | None = None
            nmea: Path | None = None
            if channel == "front":
                rear_root = _find_dir_ci(card_root, "REAR")
                if rear_root is not None:
                    rear_mode = _find_dir_ci(rear_root, mode)
                    if rear_mode is not None:
                        cand = rear_mode / p.name
                        if cand.exists():
                            pair = cand
                nmea = _nmea_for(card_root, mode, stem)
            clips.append(
                ClipInfo(
                    path=p,
                    mode=mode,
                    channel=channel,
                    priority=priority.get(mode, 0.5),
                    recording_start_utc=(
                        start_utc.isoformat() if start_utc else None
                    ),
                    pair_path=pair,
                    nmea_path=nmea,
                )
            )
        return clips


def _nmea_checksum(line: str) -> bool:
    if not line.startswith("$"):
        return False
    if "*" not in line:
        return True
    body, _, csum = line[1:].partition("*")
    try:
        expected = int(csum.strip()[:2], 16)
    except ValueError:
        return False
    actual = 0
    for ch in body:
        actual ^= ord(ch)
    return actual == expected


def _parse_gprmc(fields: list[str]) -> tuple[datetime, float, float, float, float] | None:
    if len(fields) < 10 or fields[2] != "A":
        return None
    try:
        t = datetime.strptime(fields[1].split(".")[0], "%H%M%S").replace(tzinfo=UTC)
        d = datetime.strptime(fields[9], "%d%m%y").replace(tzinfo=UTC)
        dt = datetime(
            d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=UTC
        )
        lat_deg = float(fields[3][:2]) + float(fields[3][2:]) / 60.0
        if fields[4] == "S":
            lat_deg = -lat_deg
        lon_deg = float(fields[5][:3]) + float(fields[5][3:]) / 60.0
        if fields[6] == "W":
            lon_deg = -lon_deg
        speed = float(fields[7]) * 1.852
        bearing = float(fields[8])
    except (ValueError, IndexError):
        return None
    return dt, lat_deg, lon_deg, speed, bearing


def parse_nmea(path: Path, clip_start_utc: datetime | None = None) -> list[GpsSample]:
    samples: list[GpsSample] = []
    pending_gSENS: list[tuple[float, float, float]] = []
    last_fix: datetime | None = None

    def flush_gsens(next_fix: datetime | None) -> None:
        nonlocal pending_gSENS
        if not pending_gSENS:
            return
        t0 = last_fix
        t1 = next_fix
        n = len(pending_gSENS)
        for i, (ax, ay, az) in enumerate(pending_gSENS):
            if t0 is not None and t1 is not None and t1 > t0:
                frac = (i + 1) / (n + 1)
                t = t0 + (t1 - t0) * frac
            elif t0 is not None:
                t = t0 + timedelta(seconds=0.25 * (i + 1))
            else:
                t = None
            if t is None:
                continue
            samples.append(_sample_for(t, ax, ay, az))
        pending_gSENS = []

    def _sample_for(t: datetime, ax: float, ay: float, az: float) -> GpsSample:
        if clip_start_utc is not None:
            rel = (t - clip_start_utc).total_seconds()
        elif epoch is not None:
            rel = (t - epoch).total_seconds()
        else:
            rel = 0.0
        return GpsSample(time_sec=rel, ax=ax, ay=ay, az=az)

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    epoch: datetime | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("$"):
            continue
        if not _nmea_checksum(line):
            continue
        fields = ("$" + line[1:].split("*")[0]).split(",")
        tag = fields[0]
        if tag == "$GPRMC":
            parsed = _parse_gprmc(fields)
            if parsed is None:
                continue
            dt, lat, lon, speed, bearing = parsed
            flush_gsens(dt)
            if epoch is None:
                epoch = dt
            rel = (
                (dt - clip_start_utc).total_seconds()
                if clip_start_utc is not None
                else (dt - epoch).total_seconds()
            )
            samples.append(
                GpsSample(
                    time_sec=rel, lat=lat, lon=lon, speed_kmh=speed, bearing=bearing
                )
            )
            last_fix = dt
        elif tag == "$GSENS":
            try:
                pending_gSENS.append(
                    (float(fields[1]), float(fields[2]), float(fields[3]))
                )
            except (ValueError, IndexError):
                continue
    flush_gsens(None)
    return sorted(samples, key=lambda s: s.time_sec)


def _axis_deviation(samples: list[GpsSample], attr: str) -> list[float]:
    vals = [v for v in (getattr(s, attr) for s in samples) if v is not None]
    if len(vals) < 10:
        return []
    base = median(vals)
    return [
        float(v - base) if v is not None else 0.0
        for v in (getattr(s, attr) for s in samples)
    ]


def gforce_events(
    samples: list[GpsSample], cfg: GsensConfig
) -> list[GForceEvent]:
    if len(samples) < 10:
        return []
    dev_x = _axis_deviation(samples, "ax")
    dev_y = _axis_deviation(samples, "ay")
    dev_z = _axis_deviation(samples, "az")
    if not dev_x:
        return []
    crossings: list[tuple[str, int, float]] = []
    for i in range(len(samples)):
        if dev_y[i] <= -cfg.hard_brake_g:
            crossings.append(("hard_brake", i, abs(dev_y[i])))
        if abs(dev_x[i]) >= cfg.hard_corner_g:
            crossings.append(("hard_corner", i, abs(dev_x[i])))
        if abs(dev_z[i]) >= cfg.impact_g:
            crossings.append(("impact", i, abs(dev_z[i])))
    by_type: dict[str, list[tuple[int, float]]] = {}
    for etype, i, mag in crossings:
        by_type.setdefault(etype, []).append((i, mag))
    events: list[GForceEvent] = []
    for etype, hits in by_type.items():
        hits.sort()
        group: list[tuple[int, float]] = []
        groups: list[list[tuple[int, float]]] = []
        prev_i = -1
        for i, mag in hits:
            gap = samples[i].time_sec - samples[prev_i].time_sec
            if prev_i >= 0 and gap > GFORCE_MERGE_GAP_SEC:
                groups.append(group)
                group = []
            group.append((i, mag))
            prev_i = i
        if group:
            groups.append(group)
        for g in groups:
            start = max(0.0, samples[g[0][0]].time_sec)
            end = max(start, samples[g[-1][0]].time_sec)
            peak = max(mag for _, mag in g)
            events.append(
                GForceEvent(
                    event_type=etype,
                    start_sec=start,
                    end_sec=end,
                    peak_g=peak,
                )
            )
    events.sort(key=lambda e: e.start_sec)
    return events
