from __future__ import annotations

import hashlib
import io
import platform
import shutil
import signal
import sqlite3
import subprocess
from pathlib import Path

from video_security.config import Config
from video_security.db import JobRow, connect, delete_job_rows, init_db


class EngineError(Exception):
    pass


def video_hash(path: Path) -> str:
    file_size = path.stat().st_size
    sha = hashlib.sha256(str(file_size).encode())
    sha.update(b"\0")
    with open(path, "rb") as f:
        if file_size < 12 * 1024 * 1024:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
        else:
            f.seek(0)
            sha.update(f.read(4 * 1024 * 1024))
            f.seek(file_size // 2 - 2 * 1024 * 1024)
            sha.update(f.read(4 * 1024 * 1024))
            f.seek(-4 * 1024 * 1024, io.SEEK_END)
            sha.update(f.read(4 * 1024 * 1024))
    return sha.hexdigest()


def disk_free_gb(path: Path) -> float:
    existing = path
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    usage = shutil.disk_usage(str(existing) if str(existing) != "." else existing)
    return usage.free / (1024**3)


def caffeinate_wrap(cmd: list[str]) -> int:
    if shutil.which("caffeinate") and platform.system() == "Darwin":
        return subprocess.run(["caffeinate", "-s", "-i", *cmd]).returncode
    return subprocess.run(cmd).returncode


def on_ac_power() -> bool:
    if platform.system() != "Darwin":
        return True
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True
        )
        return "AC Power" in result.stdout or "No adapter found" not in result.stdout
    except FileNotFoundError:
        return True


class SignalGuard:
    def __init__(self) -> None:
        self.stop_requested: bool = False
        self._old_sigint: signal._HANDLER | int = signal.SIG_DFL
        self._old_sigterm: signal._HANDLER | int = signal.SIG_DFL

    def __enter__(self) -> SignalGuard:
        self._old_sigint = signal.signal(signal.SIGINT, self._handle)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle)
        return self

    def __exit__(self, *args: object) -> None:
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def _handle(self, signum: int, frame: object) -> None:
        if not self.stop_requested:
            self.stop_requested = True
            return
        raise KeyboardInterrupt


class BatchEngine:
    def __init__(self, config: Config, db_path: str | None = None) -> None:
        self.config = config
        self.db_path = db_path or config.storage.db_path

    def _connect(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        init_db(conn)
        return conn

    def claim_next_job(self) -> JobRow | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE jobs SET status = 'extracting', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            return JobRow(
                id=row["id"],
                video_path=row["video_path"],
                video_hash=row["video_hash"],
                status="extracting",
                total_frames=row["total_frames"],
                current_frame=row["current_frame"],
                current_stage=row["current_stage"],
                recording_start_utc=row["recording_start_utc"],
                import_id=row["import_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reclaim_stale_jobs(self, max_age_sec: int = 3600) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE jobs SET status = 'pending', updated_at = CURRENT_TIMESTAMP "
                "WHERE status IN ('extracting','filtering','triage','detail') "
                "AND updated_at < datetime('now', ?)",
                (f"-{max_age_sec} seconds",),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def preflight(self) -> None:
        home_gb = disk_free_gb(Path.home())
        min_gb = self.config.engine.disk_preflight_gb
        if home_gb < min_gb:
            raise EngineError(
                f"System disk free ({home_gb:.1f} GB) below preflight threshold "
                f"({min_gb} GB)"
            )
        artifact_dir = self.config.storage.artifact_dir
        if artifact_dir:
            art_path = Path(artifact_dir)
            art_path.mkdir(parents=True, exist_ok=True)
            art_gb = disk_free_gb(art_path)
            if art_gb < min_gb:
                raise EngineError(
                    f"Artifact disk free ({art_gb:.1f} GB) below preflight threshold "
                    f"({min_gb} GB)"
                )

    def check_disk_watermark(self) -> None:
        home_gb = disk_free_gb(Path.home())
        watermark = self.config.engine.disk_watermark_gb
        if home_gb < watermark:
            raise EngineError("disk watermark breached — checkpointing and aborting")

    def mark_failed(self, job_id: int, attempts_cap: int = 3) -> None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status, attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            new_attempts: int = int(row["attempts"]) + 1
            if new_attempts >= attempts_cap:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', attempts = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_attempts, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status = 'pending', attempts = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_attempts, job_id),
                )
            conn.commit()
        finally:
            conn.close()

    def prune_old_jobs(self, days: int | None = None) -> int:
        days_val = days if days is not None else self.config.engine.retention_days
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE created_at < datetime('now', ?)",
                (f"-{days_val} days",),
            ).fetchall()
            job_ids = [int(r["id"]) for r in rows]
            for job_id in job_ids:
                delete_job_rows(conn, job_id)
            if job_ids:
                placeholders = ",".join("?" * len(job_ids))
                conn.execute(
                    f"DELETE FROM jobs WHERE id IN ({placeholders})", job_ids
                )
            conn.commit()
            return len(job_ids)
        finally:
            conn.close()