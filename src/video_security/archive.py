from __future__ import annotations

import dataclasses
import shutil
import sqlite3
import subprocess
from pathlib import Path

from video_security import db
from video_security.config import Config
from video_security.engine import EngineError


@dataclasses.dataclass
class ArchiveReport:
    archived: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_moved: int = 0
    bytes_saved: int = 0
    restored: int = 0
    deleted: int = 0
    planned: int = 0
    failures: list[str] = dataclasses.field(default_factory=list)


def select_jobs(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id FROM jobs WHERE status = 'done' "
        "AND created_at < datetime('now', ?) "
        "AND id NOT IN (SELECT job_id FROM archived_originals) "
        "ORDER BY id",
        (f'-{days} days',),
    ).fetchall()


def _duration(path: Path) -> float | None:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _proxy(src: Path, dst: Path, video_height: int) -> None:
    cmd: list[str] = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"scale=-2:{video_height}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-movflags", "+faststart",
        "-f", "mp4", "-y", str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:])


def _find_clips_root(src: Path) -> Path | None:
    for p in src.parents:
        if p.name == "clips":
            return p.parent
    return None


def archive_job(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_job_by_id(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not found")
        return

    if row.status != "done":
        report.failed += 1
        report.failures.append(f"job {job_id}: not done (status={row.status})")
        return

    if db.get_archived_original(conn, job_id) is not None:
        report.skipped += 1
        return

    src = Path(row.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: video not found at {src}")
        return

    proxy_tmp = src.with_name(src.name + ".vs-partial")
    assert config.archive.cold_dir is not None
    cold_root = Path(config.archive.cold_dir).expanduser()

    try:
        _proxy(src, proxy_tmp, config.archive.video_height)
        orig_dur = _duration(src)
        proxy_dur = _duration(proxy_tmp)
        if orig_dur is None or proxy_dur is None or abs(orig_dur - proxy_dur) > 1.0:
            proxy_tmp.unlink(missing_ok=True)
            report.failed += 1
            report.failures.append(
                f"job {job_id}: duration mismatch (orig={orig_dur}, proxy={proxy_dur})"
            )
            return

        clips_root = _find_clips_root(src)
        if clips_root is not None:
            cold_dest = cold_root / src.relative_to(clips_root)
        else:
            cold_dest = cold_root / src.name
        cold_dest.parent.mkdir(parents=True, exist_ok=True)

        original_bytes = src.stat().st_size
        shutil.move(str(src), str(cold_dest))

        proxy_bytes = proxy_tmp.stat().st_size
        proxy_tmp.rename(src)

        db.insert_archived_original(
            conn, job_id, str(cold_dest), original_bytes, proxy_bytes
        )
        report.archived += 1
        report.bytes_moved += original_bytes
        report.bytes_saved += max(0, original_bytes - proxy_bytes)
    except (OSError, RuntimeError, sqlite3.Error) as e:
        proxy_tmp.unlink(missing_ok=True)
        cold_dest = cold_root / (
            src.relative_to(clips_root) if (clips_root := _find_clips_root(src)) else src.name
        )
        if cold_dest.exists() and not src.exists():
            try:
                shutil.move(str(cold_dest), str(src))
            except OSError:
                pass
        report.failed += 1
        report.failures.append(f"job {job_id}: {e}")


def delete_job(
    conn: sqlite3.Connection,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_job_by_id(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not found")
        return
    if row.status != "done":
        report.failed += 1
        report.failures.append(f"job {job_id}: not done (status={row.status})")
        return
    if db.get_archived_original(conn, job_id) is not None:
        report.skipped += 1
        return
    src = Path(row.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: video not found at {src}")
        return
    try:
        original_bytes = src.stat().st_size
        src.unlink()
        db.insert_archived_original(
            conn, job_id, str(src), original_bytes, 0, location="deleted"
        )
        report.deleted += 1
        report.bytes_saved += original_bytes
    except (OSError, sqlite3.Error) as e:
        report.failed += 1
        report.failures.append(f"job {job_id}: {e}")


def restore_job(
    conn: sqlite3.Connection,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_archived_original(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not archived")
        return
    if row["location"] == "deleted":
        report.failed += 1
        report.failures.append(f"job {job_id}: original was deleted, not cold-stored")
        return

    job = db.get_job_by_id(conn, job_id)
    if job is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: job not found")
        return

    src = Path(job.video_path)
    cold = Path(row["original_path"])
    if not cold.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: archived original not found at {cold}")
        return

    if src.exists():
        src.unlink()
    shutil.move(str(cold), str(src))
    db.delete_archived_original(conn, job_id)
    report.restored += 1


def run_archive(
    conn: sqlite3.Connection,
    config: Config,
    days: int | None = None,
    job_ids: list[int] | None = None,
    dry_run: bool = False,
    restore: bool = False,
    delete: bool = False,
) -> ArchiveReport:
    report = ArchiveReport()

    if restore:
        if not job_ids:
            raise EngineError("--restore requires explicit --job")
        for jid in job_ids:
            restore_job(conn, jid, report)
        return report

    if job_ids:
        jobs_to_process = job_ids
    else:
        d = days if days is not None else config.archive.days
        jobs_to_process = [int(r["id"]) for r in select_jobs(conn, d)]

    if not dry_run and jobs_to_process and not delete:
        if config.archive.cold_dir is None:
            raise EngineError(
                "set [archive] cold_dir in config "
                "(cold storage target for archived originals)"
            )

    for jid in jobs_to_process:
        if dry_run:
            jrow = db.get_job_by_id(conn, jid)
            if jrow is None or jrow.status != "done":
                continue
            jpath = Path(jrow.video_path)
            if not jpath.exists():
                continue
            if db.get_archived_original(conn, jid) is not None:
                continue
            report.planned += 1
            report.bytes_moved += jpath.stat().st_size
        else:
            if delete:
                delete_job(conn, jid, report)
            else:
                archive_job(conn, config, jid, report)

    return report