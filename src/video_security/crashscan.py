from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from video_security import db
from video_security.archive import _resolve_cold_path
from video_security.config import Config, CrashConfig
from video_security.ingest.audio import (
    AudioError,
    AudioHit,
    audio_transients,
    extract_pcm,
)
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
CALIBRATE_DEFAULT_LIMIT = 50


def _is_mazda_clip_path(video_path: str, mode: str) -> bool:
    parts = Path(video_path).parts
    if len(parts) < 5:
        return False
    channel, clip_mode, import_date = parts[-2], parts[-3], parts[-4]
    return (
        bool(_MAZDA_FILENAME_RE.match(parts[-1]))
        and channel in ("front", "rear")
        and clip_mode in DASH_MODES
        and clip_mode == mode
        and bool(_IMPORT_DATE_RE.match(import_date))
        and "clips" in parts[:-4]
    )


def is_mazda_event_path(video_path: str) -> bool:
    return _is_mazda_clip_path(video_path, "EVENT")


def is_mazda_normal_path(video_path: str) -> bool:
    return _is_mazda_clip_path(video_path, "NORMAL")


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


def crash_calibrate_candidates(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    if limit is None:
        limit = CALIBRATE_DEFAULT_LIMIT
    rows = conn.execute(
        "SELECT id, video_path, recording_start_utc FROM jobs "
        "WHERE status = 'done' ORDER BY id"
    ).fetchall()
    out = [r for r in rows if is_mazda_normal_path(str(r["video_path"]))]
    if limit <= 0 or len(out) <= limit:
        return out
    step = -(-len(out) // limit)
    return out[::step]


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
        "SELECT time_sec, lat, lon, speed_kmh, bearing FROM clip_gps_data "
        "WHERE job_id = ? ORDER BY time_sec",
        (job_id,),
    ).fetchall()
    return [
        GpsPoint(
            float(r["time_sec"]),
            float(r["speed_kmh"]) if r["speed_kmh"] is not None else None,
            float(r["bearing"]) if r["bearing"] is not None else None,
            float(r["lat"]) if r["lat"] is not None else None,
            float(r["lon"]) if r["lon"] is not None else None,
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

    transients: list[AudioHit] = []
    try:
        pcm = extract_pcm(path)
        transients = audio_transients(
            pcm, 16000, cfg.audio_sigma, config.audio.rms_floor
        )
        if transients:
            result.a = SignalReport(
                "hit",
                "hit "
                + ", ".join(
                    f"{h.start_sec:.1f}-{h.end_sec:.1f}s {h.sigma_multiple:.1f}x"
                    for h in transients
                ),
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


@dataclass
class CalibrationResult:
    job_id: int
    recorded_at: str | None
    clip_hours: float
    a_base: int
    a_confirm: int
    max_sigma: float
    sigmas: list[float]
    s_by_rule: dict[str, int]
    co_occurrence: int
    note: str = ""


def calibrate_job(
    conn: sqlite3.Connection, config: Config, row: sqlite3.Row
) -> CalibrationResult:
    job_id = int(row["id"])
    cfg = config.crash
    result = CalibrationResult(
        job_id=job_id,
        recorded_at=row["recording_start_utc"],
        clip_hours=0.0,
        a_base=0,
        a_confirm=0,
        max_sigma=0.0,
        sigmas=[],
        s_by_rule={},
        co_occurrence=0,
    )
    path, err = _decode_path(conn, config, job_id, str(row["video_path"]))
    if path is None:
        result.note = err
        return result
    audio: list[AudioHit] = []
    try:
        pcm = extract_pcm(path)
        result.clip_hours = len(pcm) / 16000.0 / 3600.0
        audio = audio_transients(
            pcm, 16000, cfg.audio_sigma, config.audio.rms_floor
        )
        confirm = audio_transients(
            pcm, 16000, cfg.audio_confirm_sigma, config.audio.rms_floor
        )
        result.a_base = len(audio)
        result.a_confirm = len(confirm)
        result.sigmas = [h.sigma_multiple for h in audio]
        result.max_sigma = max(result.sigmas, default=0.0)
    except AudioError:
        result.note = "no audio"
    gps = _gps_points(conn, job_id)
    speed = speed_drop_hits(gps, cfg.speed_drop_kmh) if gps else []
    for hit in speed:
        result.s_by_rule[hit.rule] = result.s_by_rule.get(hit.rule, 0) + 1
    result.co_occurrence = len(fuse_crash(False, [], audio, speed, cfg))
    return result


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _calibration_summary(
    results: list[CalibrationResult], cfg: CrashConfig
) -> list[str]:
    total_hours = sum(r.clip_hours for r in results)
    a_base = [float(r.a_base) for r in results]
    a_confirm = [float(r.a_confirm) for r in results]
    s_counts = [float(sum(r.s_by_rule.values())) for r in results]
    co = [float(r.co_occurrence) for r in results]
    all_sigmas = [s for r in results for s in r.sigmas]
    p99_sigma = _percentile(all_sigmas, 0.99)
    peak_sigma = max(all_sigmas, default=0.0)

    def line(label: str, totals: list[float]) -> str:
        total = int(sum(totals))
        rate = f"{total / total_hours:.1f}/clip-hour" if total_hours > 0 else "n/a"
        return (
            f"  {label}: {total} ({rate}) "
            f"p50 {_percentile(totals, 0.5):.0f} "
            f"p90 {_percentile(totals, 0.9):.0f} "
            f"max {max(totals, default=0.0):.0f}"
        )

    return [
        "summary:",
        f"  clips: {len(results)}, total {total_hours:.2f} clip-hours",
        line("A-base", a_base),
        line("A-confirm", a_confirm),
        line("S", s_counts),
        line("A+S co-occurrence (no-G would-fire)", co),
        (
            f"  suggested thresholds: noise sigma p99 = {p99_sigma:.1f}, "
            f"peak = {peak_sigma:.1f} "
            f"(current audio_sigma = {cfg.audio_sigma:.1f}, "
            f"audio_confirm_sigma = {cfg.audio_confirm_sigma:.1f})"
        ),
    ]


def run_crash_calibration(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
) -> str:
    candidates = crash_calibrate_candidates(conn, limit=limit)
    if not candidates:
        return "crash-calibrate: no NORMAL-mode done jobs found"
    lines = [
        f"crash-calibrate: {len(candidates)} NORMAL-mode done job(s) sampled"
    ]
    results = [calibrate_job(conn, config, row) for row in candidates]
    for res in results:
        rec = f" rec {res.recorded_at}" if res.recorded_at else ""
        lines.append(f"job {res.job_id}{rec}")
        s_total = sum(res.s_by_rule.values())
        rules = (
            ", ".join(f"{k}={v}" for k, v in sorted(res.s_by_rule.items()))
            or "none"
        )
        lines.append(
            f"  A base {res.a_base} (max {res.max_sigma:.1f}x) | "
            f"A confirm {res.a_confirm} | S {s_total} ({rules})"
        )
        note = f" [{res.note}]" if res.note else ""
        lines.append(
            f"  A+S co-occurrence within window_sec: {res.co_occurrence}{note}"
        )
    lines.extend(_calibration_summary(results, config.crash))
    return "\n".join(lines)
