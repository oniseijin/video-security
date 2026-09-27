from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from video_security import db
from video_security.archive import _resolve_cold_path
from video_security.config import Config
from video_security.ingest.audio import AudioError, audio_transients, extract_pcm
from video_security.prefilter.crash import (
    GpsPoint,
    JoltHit,
    fuse_crash,
    job_channel,
    jolt_hits,
    speed_drop_hits,
)

DASH_MODES = {"NORMAL", "EVENT", "MANUAL", "PARKING"}
_IMPORT_DATE_RE = re.compile(r"^\d{8}$")
_MAZDA_FILENAME_RE = re.compile(r"^\d{12}\.MP4$", re.IGNORECASE)


def is_mazda_event_path(video_path: str) -> bool:
    parts = Path(video_path).parts
    if len(parts) < 5:
        return False
    channel, mode, import_date = parts[-2], parts[-3], parts[-4]
    return (
        bool(_MAZDA_FILENAME_RE.match(parts[-1]))
        and channel in ("front", "rear")
        and mode in DASH_MODES
        and mode == "EVENT"
        and bool(_IMPORT_DATE_RE.match(import_date))
        and "clips" in parts[:-4]
    )


def crash_scan_candidates(
    conn: sqlite3.Connection,
    job_id: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT id, video_path, recording_start_utc FROM jobs WHERE status = 'done'"
    args: list[object] = []
    if job_id is not None:
        sql += " AND id = ?"
        args.append(job_id)
    sql += " ORDER BY id"
    rows = conn.execute(sql, args).fetchall()
    out = [r for r in rows if is_mazda_event_path(str(r["video_path"]))]
    if limit is not None:
        out = out[:limit]
    return out


def _decode_path(
    conn: sqlite3.Connection, config: Config, job_id: int, video_path: str
) -> tuple[Path | None, str]:
    p = Path(video_path)
    if p.exists():
        return p, ""
    row = db.get_archived_original(conn, job_id)
    if row is None:
        return None, "video missing"
    if row["location"] == "deleted":
        return None, "original deleted"
    roots = [Path(r).expanduser() for r in config.archive.cold_roots()]
    for col in ("proxy_cold_path", "original_path"):
        val = row[col]
        if not val:
            continue
        cand = _resolve_cold_path(str(val), roots)
        if cand.is_file():
            return cand, ""
    return None, "video missing (cold copy not found)"


def _gps_points(conn: sqlite3.Connection, job_id: int) -> list[GpsPoint]:
    rows = conn.execute(
        "SELECT time_sec, speed_kmh, bearing FROM clip_gps_data "
        "WHERE job_id = ? ORDER BY time_sec",
        (job_id,),
    ).fetchall()
    return [
        GpsPoint(
            float(r["time_sec"]),
            float(r["speed_kmh"]) if r["speed_kmh"] is not None else None,
            float(r["bearing"]) if r["bearing"] is not None else None,
        )
        for r in rows
    ]


@dataclass
class SignalReport:
    state: str
    detail: str = ""


@dataclass
class CrashScanResult:
    job_id: int
    recorded_at: str | None
    g: SignalReport
    j: SignalReport
    a: SignalReport
    s: SignalReport
    verdict: str
    rule: str = ""
    window: str = ""
    notes: list[str] = field(default_factory=list)


def scan_job(
    conn: sqlite3.Connection, config: Config, row: sqlite3.Row
) -> CrashScanResult:
    job_id = int(row["id"])
    video_path = str(row["video_path"])
    cfg = config.crash
    result = CrashScanResult(
        job_id=job_id,
        recorded_at=row["recording_start_utc"],
        g=SignalReport("hit", "EVENT-mode clip"),
        j=SignalReport("miss"),
        a=SignalReport("miss"),
        s=SignalReport("miss"),
        verdict="insufficient",
    )
    path, err = _decode_path(conn, config, job_id, video_path)
    if path is None:
        result.verdict = "unavailable"
        result.notes.append(err)
        return result

    channel = job_channel(conn, job_id)
    jolt: list[JoltHit] = []
    if channel == "rear":
        result.j = SignalReport("excluded", "excluded (rear channel)")
    else:
        try:
            jolt = jolt_hits(path, cfg.jolt_sigma)
            if jolt:
                result.j = SignalReport(
                    "hit",
                    "hit "
                    + ", ".join(
                        f"{h.spike_px:.1f}px vs base {h.baseline_px:.1f}px "
                        f"@{h.time_sec:.1f}s"
                        for h in jolt
                    ),
                )
        except Exception as e:
            result.j = SignalReport("n/a", f"decode failed: {e}")

    transients: list[tuple[float, float]] = []
    try:
        pcm = extract_pcm(path)
        transients = audio_transients(
            pcm, 16000, cfg.audio_sigma, config.audio.rms_floor
        )
        if transients:
            result.a = SignalReport(
                "hit",
                "hit " + ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in transients),
            )
    except AudioError:
        result.a = SignalReport("n/a", "no audio")

    gps = _gps_points(conn, job_id)
    speed = speed_drop_hits(gps, cfg.speed_drop_kmh) if gps else []
    if not gps:
        result.s = SignalReport("n/a", "no GPS rows")
    elif speed:
        result.s = SignalReport(
            "hit",
            "hit "
            + ", ".join(
                f"{h.drop_kmh:.0f}km/h {h.rule} @{h.time_sec:.1f}s" for h in speed
            ),
        )

    events = fuse_crash(True, jolt, transients, speed, cfg)
    if events:
        result.verdict = "would-fire"
        result.rule = events[0].rule
        result.window = f"{events[0].start_sec:.1f}-{events[0].end_sec:.1f}s"
    return result


def run_crash_scan(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int | None = None,
    limit: int | None = None,
) -> str:
    candidates = crash_scan_candidates(conn, job_id=job_id, limit=limit)
    if not candidates:
        return "crash-scan: no EVENT-mode done jobs found"
    lines: list[str] = []
    fired = 0
    insufficient = 0
    unavailable = 0
    for row in candidates:
        res = scan_job(conn, config, row)
        rec = f" rec {res.recorded_at}" if res.recorded_at else ""
        lines.append(f"job {res.job_id}{rec}")
        lines.append(f"  G  {res.g.detail or res.g.state}")
        lines.append(f"  J  {res.j.detail or res.j.state}")
        lines.append(f"  A  {res.a.detail or res.a.state}")
        lines.append(f"  S  {res.s.detail or res.s.state}")
        for note in res.notes:
            lines.append(f"  note: {note}")
        verdict = res.verdict
        if verdict == "would-fire":
            fired += 1
            verdict = f"WOULD-FIRE rule {res.rule} window {res.window}"
        elif verdict == "unavailable":
            unavailable += 1
        else:
            insufficient += 1
        lines.append(f"  verdict: {verdict}")
    header = f"crash-scan: {len(candidates)} EVENT-mode done job(s) scanned"
    summary = (
        f"summary: {fired} would-fire, {insufficient} insufficient, "
        f"{unavailable} unavailable of {len(candidates)} scanned"
    )
    return "\n".join([header, *lines, summary])
