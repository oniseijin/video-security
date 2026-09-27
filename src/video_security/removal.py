from __future__ import annotations

import sqlite3
from pathlib import Path

from video_security import db
from video_security.config import Config


def remove_job(
    conn: sqlite3.Connection, cfg: Config, job_id: int, reason: str
) -> tuple[bool, str]:
    job = conn.execute(
        "SELECT id, video_path, video_hash FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job is None:
        return False, f"Error: job {job_id} not found"
    artifact_dir = Path(cfg.storage.artifact_dir).expanduser()
    clip_path = Path(str(job["video_path"]))
    clip_deleted = False
    if clip_path.is_relative_to(artifact_dir):
        try:
            clip_path.unlink(missing_ok=True)
            clip_deleted = True
        except OSError:
            clip_deleted = False
    archived = db.get_archived_original(conn, job_id)
    cold_path = str(archived["original_path"]) if archived else None
    db.delete_job_rows(conn, job_id, cfg.storage.artifact_dir)
    db.delete_photos_imports_for_job(conn, job_id)
    conn.execute("DELETE FROM archived_originals WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    report_path = artifact_dir / "reports" / f"job_{job_id}.html"
    report_path.unlink(missing_ok=True)
    db.insert_removal(
        conn,
        job_id,
        str(job["video_hash"]),
        str(job["video_path"]),
        reason,
        cold_path=cold_path,
    )
    details = ["db rows + artifacts cleared"]
    details.append("clip deleted" if clip_deleted else "clip file kept (outside artifact dir)")
    if cold_path:
        details.append(f"cold original kept: {cold_path}")
    return True, f"removed job {job_id} ({reason}): " + ", ".join(details)
