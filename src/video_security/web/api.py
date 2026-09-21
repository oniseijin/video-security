from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from video_security import db as vsdb
from video_security.config import Config
from video_security.enrich import (
    clip_mode,
    event_category,
    event_description,
    gps_track,
    nearest_gps,
    scene_description,
)
from video_security.geo import cached_description
from video_security.jp_plates import ken_for_plate
from video_security.report import render_report_html
from video_security.report_theme import event_tone
from video_security.web.server import ApiError, Route

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

ACTIVE_STATUSES = ("extracting", "filtering", "triage", "detail")

_categories_cache: tuple[float, dict[str, Any]] | None = None


def health(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            pkg = version("video-security")
        except PackageNotFoundError:
            pkg = "dev"
    except Exception:
        pkg = "dev"
    return {"ok": True, "version": pkg, "db": "readonly"}


def app_config(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    return {"carto_api_key": cfg.map.carto_api_key}


def stats(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    by_status: dict[str, int] = {
        str(row["status"]): int(row["c"])
        for row in conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status")
    }
    events = int(conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"])
    plates = int(conn.execute("SELECT COUNT(*) AS c FROM plates").fetchone()["c"])
    plates_with_crops = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM plates WHERE crop_path IS NOT NULL"
        ).fetchone()["c"]
    )
    frames_kept = int(conn.execute("SELECT COUNT(*) AS c FROM frames").fetchone()["c"])
    transcripts = int(
        conn.execute("SELECT COUNT(*) AS c FROM transcript_segments").fetchone()["c"]
    )
    faces_events = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM events "
            "WHERE faces_json IS NOT NULL AND faces_json GLOB '*[0-9]*'"
        ).fetchone()["c"]
    )
    active: dict[str, Any] | None = None
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    row = conn.execute(
        "SELECT id, current_stage, current_frame, total_frames FROM jobs "
        f"WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
        ACTIVE_STATUSES,
    ).fetchone()
    if row is not None:
        active = {
            "id": int(row["id"]),
            "stage": row["current_stage"],
            "current_frame": int(row["current_frame"] or 0),
            "total_frames": (
                int(row["total_frames"]) if row["total_frames"] is not None else None
            ),
        }
    artifact = Path(cfg.storage.artifact_dir).expanduser()
    db_path = Path(cfg.storage.db_path).expanduser()
    storage: dict[str, Any] = {
        "artifact_dir": str(artifact),
        "free_gb": None,
        "total_gb": None,
        "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
    }
    if artifact.exists():
        from shutil import disk_usage

        usage = disk_usage(artifact)
        storage["free_gb"] = round(usage.free / 1e9, 1)
        storage["total_gb"] = round(usage.total / 1e9, 1)
    imports = [
        {
            "import_id": row["import_id"],
            "jobs": int(row["jobs"]),
            "done": int(row["done"] or 0),
            "first_at": row["first_at"],
            "last_at": row["last_at"],
        }
        for row in conn.execute(
            "SELECT import_id, COUNT(*) AS jobs, SUM(status = 'done') AS done, "
            "MIN(created_at) AS first_at, MAX(created_at) AS last_at "
            "FROM jobs WHERE import_id IS NOT NULL "
            "GROUP BY import_id ORDER BY first_at"
        )
    ]
    return {
        "jobs": {"total": sum(by_status.values()), "by_status": by_status},
        "events": events,
        "plates": plates,
        "plates_with_crops": plates_with_crops,
        "frames_kept": frames_kept,
        "transcript_segments": transcripts,
        "faces": faces_events,
        "active_job": active,
        "storage": storage,
        "imports": imports,
    }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: str | None) -> str | None:
    dt = _parse_utc(value)
    return dt.isoformat() if dt else None


def _artifact_dir(cfg: Config) -> Path:
    return Path(cfg.storage.artifact_dir).expanduser()


def _frames_url(path_str: str) -> str:
    p = Path(path_str)
    return f"/media/frames/{p.parent.name}/{p.name}"


def _plate_media_url(path_str: str | None) -> str | None:
    if not path_str:
        return None
    p = Path(path_str)
    return f"/media/plates/{p.parent.name}/{p.name}"


def _faces_list(evt: sqlite3.Row) -> list[list[list[float]]]:
    raw = evt["faces_json"]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[list[list[float]]] = []
    for group in parsed:
        boxes: list[list[float]] = []
        if isinstance(group, list):
            for box in group:
                if isinstance(box, list) and len(box) == 4:
                    boxes.append([float(v) for v in box])
        out.append(boxes)
    return out


def _session_paths(raw: str) -> list[str]:
    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out.append(entry["path"])
    return out


def pair_job_id(conn: sqlite3.Connection, job_id: int) -> int | None:
    row = conn.execute(
        "SELECT clips_json FROM sessions WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is not None:
        for path in _session_paths(row["clips_json"]):
            other = conn.execute(
                "SELECT id FROM jobs WHERE video_path = ?", (path,)
            ).fetchone()
            if other is not None and int(other["id"]) != job_id:
                return int(other["id"])
    me = conn.execute(
        "SELECT video_path, recording_start_utc FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if me is None or me["recording_start_utc"] is None:
        return None
    my_rear = "/rear/" in str(me["video_path"])
    for cand in conn.execute(
        "SELECT id, video_path FROM jobs WHERE recording_start_utc = ? AND id != ?",
        (me["recording_start_utc"], job_id),
    ):
        cand_rear = "/rear/" in str(cand["video_path"])
        if my_rear != cand_rear:
            return int(cand["id"])
    return None


def _pair_job_id(conn: sqlite3.Connection, job_id: int) -> int | None:
    return pair_job_id(conn, job_id)


def _plates_by_track(conn: sqlite3.Connection, job_id: int) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list[sqlite3.Row]] = {}
    for row in conn.execute("SELECT * FROM plates WHERE job_id = ?", (job_id,)):
        out.setdefault(int(row["track_id"]), []).append(row)
    return out


def _event_summary(
    conn: sqlite3.Connection,
    evt: sqlite3.Row,
    job: sqlite3.Row,
    track: list[sqlite3.Row],
    plates_by_track: dict[int, list[sqlite3.Row]],
) -> dict[str, Any]:
    mode = clip_mode(str(job["video_path"]))
    gps = nearest_gps(track, float(evt["start_sec"])) if track else None
    category = event_category(mode, gps)
    faces = _faces_list(evt)
    face_count = sum(len(f) for f in faces)
    plate_rows: list[sqlite3.Row] = []
    if evt["track_id"] is not None:
        plate_rows = plates_by_track.get(int(evt["track_id"]), [])
    plate_texts = [str(r["norm_text"] or "") for r in plate_rows]
    description = event_description(conn, evt)
    source = "llm"
    if not description:
        description = scene_description(evt, gps, category, face_count, plate_texts)
        source = "synthesized"
    recorded = _parse_utc(job["recording_start_utc"])
    recorded_at = None
    if recorded is not None:
        recorded_at = (recorded + timedelta(seconds=float(evt["start_sec"]))).isoformat()
    return {
        "event_id": int(evt["id"]),
        "job_id": int(evt["job_id"]),
        "event_type": str(evt["event_type"]),
        "category": category,
        "start_sec": float(evt["start_sec"]),
        "end_sec": float(evt["end_sec"]),
        "recorded_at": recorded_at,
        "priority": float(evt["priority"]),
        "detector_score": float(evt["detector_score"]),
        "status": str(evt["status"]),
        "tone": event_tone(str(evt["event_type"])),
        "description": description,
        "description_source": source,
        "face_count": face_count,
        "plate_norm": plate_texts[0] if plate_texts else None,
    }


def _limit_offset(params: dict[str, Any]) -> tuple[int, int]:
    limit = min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    offset = max(int(params.get("offset", 0)), 0)
    return limit, offset


def jobs_list(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    where: list[str] = []
    args: list[Any] = []
    if params.get("status"):
        where.append("j.status = ?")
        args.append(params["status"])
    if params.get("mode"):
        where.append("j.video_path LIKE ?")
        args.append(f"%/{params['mode']}/%")
    if params.get("channel"):
        where.append(
            "j.id IN (SELECT job_id FROM clips WHERE clip_id = 0 AND channel = ?)"
        )
        args.append(params["channel"])
    if params.get("import_id"):
        where.append("j.import_id = ?")
        args.append(params["import_id"])
    if params.get("recorded_from"):
        where.append("j.recording_start_utc >= ?")
        args.append(params["recorded_from"])
    if params.get("recorded_to"):
        where.append("j.recording_start_utc <= ?")
        args.append(params["recorded_to"])
    if params.get("q"):
        where.append("j.video_path LIKE ?")
        args.append(f"%{params['q']}%")
    if params.get("device"):
        where.append("json_extract(j.metadata_json, '$.device_kind') = ?")
        args.append(params["device"])
    sort = params.get("sort", "id")
    order_col = {
        "recorded": "j.recording_start_utc",
        "imported": "j.created_at",
        "id": "j.id",
    }.get(sort, "j.id")
    order_dir = "ASC" if str(params.get("order", "desc")).lower() == "asc" else "DESC"
    limit, offset = _limit_offset(params)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(
        conn.execute("SELECT COUNT(*) AS c FROM jobs j" + clause, args).fetchone()["c"]
    )
    rows = conn.execute(
        "SELECT j.id, j.status, j.video_path, j.recording_start_utc, j.created_at, "
        "j.import_id, "
        "(SELECT COUNT(*) FROM events e WHERE e.job_id = j.id) AS n_events, "
        "(SELECT COUNT(*) FROM plates p WHERE p.job_id = j.id) AS n_plates, "
        "(SELECT COUNT(*) FROM events e2 WHERE e2.job_id = j.id "
        " AND e2.faces_json GLOB '*[0-9]*') AS n_faces, "
        "EXISTS(SELECT 1 FROM clip_gps_data g WHERE g.job_id = j.id "
        " AND g.lat IS NOT NULL) AS has_gps, "
        "EXISTS(SELECT 1 FROM transcript_segments t WHERE t.job_id = j.id) "
        "AS has_transcript, "
        "(SELECT c.channel FROM clips c WHERE c.job_id = j.id AND c.clip_id = 0) "
        "AS channel, "
        "(SELECT c2.duration_sec FROM clips c2 WHERE c2.job_id = j.id "
        " AND c2.clip_id = 0) AS duration_sec "
        f"FROM jobs j{clause} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        recorded = _parse_utc(row["recording_start_utc"])
        imported = _parse_utc(str(row["created_at"]))
        archive = False
        if recorded is not None and imported is not None:
            archive = (imported - recorded).days > 1
        duration = row["duration_sec"]
        if duration is None:
            d = conn.execute(
                "SELECT MAX(timestamp_sec) AS m FROM frames WHERE job_id = ?",
                (row["id"],),
            ).fetchone()
            duration = d["m"] if d and d["m"] is not None else None
        items.append(
            {
                "id": int(row["id"]),
                "status": str(row["status"]),
                "mode": clip_mode(str(row["video_path"])),
                "channel": row["channel"],
                "recorded_at": _iso(row["recording_start_utc"]),
                "imported_at": _iso(str(row["created_at"])),
                "import_id": row["import_id"],
                "archive": archive,
                "duration_sec": float(duration) if duration is not None else None,
                "counts": {
                    "events": int(row["n_events"]),
                    "plates": int(row["n_plates"]),
                    "faces": int(row["n_faces"]),
                },
                "has_gps": bool(row["has_gps"]),
                "has_transcript": bool(row["has_transcript"]),
                "pair_job_id": _pair_job_id(conn, int(row["id"])),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def job_detail(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ApiError(404, "job not found")
    track = gps_track(conn, job_id)
    clips = [
        {
            "clip_id": int(r["clip_id"]),
            "filename": r["filename"],
            "channel": r["channel"],
            "recording_start_utc": r["recording_start_utc"],
            "duration_sec": r["duration_sec"],
            "lighting": r["lighting"],
            "has_audio": bool(r["has_audio"]),
        }
        for r in conn.execute(
            "SELECT clip_id, filename, channel, recording_start_utc, duration_sec, "
            "lighting, has_audio FROM clips WHERE job_id = ? ORDER BY clip_id",
            (job_id,),
        )
    ]
    event_types: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for evt in conn.execute(
        "SELECT event_type, status FROM events WHERE job_id = ?", (job_id,)
    ):
        event_types[str(evt["event_type"])] = (
            event_types.get(str(evt["event_type"]), 0) + 1
        )
        status_counts[str(evt["status"])] = status_counts.get(str(evt["status"]), 0) + 1
    counts = {
        "events": sum(event_types.values()),
        "plates": int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM plates WHERE job_id = ?", (job_id,)
            ).fetchone()["c"]
        ),
        "faces": int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE job_id = ? "
                "AND faces_json GLOB '*[0-9]*'",
                (job_id,),
            ).fetchone()["c"]
        ),
        "transcript_segments": int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM transcript_segments WHERE job_id = ?",
                (job_id,),
            ).fetchone()["c"]
        ),
        "frames_kept": int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM frames WHERE job_id = ?", (job_id,)
            ).fetchone()["c"]
        ),
    }
    channel = clips[0]["channel"] if clips else None
    pair = None
    pair_id = _pair_job_id(conn, job_id)
    if pair_id is not None:
        pair_channel = conn.execute(
            "SELECT channel FROM clips WHERE job_id = ? AND clip_id = 0", (pair_id,)
        ).fetchone()
        pair = {
            "job_id": pair_id,
            "channel": pair_channel["channel"] if pair_channel else None,
        }
    recorded = _parse_utc(job["recording_start_utc"])
    imported = _parse_utc(str(job["created_at"]))
    archive = False
    if recorded is not None and imported is not None:
        archive = (imported - recorded).days > 1
    duration = clips[0]["duration_sec"] if clips else None
    if duration is None:
        d = conn.execute(
            "SELECT MAX(timestamp_sec) AS m FROM frames WHERE job_id = ?", (job_id,)
        ).fetchone()
        duration = d["m"] if d and d["m"] is not None else None
    device: dict[str, Any] | None = None
    raw_meta = job["metadata_json"]
    if raw_meta:
        try:
            meta = json.loads(raw_meta)
            dk = meta.get("device_kind")
            if dk is not None:
                device = {
                    "kind": dk,
                    "make": meta.get("device_make"),
                    "model": meta.get("device_model"),
                }
        except (json.JSONDecodeError, TypeError):
            pass
    archived: dict[str, Any] | None = None
    arow = conn.execute(
        "SELECT archived_at, original_bytes, proxy_bytes FROM archived_originals "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if arow is not None:
        archived = {
            "archived_at": _iso(str(arow["archived_at"])),
            "original_bytes": int(arow["original_bytes"]),
            "proxy_bytes": (
                int(arow["proxy_bytes"]) if arow["proxy_bytes"] is not None else None
            ),
        }
    return {
        "id": job_id,
        "status": str(job["status"]),
        "video_path": str(job["video_path"]),
        "recorded_at": _iso(job["recording_start_utc"]),
        "imported_at": _iso(str(job["created_at"])),
        "import_id": job["import_id"],
        "archive": archive,
        "mode": clip_mode(str(job["video_path"])),
        "channel": channel,
        "duration_sec": float(duration) if duration is not None else None,
        "has_audio": bool(clips[0]["has_audio"]) if clips else True,
        "has_gps": bool(track),
        "has_transcript": counts["transcript_segments"] > 0,
        "pair": pair,
        "counts": counts,
        "event_types": event_types,
        "status_counts": status_counts,
        "clips": clips,
        "device": device,
        "archived": archived,
    }


def job_events(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ApiError(404, "job not found")
    track = gps_track(conn, job_id)
    plates_by_track = _plates_by_track(conn, job_id)
    where = ["job_id = ?"]
    args: list[Any] = [job_id]
    if params.get("type"):
        where.append("event_type = ?")
        args.append(params["type"])
    if params.get("status"):
        where.append("status = ?")
        args.append(params["status"])
    rows = conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY start_sec", args
    ).fetchall()
    items = []
    for evt in rows:
        summary = _event_summary(conn, evt, job, track, plates_by_track)
        if params.get("category") and summary["category"] != params["category"]:
            continue
        items.append(summary)
    return {"items": items}


def events_list(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    where: list[str] = []
    args: list[Any] = []
    join_jobs = False
    if params.get("type"):
        where.append("e.event_type = ?")
        args.append(params["type"])
    if params.get("status"):
        where.append("e.status = ?")
        args.append(params["status"])
    if params.get("job_id"):
        where.append("e.job_id = ?")
        args.append(int(params["job_id"]))
    if params.get("from"):
        join_jobs = True
        where.append("date(j.recording_start_utc) >= ?")
        args.append(params["from"])
    if params.get("to"):
        if not join_jobs:
            join_jobs = True
        where.append("date(j.recording_start_utc) <= ?")
        args.append(params["to"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    table = "events e JOIN jobs j ON j.id = e.job_id" if join_jobs else "events e"
    rows = conn.execute(
        f"SELECT e.* FROM {table}{clause} ORDER BY e.id DESC LIMIT 5000", args
    ).fetchall()
    job_cache: dict[int, sqlite3.Row] = {}
    track_cache: dict[int, list[sqlite3.Row]] = {}
    plates_cache: dict[int, dict[int, list[sqlite3.Row]]] = {}

    def job_of(jid: int) -> sqlite3.Row | None:
        if jid not in job_cache:
            job_cache[jid] = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (jid,)
            ).fetchone()
        return job_cache[jid]

    summaries: list[dict[str, Any]] = []
    for evt in rows:
        jid = int(evt["job_id"])
        job = job_of(jid)
        if job is None:
            continue
        if jid not in track_cache:
            track_cache[jid] = gps_track(conn, jid)
            plates_cache[jid] = _plates_by_track(conn, jid)
        summaries.append(
            _event_summary(conn, evt, job, track_cache[jid], plates_cache[jid])
        )
    if params.get("category"):
        summaries = [s for s in summaries if s["category"] == params["category"]]
    limit, offset = _limit_offset(params)
    total = len(summaries)
    return {
        "items": summaries[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def event_detail(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    event_id = int(params["id"])
    evt = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if evt is None:
        raise ApiError(404, "event not found")
    job_id = int(evt["job_id"])
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ApiError(404, "job not found")
    track = gps_track(conn, job_id)
    plates_by_track = _plates_by_track(conn, job_id)
    summary = _event_summary(conn, evt, job, track, plates_by_track)
    fps = vsdb.clip_fps(conn, job_id)
    faces = _faces_list(evt)
    try:
        kf_paths = json.loads(evt["keyframes_json"]) if evt["keyframes_json"] else []
    except (json.JSONDecodeError, TypeError):
        kf_paths = []
    if not isinstance(kf_paths, list):
        kf_paths = []
    keyframes: list[dict[str, Any]] = []
    artifact = _artifact_dir(cfg)
    for i, path_str in enumerate(kf_paths):
        p = Path(str(path_str))
        boxes = faces[i] if i < len(faces) else []
        entry: dict[str, Any] = {
            "url": _frames_url(str(p)),
            "raw_url": None,
            "faces": boxes,
            "face_crops": [],
        }
        raw = p.with_name(p.stem + "_raw.jpg")
        if raw.is_file():
            entry["raw_url"] = _frames_url(str(raw))
        for j in range(len(boxes)):
            crop = artifact / "faces" / str(job_id) / f"face_{event_id}_{i}_{j}.jpg"
            if crop.is_file():
                entry["face_crops"].append(f"/media/faces/{job_id}/{crop.name}")
        keyframes.append(entry)
    plate_items: list[dict[str, Any]] = []
    track_id = evt["track_id"]
    if track_id is not None:
        for pr in plates_by_track.get(int(track_id), []):
            ken = ken_for_plate(str(pr["norm_text"] or ""))
            read_at = None
            if pr["best_frame"] is not None and fps:
                read_at = float(pr["best_frame"]) / fps
            ev = vsdb.nearest_event_to_seconds(
                conn, job_id, int(pr["track_id"]), read_at if read_at is not None else 0.0
            )
            plate_items.append(
                {
                    "track_id": int(pr["track_id"]),
                    "norm_text": pr["norm_text"],
                    "raw_text": pr["raw_text"],
                    "confidence": pr["confidence"],
                    "ken": ken[0] if ken else None,
                    "ken_en": ken[1] if ken else None,
                    "crop_url": _plate_media_url(pr["crop_path"]),
                    "event_id": int(ev["id"]) if ev is not None else None,
                }
            )
    transcript_window = [
        {
            "start_time": float(r["start_time"]),
            "end_time": float(r["end_time"]),
            "text": r["text"],
            "language": r["language"],
        }
        for r in conn.execute(
            "SELECT start_time, end_time, text, language FROM transcript_segments "
            "WHERE job_id = ? AND start_time <= ? AND end_time >= ? ORDER BY start_time",
            (job_id, float(evt["end_sec"]), float(evt["start_sec"])),
        )
    ]
    track_obj: dict[str, Any] | None = None
    if track_id is not None:
        tr = conn.execute(
            "SELECT * FROM vehicle_tracks WHERE job_id = ? AND track_id = ?",
            (job_id, track_id),
        ).fetchone()
        if tr is not None:
            try:
                strip_paths = json.loads(tr["strip_json"]) if tr["strip_json"] else []
            except (json.JSONDecodeError, TypeError):
                strip_paths = []
            if not isinstance(strip_paths, list):
                strip_paths = []
            track_obj = {
                "track_id": int(tr["track_id"]),
                "first_sec": float(tr["first_frame"]) / fps if fps else None,
                "last_sec": float(tr["last_frame"]) / fps if fps else None,
                "weaving_score": tr["weaving_score"],
                "direction": tr["direction"],
                "strip": [_frames_url(str(sp)) for sp in strip_paths],
            }
    location: dict[str, Any] | None = None
    gps = nearest_gps(track, float(evt["start_sec"])) if track else None
    if gps is not None and gps["lat"] is not None:
        location = {
            "lat": float(gps["lat"]),
            "lon": float(gps["lon"]),
            "speed_kmh": gps["speed_kmh"],
            "label": cached_description(conn, float(gps["lat"]), float(gps["lon"])),
        }
    summary.update(
        {
            "clip_id": int(evt["clip_id"]),
            "track_id": track_id,
            "location": location,
            "keyframes": keyframes,
            "plates": plate_items,
            "transcript_window": transcript_window,
            "track": track_obj,
            "links": {"report": f"/jobs/{job_id}/report#event-{event_id}"},
        }
    )
    return summary


def categories(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    global _categories_cache
    now = time.monotonic()
    if _categories_cache is not None and now - _categories_cache[0] < 30.0:
        return _categories_cache[1]
    job_cache: dict[int, str | None] = {}
    track_cache: dict[int, list[sqlite3.Row]] = {}
    category_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for evt in conn.execute(
        "SELECT job_id, event_type, start_sec FROM events"
    ):
        jid = int(evt["job_id"])
        if jid not in job_cache:
            job = conn.execute(
                "SELECT video_path FROM jobs WHERE id = ?", (jid,)
            ).fetchone()
            job_cache[jid] = clip_mode(str(job["video_path"])) if job else None
            track_cache[jid] = gps_track(conn, jid)
        gps = nearest_gps(track_cache[jid], float(evt["start_sec"]))
        cat = event_category(job_cache[jid], gps)
        category_counts[cat] = category_counts.get(cat, 0) + 1
        etype = str(evt["event_type"])
        type_counts[etype] = type_counts.get(etype, 0) + 1
    payload = {"categories": category_counts, "types": type_counts}
    _categories_cache = (now, payload)
    return payload


def job_plates(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    fps = vsdb.clip_fps(conn, job_id)
    items: list[dict[str, Any]] = []
    for pr in conn.execute(
        "SELECT * FROM plates WHERE job_id = ? ORDER BY track_id", (job_id,)
    ):
        ken = ken_for_plate(str(pr["norm_text"] or ""))
        read_at = None
        if pr["best_frame"] is not None and fps:
            read_at = float(pr["best_frame"]) / fps
        ev = vsdb.nearest_event_to_seconds(
            conn, job_id, int(pr["track_id"]), read_at if read_at is not None else 0.0
        )
        items.append(
            {
                "track_id": int(pr["track_id"]),
                "norm_text": pr["norm_text"],
                "raw_text": pr["raw_text"],
                "confidence": pr["confidence"],
                "ken": ken[0] if ken else None,
                "ken_en": ken[1] if ken else None,
                "read_at_sec": read_at,
                "crop_url": _plate_media_url(pr["crop_path"]),
                "event_id": int(ev["id"]) if ev is not None else None,
            }
        )
    return {"items": items}


def plates_gallery(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    where = "p.norm_text IS NOT NULL"
    args: list[Any] = []
    if params.get("q"):
        where += " AND (p.norm_text LIKE ? OR p.raw_text LIKE ?)"
        args.extend([f"%{params['q']}%", f"%{params['q']}%"])
    limit, offset = _limit_offset(params)
    total = int(
        conn.execute(
            f"SELECT COUNT(DISTINCT p.norm_text) AS c FROM plates p WHERE {where}",
            args,
        ).fetchone()["c"]
    )
    rows = conn.execute(
        f"SELECT p.norm_text, COUNT(*) AS sightings, MAX(p.confidence) AS best_confidence, "
        "MIN(j.recording_start_utc) AS first_seen, MAX(j.recording_start_utc) AS last_seen "
        "FROM plates p JOIN jobs j ON j.id = p.job_id "
        f"WHERE {where} GROUP BY p.norm_text ORDER BY sightings DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        norm = str(row["norm_text"])
        crop = conn.execute(
            "SELECT crop_path FROM plates WHERE norm_text = ? AND crop_path IS NOT NULL "
            "LIMIT 1",
            (norm,),
        ).fetchone()
        job_rows = conn.execute(
            "SELECT DISTINCT job_id FROM plates WHERE norm_text = ?", (norm,)
        ).fetchall()
        items.append(
            {
                "norm_text": norm,
                "sightings": int(row["sightings"]),
                "best_confidence": row["best_confidence"],
                "best_crop_url": _plate_media_url(crop["crop_path"]) if crop else None,
                "jobs": [int(r["job_id"]) for r in job_rows],
                "first_seen": _iso(row["first_seen"]),
                "last_seen": _iso(row["last_seen"]),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def plate_detail(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    norm = str(params["norm_text"])
    rows = conn.execute(
        "SELECT p.*, j.recording_start_utc FROM plates p "
        "JOIN jobs j ON j.id = p.job_id WHERE p.norm_text = ? "
        "ORDER BY j.recording_start_utc",
        (norm,),
    ).fetchall()
    if not rows:
        raise ApiError(404, "plate not found")
    sightings: list[dict[str, Any]] = []
    fps_cache: dict[int, float] = {}
    for pr in rows:
        job_id = int(pr["job_id"])
        if job_id not in fps_cache:
            fps_cache[job_id] = vsdb.clip_fps(conn, job_id)
        fps = fps_cache[job_id]
        read_at = None
        if pr["best_frame"] is not None and fps:
            read_at = float(pr["best_frame"]) / fps
        ev = vsdb.nearest_event_to_seconds(
            conn, job_id, int(pr["track_id"]), read_at if read_at is not None else 0.0
        )
        sightings.append(
            {
                "job_id": job_id,
                "track_id": int(pr["track_id"]),
                "confidence": pr["confidence"],
                "read_at_sec": read_at,
                "recorded_at": _iso(pr["recording_start_utc"]),
                "crop_url": _plate_media_url(pr["crop_path"]),
                "event_id": int(ev["id"]) if ev is not None else None,
            }
        )
    ken = ken_for_plate(norm)
    return {
        "norm_text": norm,
        "ken": ken[0] if ken else None,
        "ken_en": ken[1] if ken else None,
        "sightings": sightings,
    }


def job_tracks(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    fps = vsdb.clip_fps(conn, job_id)
    items: list[dict[str, Any]] = []
    for tr in conn.execute(
        "SELECT * FROM vehicle_tracks WHERE job_id = ? "
        "AND class_id IN (0,2,3,5,7) ORDER BY track_id",
        (job_id,),
    ):
        evs = [
            int(e["id"])
            for e in conn.execute(
                "SELECT id FROM events WHERE job_id = ? AND track_id = ?",
                (job_id, tr["track_id"]),
            )
        ]
        pl = conn.execute(
            "SELECT norm_text FROM plates WHERE job_id = ? AND track_id = ?",
            (job_id, tr["track_id"]),
        ).fetchone()
        try:
            strip_paths = json.loads(tr["strip_json"]) if tr["strip_json"] else []
        except (json.JSONDecodeError, TypeError):
            strip_paths = []
        if not isinstance(strip_paths, list):
            strip_paths = []
        items.append(
            {
                "track_id": int(tr["track_id"]),
                "clip_id": int(tr["clip_id"]),
                "class_id": int(tr["class_id"]),
                "first_sec": float(tr["first_frame"]) / fps if fps else None,
                "last_sec": float(tr["last_frame"]) / fps if fps else None,
                "weaving_score": tr["weaving_score"],
                "direction": tr["direction"],
                "plate_norm": pl["norm_text"] if pl else None,
                "event_ids": evs,
                "strip": [_frames_url(str(sp)) for sp in strip_paths],
            }
        )
    return {"items": items}


def job_gps(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    track = [
        r
        for r in conn.execute(
            "SELECT time_sec, lat, lon, speed_kmh, bearing FROM clip_gps_data "
            "WHERE job_id = ? AND lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY time_sec",
            (job_id,),
        )
    ]
    events = conn.execute(
        "SELECT id, event_type, start_sec FROM events WHERE job_id = ? ORDER BY start_sec",
        (job_id,),
    ).fetchall()
    max_points = min(max(int(params.get("max_points", 2000)), 10), 20000)
    stride = max(1, len(track) // max_points)
    points: list[dict[str, Any]] = []
    for idx, r in enumerate(track):
        keep = idx % stride == 0 or idx == len(track) - 1
        if not keep:
            for e in events:
                if abs(float(e["start_sec"]) - float(r["time_sec"])) < 2.0:
                    keep = True
                    break
        if keep:
            points.append(
                {
                    "t": float(r["time_sec"]),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "speed": r["speed_kmh"],
                    "bearing": r["bearing"],
                }
            )
    markers: list[dict[str, Any]] = []
    for e in events:
        gps = nearest_gps(track, float(e["start_sec"]))
        if gps is None or gps["lat"] is None:
            continue
        markers.append(
            {
                "event_id": int(e["id"]),
                "lat": float(gps["lat"]),
                "lon": float(gps["lon"]),
                "type": str(e["event_type"]),
                "tone": event_tone(str(e["event_type"])),
                "time": float(e["start_sec"]),
            }
        )
    return {"points": points, "events": markers}


def job_transcript(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    items = [
        {
            "start_time": float(r["start_time"]),
            "end_time": float(r["end_time"]),
            "text": r["text"],
            "language": r["language"],
        }
        for r in conn.execute(
            "SELECT start_time, end_time, text, language FROM transcript_segments "
            "WHERE job_id = ? ORDER BY start_time",
            (job_id,),
        )
    ]
    return {"items": items}


def search(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    q = str(params.get("q", "")).strip()
    if not q:
        raise ApiError(400, "missing query")
    plates = [
        {
            "job_id": int(r["job_id"]),
            "track_id": int(r["track_id"]),
            "norm_text": r["norm_text"],
            "raw_text": r["raw_text"],
            "confidence": r["confidence"],
            "crop_url": _plate_media_url(r["crop_path"]),
        }
        for r in conn.execute(
            "SELECT job_id, track_id, norm_text, raw_text, confidence, crop_path "
            "FROM plates WHERE norm_text LIKE ? OR raw_text LIKE ? LIMIT 50",
            (f"%{q}%", f"%{q}%"),
        )
    ]
    text: list[dict[str, Any]] = []
    try:
        text = [
            {
                "job_id": int(r["job_id"]),
                "clip_id": int(r["clip_id"]),
                "frame_number": int(r["frame_number"]),
                "text": r["text"],
            }
            for r in vsdb.fts_match(
                conn,
                "SELECT f.job_id, f.clip_id, f.frame_number, ft.text "
                "FROM frame_text_fts ft JOIN frame_text f ON f.id = ft.rowid "
                "WHERE frame_text_fts MATCH ? ORDER BY rank LIMIT 50",
                q,
            )
        ]
    except sqlite3.OperationalError:
        raise ApiError(400, "invalid search query") from None
    transcripts = [
        {
            "job_id": int(r["job_id"]),
            "clip_id": int(r["clip_id"]),
            "start_time": float(r["start_time"]),
            "end_time": float(r["end_time"]),
            "text": r["text"],
        }
        for r in conn.execute(
            "SELECT job_id, clip_id, start_time, end_time, text FROM transcript_segments "
            "WHERE text LIKE ? LIMIT 50",
            (f"%{q}%",),
        )
    ]
    events = [
        {
            "job_id": int(r["job_id"]),
            "event_id": int(r["id"]),
            "event_type": str(r["event_type"]),
            "start_sec": float(r["start_sec"]),
        }
        for r in conn.execute(
            "SELECT id, job_id, event_type, start_sec FROM events "
            "WHERE event_type LIKE ? LIMIT 50",
            (f"%{q}%",),
        )
    ]
    semantic_available = False
    semantic: list[dict[str, Any]] = []
    semantic_transcripts: list[dict[str, Any]] = []
    try:
        from video_security.llm.embeddings import EMBED_MODEL, embed_query
        from video_security.llm.embeddings import (
            semantic_search as sem_search,
        )
        from video_security.llm.embeddings import (
            semantic_search_transcripts as sem_search_t,
        )
        from video_security.llm.ollama import OllamaClient

        client = OllamaClient(timeout_s=10)
        q_embed = embed_query(client, EMBED_MODEL, q)
        semantic = sem_search(conn, q_embed, top_k=10)
        semantic_transcripts = sem_search_t(conn, q_embed, top_k=10)
        semantic_available = True
    except Exception:
        pass
    return {
        "q": q,
        "plates": plates,
        "text": text,
        "transcripts": transcripts,
        "events": events,
        "semantic_available": semantic_available,
        "semantic": semantic,
        "semantic_transcripts": semantic_transcripts,
    }


def map_recent(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    limit = min(max(int(params.get("limit", 200)), 1), 1000)
    rows = conn.execute(
        "SELECT e.id, e.job_id, e.event_type, e.start_sec, j.recording_start_utc "
        "FROM events e JOIN jobs j ON j.id = e.job_id "
        "ORDER BY e.id DESC LIMIT ?",
        (limit * 3,),
    ).fetchall()
    track_cache: dict[int, list[sqlite3.Row]] = {}
    items: list[dict[str, Any]] = []
    for e in rows:
        if len(items) >= limit:
            break
        job_id = int(e["job_id"])
        if job_id not in track_cache:
            track_cache[job_id] = gps_track(conn, job_id)
        gps = nearest_gps(track_cache[job_id], float(e["start_sec"]))
        if gps is None or gps["lat"] is None:
            continue
        items.append(
            {
                "event_id": int(e["id"]),
                "job_id": job_id,
                "lat": float(gps["lat"]),
                "lon": float(gps["lon"]),
                "type": str(e["event_type"]),
                "tone": event_tone(str(e["event_type"])),
                "recorded_at": _iso(e["recording_start_utc"]),
                "label": cached_description(
                    conn, float(gps["lat"]), float(gps["lon"])
                ),
            }
        )
    return {"items": items}


def people_tracks(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    limit, offset = _limit_offset(params)
    where = ["vt.class_id = 0"]
    args: list[Any] = []
    if params.get("job_id"):
        where.append("vt.job_id = ?")
        args.append(int(params["job_id"]))
    rows = conn.execute(
        "SELECT vt.job_id, vt.track_id, vt.first_frame, vt.last_frame, "
        "vt.direction, vt.strip_json, j.recording_start_utc, "
        "(SELECT COUNT(*) FROM events e WHERE e.job_id = vt.job_id "
        " AND e.track_id = vt.track_id) AS n_events, "
        "(SELECT f.person_id FROM faces f JOIN events e2 ON e2.id = f.event_id "
        " WHERE e2.job_id = vt.job_id AND e2.track_id = vt.track_id "
        " AND f.person_id IS NOT NULL LIMIT 1) AS person_id "
        "FROM vehicle_tracks vt JOIN jobs j ON j.id = vt.job_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY j.recording_start_utc DESC, vt.track_id "
        "LIMIT ? OFFSET ?",
        [*args, 500, offset],
    ).fetchall()
    items = []
    for r in rows:
        strips = []
        try:
            strips = [
                p for p in json.loads(r["strip_json"] or "[]") if Path(p).is_file()
            ]
        except (json.JSONDecodeError, TypeError):
            strips = []
        strip_urls = []
        for sp in strips[:5]:
            m = re.match(r"^.*?/frames/(\d+)/(.+)$", str(sp))
            if m:
                strip_urls.append(f"/media/frames/{m.group(1)}/{m.group(2)}")
        items.append(
            {
                "job_id": int(r["job_id"]),
                "track_id": int(r["track_id"]),
                "recorded_at": _iso(r["recording_start_utc"]),
                "first_frame": int(r["first_frame"]),
                "last_frame": int(r["last_frame"]),
                "direction": r["direction"],
                "n_events": int(r["n_events"]),
                "person_id": (
                    int(r["person_id"]) if r["person_id"] is not None else None
                ),
                "strips": strip_urls,
            }
        )
    total = conn.execute(
        "SELECT COUNT(*) FROM vehicle_tracks WHERE class_id = 0"
    ).fetchone()[0]
    return {"items": items[:limit], "total": int(total), "limit": limit, "offset": offset}


def watchlists(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    from video_security.watchlist import list_watchlists

    return {"items": list_watchlists(conn)}


def watchlist_hits(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    limit, offset = _limit_offset(params)
    rows = conn.execute(
        "SELECT h.id, h.watchlist_id, h.job_id, h.event_id, h.detail, "
        "h.created_at, w.kind, w.pattern, w.note "
        "FROM watchlist_hits h JOIN watchlists w ON w.id = h.watchlist_id "
        "ORDER BY h.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "hit_id": int(r["id"]),
                "watchlist_id": int(r["watchlist_id"]),
                "kind": str(r["kind"]),
                "pattern": str(r["pattern"]),
                "note": r["note"],
                "job_id": int(r["job_id"]),
                "event_id": int(r["event_id"]) if r["event_id"] else None,
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
        )
    total = conn.execute("SELECT COUNT(*) FROM watchlist_hits").fetchone()[0]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def persons(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT p.id, p.sightings, "
        "(SELECT f.crop_path FROM faces f WHERE f.person_id = p.id "
        " ORDER BY f.quality DESC LIMIT 1) AS rep_crop, "
        "(SELECT MIN(j.recording_start_utc) FROM faces f "
        " JOIN events e ON e.id = f.event_id "
        " JOIN jobs j ON j.id = f.job_id WHERE f.person_id = p.id) AS first_seen, "
        "(SELECT MAX(j.recording_start_utc) FROM faces f "
        " JOIN events e ON e.id = f.event_id "
        " JOIN jobs j ON j.id = f.job_id WHERE f.person_id = p.id) AS last_seen "
        "FROM persons p WHERE p.sightings > 0 "
        "ORDER BY p.sightings DESC, p.id",
        (),
    ).fetchall()
    items = []
    for r in rows:
        rep = r["rep_crop"]
        rep_url = None
        if rep:
            p = Path(rep)
            if p.is_file():
                m = re.match(r"^.*?/faces/(\d+)/(.+)$", str(p))
                if m:
                    rep_url = f"/media/faces/{m.group(1)}/{m.group(2)}"
        items.append(
            {
                "person_id": int(r["id"]),
                "sightings": int(r["sightings"]),
                "representative_crop_url": rep_url,
                "first_seen": _iso(r["first_seen"]),
                "last_seen": _iso(r["last_seen"]),
            }
        )
    limit, offset = _limit_offset(params)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


def person_detail(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    person_id = int(params["id"])
    exists = conn.execute(
        "SELECT id FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    if exists is None:
        raise ApiError(404, "person not found")
    rows = conn.execute(
        "SELECT f.id, f.crop_path, f.quality, f.event_id, e.job_id, "
        "e.event_type, e.start_sec, e.track_id, j.recording_start_utc "
        "FROM faces f JOIN events e ON e.id = f.event_id "
        "JOIN jobs j ON j.id = f.job_id "
        "WHERE f.person_id = ? "
        "ORDER BY j.recording_start_utc DESC, f.id DESC",
        (person_id,),
    ).fetchall()
    sightings = []
    for r in rows:
        crop = r["crop_path"]
        url = None
        if crop and Path(crop).is_file():
            m = re.match(r"^.*?/faces/(\d+)/(.+)$", crop)
            if m:
                url = f"/media/faces/{m.group(1)}/{m.group(2)}"
        if url is None:
            continue
        track = conn.execute(
            "SELECT track_id, first_frame, last_frame, direction "
            "FROM vehicle_tracks WHERE job_id = ? AND track_id = ?",
            (int(r["job_id"]), r["track_id"]),
        ).fetchone() if r["track_id"] is not None else None
        sightings.append(
            {
                "job_id": int(r["job_id"]),
                "event_id": int(r["event_id"]),
                "event_type": str(r["event_type"]),
                "tone": event_tone(str(r["event_type"])),
                "recorded_at": _iso(r["recording_start_utc"]),
                "start_sec": float(r["start_sec"]),
                "quality": r["quality"],
                "crop_url": url,
                "track": (
                    {
                        "track_id": int(track["track_id"]),
                        "first_frame": int(track["first_frame"]),
                        "last_frame": int(track["last_frame"]),
                        "direction": track["direction"],
                    }
                    if track is not None
                    else None
                ),
            }
        )
    return {
        "person_id": person_id,
        "sightings": sightings,
        "total": len(sightings),
    }


def job_faces(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_id = int(params["id"])
    rows = conn.execute(
        "SELECT f.event_id, f.person_id, f.quality, f.crop_path, "
        "e.event_type, e.start_sec "
        "FROM faces f JOIN events e ON e.id = f.event_id "
        "WHERE f.job_id = ? "
        "ORDER BY (f.person_id IS NULL), f.person_id, f.quality DESC, f.id",
        (job_id,),
    ).fetchall()
    crops: list[dict[str, Any]] = []
    for r in rows:
        crop = r["crop_path"]
        url = None
        if crop and Path(crop).is_file():
            m = re.match(r"^.*?/faces/(\d+)/(.+)$", crop)
            if m:
                url = f"/media/faces/{m.group(1)}/{m.group(2)}"
        if url is None:
            continue
        crops.append(
            {
                "event_id": int(r["event_id"]),
                "event_type": str(r["event_type"]),
                "tone": event_tone(str(r["event_type"])),
                "start_sec": float(r["start_sec"]),
                "quality": r["quality"],
                "person_id": int(r["person_id"]) if r["person_id"] else None,
                "crop_url": url,
            }
        )
    groups: list[dict[str, Any]] = []
    index: dict[int | None, dict[str, Any]] = {}
    for crop in crops:
        pid = crop["person_id"]
        group = index.get(pid)
        if group is None:
            group = {"person_id": pid, "count": 0, "crops": []}
            index[pid] = group
            groups.append(group)
        group["count"] += 1
        group["crops"].append(crop)
    return {"job_id": job_id, "total": len(crops), "groups": groups}


def faces(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    where = [
        "e.faces_json IS NOT NULL",
        "e.faces_json GLOB '*[0-9]*'",
    ]
    args: list[Any] = []
    if params.get("job_id"):
        where.append("e.job_id = ?")
        args.append(int(params["job_id"]))
    rows = conn.execute(
        "SELECT e.id, e.job_id, e.event_type, e.start_sec, e.faces_json, "
        "e.keyframes_json, j.recording_start_utc "
        "FROM events e JOIN jobs j ON j.id = e.job_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY j.recording_start_utc DESC, e.id DESC LIMIT 5000",
        args,
    ).fetchall()
    artifact = _artifact_dir(cfg)
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            faces_data = json.loads(row["faces_json"])
            kf_paths = (
                json.loads(row["keyframes_json"]) if row["keyframes_json"] else []
            )
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(faces_data, list) or not isinstance(kf_paths, list):
            continue
        crops: list[str] = []
        person_ids: list[int | None] = []
        for i in range(min(len(faces_data), len(kf_paths))):
            boxes = faces_data[i] if isinstance(faces_data[i], list) else []
            for j in range(len(boxes)):
                crop = (
                    artifact
                    / "faces"
                    / str(int(row["job_id"]))
                    / f"face_{row['id']}_{i}_{j}.jpg"
                )
                if crop.is_file():
                    crops.append(f"/media/faces/{int(row['job_id'])}/{crop.name}")
                    frow = conn.execute(
                        "SELECT person_id FROM faces WHERE event_id = ? "
                        "AND keyframe_index = ? AND face_index = ?",
                        (int(row["id"]), i, j),
                    ).fetchone()
                    person_ids.append(
                        int(frow["person_id"]) if frow and frow["person_id"] else None
                    )
        if not crops:
            continue
        items.append(
            {
                "job_id": int(row["job_id"]),
                "event_id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "tone": event_tone(str(row["event_type"])),
                "recorded_at": _iso(row["recording_start_utc"]),
                "start_sec": float(row["start_sec"]),
                "crops": crops,
                "person_ids": person_ids,
            }
        )
    limit, offset = _limit_offset(params)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


def report_html(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> tuple[int, str, bytes]:
    job_id = int(params["id"])
    embed = params.get("embed") == "1"
    html_str = render_report_html(
        conn,
        job_id,
        _artifact_dir(cfg),
        geocode=True,
        geocode_network=False,
        media_base="/media",
        embed=embed,
        carto_api_key=cfg.map.carto_api_key,
    )
    return 200, "text/html; charset=utf-8", html_str.encode("utf-8")


def days(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    job_rows = conn.execute(
        "SELECT date(recording_start_utc) AS day, COUNT(*) AS cnt "
        "FROM jobs WHERE recording_start_utc IS NOT NULL "
        "GROUP BY day ORDER BY day"
    ).fetchall()
    analyzed_rows = conn.execute(
        "SELECT date(recording_start_utc) AS day, COUNT(*) AS cnt "
        "FROM jobs WHERE recording_start_utc IS NOT NULL "
        "AND status != 'pending' "
        "GROUP BY day ORDER BY day"
    ).fetchall()
    event_rows = conn.execute(
        "SELECT date(j.recording_start_utc) AS day, COUNT(*) AS cnt "
        "FROM events e JOIN jobs j ON j.id = e.job_id "
        "WHERE j.recording_start_utc IS NOT NULL "
        "GROUP BY day ORDER BY day"
    ).fetchall()
    jobs_map: dict[str, int] = {str(r["day"]): int(r["cnt"]) for r in job_rows}
    analyzed_map: dict[str, int] = {
        str(r["day"]): int(r["cnt"]) for r in analyzed_rows
    }
    events_map: dict[str, int] = {str(r["day"]): int(r["cnt"]) for r in event_rows}
    all_days = sorted(set(jobs_map.keys()) | set(events_map.keys()))
    return {
        "days": [
            {
                "date": d,
                "jobs": jobs_map.get(d, 0),
                "analyzed": analyzed_map.get(d, 0),
                "events": events_map.get(d, 0),
            }
            for d in all_days
        ]
    }


def analytics_heatmap(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT lat, lon FROM clip_gps_data WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    total = len(rows)
    max_points = 2000
    stride = max(1, total // max_points) if total > max_points else 1
    cells: dict[tuple[float, float], int] = {}
    for i, r in enumerate(rows):
        if total > max_points and i % stride != 0:
            continue
        k = (round(float(r["lat"]) * 1000) / 1000, round(float(r["lon"]) * 1000) / 1000)
        cells[k] = cells.get(k, 0) + 1
    max_count = max(cells.values()) if cells else 1
    return {
        "cells": [
            {"lat": k[0], "lon": k[1], "count": c, "weight": c / max_count}
            for k, c in cells.items()
        ]
    }


def analytics_hours(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT strftime('%H', j.recording_start_utc) AS hour, COUNT(*) AS count "
        "FROM events e JOIN jobs j ON j.id = e.job_id "
        "WHERE j.recording_start_utc IS NOT NULL "
        "GROUP BY hour ORDER BY hour"
    ).fetchall()
    return {
        "hours": [
            {"hour": int(r["hour"]), "count": int(r["count"])} for r in rows
        ]
    }


def analytics_plates(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT p.norm_text, p.raw_text, p.confidence, p.job_id, "
        "p.crop_path, j.recording_start_utc "
        "FROM plates p JOIN jobs j ON j.id = p.job_id "
        "ORDER BY p.norm_text, j.recording_start_utc"
    ).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        norm = str(r["norm_text"])
        g = groups.setdefault(
            norm,
            {
                "norm_text": norm,
                "raw_text": str(r["raw_text"]),
                "count": 0,
                "best_confidence": 0.0,
                "first_seen": None,
                "last_seen": None,
                "first_job": None,
                "last_job": None,
                "crop_url": None,
            },
        )
        g["count"] += 1
        if float(r["confidence"]) > float(g["best_confidence"]):
            g["best_confidence"] = float(r["confidence"])
            g["raw_text"] = str(r["raw_text"])
            if r["crop_path"]:
                cp = str(r["crop_path"])
                if Path(cp).is_file():
                    m = re.match(r"^.*?/plates/(\d+)/(.+)$", cp)
                    if m:
                        g["crop_url"] = f"/media/plates/{m.group(1)}/{m.group(2)}"
        rec = _iso(r["recording_start_utc"])
        if g["first_seen"] is None or (rec is not None and rec < g["first_seen"]):
            g["first_seen"] = rec
            g["first_job"] = int(r["job_id"])
        if g["last_seen"] is None or (rec is not None and rec >= g["last_seen"]):
            g["last_seen"] = rec
            g["last_job"] = int(r["job_id"])
    items = sorted(
        groups.values(), key=lambda g: g["count"], reverse=True
    )[:12]
    return {"items": items, "total": len(groups)}


def analytics_locations(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    from video_security.enrich import nearest_gps

    gps_all = conn.execute(
        "SELECT job_id, time_sec, lat, lon FROM clip_gps_data "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY job_id, time_sec"
    ).fetchall()
    gps_by_job: dict[int, list[sqlite3.Row]] = {}
    for r in gps_all:
        gps_by_job.setdefault(int(r["job_id"]), []).append(r)
    events = conn.execute(
        "SELECT job_id, start_sec FROM events ORDER BY job_id"
    ).fetchall()
    location_counts: dict[str, int] = {}
    for evt in events:
        job_id = int(evt["job_id"])
        gps_list = gps_by_job.get(job_id)
        if not gps_list:
            continue
        gps = nearest_gps(gps_list, float(evt["start_sec"]))
        if gps is None or gps["lat"] is None:
            continue
        desc = cached_description(conn, float(gps["lat"]), float(gps["lon"]))
        if desc:
            location_counts[desc] = location_counts.get(desc, 0) + 1
    top = sorted(location_counts.items(), key=lambda x: -x[1])[:10]
    return {"locations": [{"name": n, "count": c} for n, c in top]}


def api_routes() -> list[Route]:
    return [
        ("GET", "/api/health", health),
        ("GET", "/api/config", app_config),
        ("GET", "/api/stats", stats),
        ("GET", "/api/jobs", jobs_list),
        ("GET", "/api/jobs/{id:int}", job_detail),
        ("GET", "/api/jobs/{id:int}/events", job_events),
        ("GET", "/api/jobs/{id:int}/plates", job_plates),
        ("GET", "/api/jobs/{id:int}/tracks", job_tracks),
        ("GET", "/api/jobs/{id:int}/faces", job_faces),
        ("GET", "/api/jobs/{id:int}/gps", job_gps),
        ("GET", "/api/jobs/{id:int}/transcript", job_transcript),
        ("GET", "/api/jobs/{id:int}/report.html", report_html),
        ("GET", "/api/events", events_list),
        ("GET", "/api/events/{id:int}", event_detail),
        ("GET", "/api/categories", categories),
        ("GET", "/api/plates", plates_gallery),
        ("GET", "/api/plates/{norm_text}", plate_detail),
        ("GET", "/api/watchlists", watchlists),
        ("GET", "/api/watchlist-hits", watchlist_hits),
        ("GET", "/api/persons", persons),
        ("GET", "/api/persons/{id}", person_detail),
        ("GET", "/api/people/tracks", people_tracks),
        ("GET", "/api/faces", faces),
        ("GET", "/api/search", search),
        ("GET", "/api/map/recent", map_recent),
        ("GET", "/api/days", days),
        ("GET", "/api/analytics/heatmap", analytics_heatmap),
        ("GET", "/api/analytics/hours", analytics_hours),
        ("GET", "/api/analytics/locations", analytics_locations),
        ("GET", "/api/analytics/plates", analytics_plates),
    ]
