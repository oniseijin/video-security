from __future__ import annotations

import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from shutil import disk_usage
from typing import Any

from video_security.config import Config
from video_security.web.server import Route

ACTIVE_STATUSES = ("extracting", "filtering", "triage", "detail")


def health(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        pkg = version("video-security")
    except PackageNotFoundError:
        pkg = "dev"
    return {"ok": True, "version": pkg, "db": "readonly"}


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
        "active_job": active,
        "storage": storage,
        "imports": imports,
    }


def api_routes() -> list[Route]:
    return [
        ("GET", "/api/health", health),
        ("GET", "/api/stats", stats),
    ]
