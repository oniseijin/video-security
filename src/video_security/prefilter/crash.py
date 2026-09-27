from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import cv2
import numpy as np

from video_security.config import CrashConfig
from video_security.enrich import clip_mode

CRASH_EVENT_TYPE = "crash"
CRASH_PRIORITY = 0.95
JOLT_SAMPLE_EVERY = 5
JOLT_DECODE_SIZE = (640, 480)
JOLT_MIN_SPIKE_PX = 2.0
JOLT_DECAY_RATIO = 0.5
JOLT_MAD_SCALE = 1.4826
SPEED_DROP_WINDOW_SEC = 2.0
BEARING_SNAP_DEG = 90.0
BEARING_SNAP_MIN_SPEED_KMH = 10.0
EVENT_PAD_SEC = 1.0


@dataclass
class JoltHit:
    time_sec: float
    spike_px: float
    baseline_px: float


@dataclass
class SpeedHit:
    time_sec: float
    drop_kmh: float
    rule: str


@dataclass
class GpsPoint:
    time_sec: float
    speed_kmh: float | None
    bearing: float | None


@dataclass
class CrashSignals:
    jolt: list[JoltHit] = field(default_factory=list)
    audio: list[tuple[float, float]] = field(default_factory=list)
    speed: list[SpeedHit] = field(default_factory=list)


@dataclass
class CrashEvent:
    start_sec: float
    end_sec: float
    signals: list[str]
    rule: str
    detector_score: float
    priority: float
    evidence: dict[str, object]


def jolt_samples(
    path: Path, sample_every: int = JOLT_SAMPLE_EVERY
) -> list[tuple[float, float]]:
    from video_security.ingest.frames import probe_video

    info = probe_video(path)
    if info.fps <= 0:
        return []
    width, height = JOLT_DECODE_SIZE
    frame_bytes = width * height
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-vf", f"scale={width}:{height},format=gray",
        "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    window = cv2.createHanningWindow((width, height), cv2.CV_32F)
    samples: list[tuple[float, float]] = []
    prev: np.ndarray | None = None
    idx = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            if idx % sample_every == 0:
                cur = (
                    np.frombuffer(raw, dtype=np.uint8)
                    .reshape((height, width))
                    .astype(np.float32)
                )
                if prev is not None:
                    (dx, dy), _response = cv2.phaseCorrelate(prev, cur, window)
                    mag = float(np.hypot(dx, dy))
                    if np.isfinite(mag):
                        samples.append((idx / info.fps, mag))
                prev = cur.copy()
            idx += 1
    finally:
        proc.stdout.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return samples


def jolt_hits_from_samples(
    samples: list[tuple[float, float]], jolt_sigma: float
) -> list[JoltHit]:
    if len(samples) < 3:
        return []
    disps = [d for _, d in samples]
    base = float(median(disps))
    mad = float(median([abs(d - base) for d in disps]))
    threshold = max(base + jolt_sigma * JOLT_MAD_SCALE * max(mad, 1e-6), JOLT_MIN_SPIKE_PX)
    ratio_floor = base * jolt_sigma
    hits: list[JoltHit] = []
    for i in range(1, len(samples) - 1):
        t, d = samples[i]
        if d < threshold or d < ratio_floor:
            continue
        if samples[i + 1][1] > d * JOLT_DECAY_RATIO:
            continue
        hits.append(JoltHit(time_sec=t, spike_px=d, baseline_px=base))
    return hits


def jolt_hits(
    path: Path, jolt_sigma: float, sample_every: int = JOLT_SAMPLE_EVERY
) -> list[JoltHit]:
    return jolt_hits_from_samples(jolt_samples(path, sample_every), jolt_sigma)


def speed_drop_hits(
    points: list[GpsPoint], speed_drop_kmh: float
) -> list[SpeedHit]:
    pts = sorted(
        (p for p in points if p.speed_kmh is not None), key=lambda p: p.time_sec
    )
    hits: list[SpeedHit] = []
    i = 0
    while i < len(pts):
        best: SpeedHit | None = None
        for j in range(i + 1, len(pts)):
            if pts[j].time_sec - pts[i].time_sec > SPEED_DROP_WINDOW_SEC:
                break
            drop = (pts[i].speed_kmh or 0.0) - (pts[j].speed_kmh or 0.0)
            if drop >= speed_drop_kmh and (best is None or drop > best.drop_kmh):
                best = SpeedHit(
                    time_sec=pts[j].time_sec, drop_kmh=drop, rule="speed_drop"
                )
        if best is not None:
            hits.append(best)
            i = next(
                (
                    k
                    for k in range(i + 1, len(pts))
                    if pts[k].time_sec > best.time_sec + SPEED_DROP_WINDOW_SEC
                ),
                len(pts),
            )
        else:
            i += 1
    moving: list[tuple[float, float]] = [
        (p.time_sec, p.bearing)
        for p in pts
        if (p.speed_kmh or 0.0) >= BEARING_SNAP_MIN_SPEED_KMH
        and p.bearing is not None
    ]
    for (t0, b0), (t1, b1) in zip(moving, moving[1:], strict=False):
        if t1 - t0 > SPEED_DROP_WINDOW_SEC:
            continue
        diff = abs((b1 - b0 + 180.0) % 360.0 - 180.0)
        if diff >= BEARING_SNAP_DEG:
            hits.append(SpeedHit(time_sec=t1, drop_kmh=0.0, rule="bearing_snap"))
    hits.sort(key=lambda h: h.time_sec)
    return hits


def fuse_crash(
    g_mode: bool,
    jolt: list[JoltHit],
    audio: list[tuple[float, float]],
    speed: list[SpeedHit],
    cfg: CrashConfig,
) -> list[CrashEvent]:
    items: list[tuple[float, str]] = []
    items.extend((h.time_sec, "J") for h in jolt)
    items.extend((start, "A") for start, _end in audio)
    items.extend((h.time_sec, "S") for h in speed)
    items.sort(key=lambda x: x[0])
    clusters: list[list[tuple[float, str]]] = []
    for item in items:
        if clusters and item[0] - clusters[-1][0][0] <= cfg.window_sec:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    min_signals = max(2, cfg.min_signals)
    events: list[CrashEvent] = []
    for cluster in clusters:
        present = {sig for _t, sig in cluster}
        if g_mode:
            present.add("G")
        if len(present) < min_signals:
            continue
        t0 = cluster[0][0]
        t1 = max(t for t, _sig in cluster)
        rule = "+".join(sorted(present))
        hits: dict[str, list[dict[str, object]]] = {}
        if g_mode:
            hits["G"] = [{"rule": "event_mode_clip"}]
        hits["J"] = [
            {
                "time_sec": h.time_sec,
                "spike_px": round(h.spike_px, 2),
                "baseline_px": round(h.baseline_px, 2),
            }
            for h in jolt
            if any(t == h.time_sec and sig == "J" for t, sig in cluster)
        ]
        hits["A"] = [
            {"start_sec": s, "end_sec": e}
            for s, e in audio
            if any(t == s and sig == "A" for t, sig in cluster)
        ]
        hits["S"] = [
            {
                "time_sec": h.time_sec,
                "drop_kmh": round(h.drop_kmh, 1),
                "rule": h.rule,
            }
            for h in speed
            if any(t == h.time_sec and sig == "S" for t, sig in cluster)
        ]
        event_start = max(0.0, t0 - EVENT_PAD_SEC)
        event_end = t1 + EVENT_PAD_SEC
        evidence: dict[str, object] = {
            "start_sec": event_start,
            "end_sec": event_end,
            "signals": sorted(present),
            "rule": rule,
            "detector_score": float(len(present)),
            "thresholds": {
                "jolt_sigma": cfg.jolt_sigma,
                "audio_sigma": cfg.audio_sigma,
                "speed_drop_kmh": cfg.speed_drop_kmh,
                "window_sec": cfg.window_sec,
                "min_signals": min_signals,
            },
            "hits": {k: v for k, v in hits.items() if v},
        }
        events.append(
            CrashEvent(
                start_sec=event_start,
                end_sec=event_end,
                signals=sorted(present),
                rule=rule,
                detector_score=float(len(present)),
                priority=CRASH_PRIORITY,
                evidence=evidence,
            )
        )
    return events


def job_channel(conn: sqlite3.Connection, job_id: int) -> str:
    row = conn.execute(
        "SELECT channel FROM clips WHERE job_id = ? AND clip_id = 0", (job_id,)
    ).fetchone()
    if row is not None and row["channel"]:
        return str(row["channel"])
    return "front"


def compute_crash_signals(
    path: Path,
    g_mode: bool,
    channel: str,
    gps: list[GpsPoint],
    audio: list[tuple[float, float]],
    cfg: CrashConfig,
) -> CrashSignals:
    jolt: list[JoltHit] = []
    if g_mode and channel != "rear":
        jolt = jolt_hits(path, cfg.jolt_sigma)
    speed = speed_drop_hits(gps, cfg.speed_drop_kmh)
    return CrashSignals(jolt=jolt, audio=audio, speed=speed)


def detect_crash_events(
    conn: sqlite3.Connection,
    job_id: int,
    video_path: str,
    config: CrashConfig,
    audio_transients: list[tuple[float, float]],
    gps_points: list[GpsPoint],
) -> list[CrashEvent]:
    if not config.enabled:
        return []
    g_mode = clip_mode(video_path) == "EVENT"
    channel = job_channel(conn, job_id)
    signals = compute_crash_signals(
        Path(video_path), g_mode, channel, gps_points, audio_transients, config
    )
    return fuse_crash(g_mode, signals.jolt, signals.audio, signals.speed, config)
