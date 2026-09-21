from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.golden import golden_clip
from video_security import db
from video_security.archive import (
    ArchiveReport,
    _find_clips_root,
    archive_job,
    run_archive,
    select_jobs,
)
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.engine import EngineError


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    config.storage.db_path = str(tmp_path / "t.db")
    config.archive.cold_dir = str(tmp_path / "cold")
    (tmp_path / "cold").mkdir()
    return config


def _make_done_job(
    conn: sqlite3.Connection,
    video_path: Path,
    days_ago: int = 0,
    status: str = "done",
) -> int:
    j = db.create_job(conn, str(video_path), "fakehash")
    conn.execute(
        "UPDATE jobs SET status = ?, created_at = datetime('now', ?) WHERE id = ?",
        (status, f'-{days_ago} days' if days_ago > 0 else '0 days', j.id),
    )
    conn.commit()
    return j.id


def test_archive_job_proxies_and_colds(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)

    job_id = _make_done_job(db_conn, clip)
    report = ArchiveReport()
    archive_job(db_conn, cfg, job_id, report)

    assert report.archived == 1
    assert report.skipped == 0
    assert report.failed == 0
    assert report.bytes_moved > 0
    assert report.bytes_saved >= 0

    assert clip.exists()
    assert clip.is_file()
    proxy_dur = float(
        subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(clip),
            ],
            capture_output=True, text=True,
        ).stdout.strip()
    )
    assert proxy_dur > 0

    cold_dir = Path(cfg.archive.cold_dir)  # type: ignore[arg-type]
    expected_cold = cold_dir / "clips" / "20250923" / "NORMAL" / "front" / "test.mp4"
    assert expected_cold.exists()
    cold_dur = float(
        subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(expected_cold),
            ],
            capture_output=True, text=True,
        ).stdout.strip()
    )
    assert abs(cold_dur - proxy_dur) < 1.0

    archived_row = db.get_archived_original(db_conn, job_id)
    assert archived_row is not None
    assert archived_row["original_path"] == str(expected_cold)
    assert archived_row["original_bytes"] > 0
    assert archived_row["proxy_bytes"] > 0

    job = db.get_job_by_id(db_conn, job_id)
    assert job is not None
    assert job.video_path == str(clip)


def test_archive_idempotent(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)

    job_id = _make_done_job(db_conn, clip)
    report1 = ArchiveReport()
    archive_job(db_conn, cfg, job_id, report1)
    assert report1.archived == 1

    report2 = ArchiveReport()
    archive_job(db_conn, cfg, job_id, report2)
    assert report2.skipped == 1
    assert report2.archived == 0


def test_archive_requires_cold_dir(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    config = Config()
    config.storage.db_path = str(tmp_path / "t.db")
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)
    _make_done_job(db_conn, clip)
    config.archive.cold_dir = None

    with pytest.raises(EngineError, match="cold_dir"):
        run_archive(db_conn, config, job_ids=[1])


def test_archive_dry_run(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)

    job_id = _make_done_job(db_conn, clip)
    report = run_archive(db_conn, cfg, job_ids=[job_id], dry_run=True)

    assert report.planned == 1
    assert report.bytes_moved > 0
    assert report.archived == 0
    assert report.skipped == 0
    assert report.failed == 0
    assert db.get_archived_original(db_conn, job_id) is None

    cold_dir = Path(cfg.archive.cold_dir)  # type: ignore[arg-type]
    assert not any(cold_dir.iterdir())


def test_archive_selection_days(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip_old = clips_root / "old.mp4"
    clip_new = clips_root / "new.mp4"
    golden_clip(clip_old)
    golden_clip(clip_new)

    old_id = _make_done_job(db_conn, clip_old, days_ago=100)
    new_id = _make_done_job(db_conn, clip_new, days_ago=0)

    report = run_archive(db_conn, cfg, days=30)
    assert report.archived == 1
    assert report.skipped == 0
    assert db.get_archived_original(db_conn, old_id) is not None
    assert db.get_archived_original(db_conn, new_id) is None


def test_archive_skips_pending(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)
    pending_id = _make_done_job(db_conn, clip, days_ago=100, status="pending")
    done_id = _make_done_job(db_conn, clip, days_ago=100, status="done")

    report = run_archive(db_conn, cfg, days=30)
    assert report.archived >= 1
    assert report.failed == 0
    for jid in [pending_id, done_id]:
        if jid == done_id:
            assert db.get_archived_original(db_conn, jid) is not None
        else:
            assert db.get_archived_original(db_conn, jid) is None


def test_restore(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)

    job_id = _make_done_job(db_conn, clip)
    report = run_archive(db_conn, cfg, job_ids=[job_id])
    assert report.archived == 1

    restore_report = run_archive(db_conn, cfg, job_ids=[job_id], restore=True)
    assert restore_report.restored == 1
    assert restore_report.failed == 0
    assert clip.exists()
    assert db.get_archived_original(db_conn, job_id) is None

    cold_dir = Path(cfg.archive.cold_dir)  # type: ignore[arg-type]
    cold_file = cold_dir / "clips" / "20250923" / "NORMAL" / "front" / "test.mp4"
    assert not cold_file.exists()


def test_restore_requires_job(
    db_conn: sqlite3.Connection, cfg: Config,
) -> None:
    with pytest.raises(EngineError, match="--restore requires explicit --job"):
        run_archive(db_conn, cfg, restore=True, job_ids=[])


def test_archive_missing_video(
    db_conn: sqlite3.Connection, cfg: Config,
) -> None:
    nonexistent = db.create_job(db_conn, "/nonexistent/path.mp4", "noop")
    conn_exec = db_conn
    conn_exec.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?", (nonexistent.id,)
    )
    conn_exec.commit()
    report = run_archive(db_conn, cfg, job_ids=[nonexistent.id])
    assert report.archived == 0
    assert report.failed == 1
    assert len(report.failures) == 1
    assert "not found" in report.failures[0]


def test_select_jobs_respects_created_at(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "test.mp4"
    golden_clip(clip)
    _make_done_job(db_conn, clip, days_ago=100)

    rows = select_jobs(db_conn, 30)
    assert len(rows) == 1

    rows_strict = select_jobs(db_conn, 200)
    assert len(rows_strict) == 0


def test_find_clips_root(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "clips" / "20250923" / "NORMAL" / "front" / "test.mp4"
    path.parent.mkdir(parents=True)
    path.touch()
    result = _find_clips_root(path)
    assert result is not None
    assert result.name == "artifacts"

    path2 = tmp_path / "no-clips" / "videos" / "test.mp4"
    path2.parent.mkdir(parents=True)
    path2.touch()
    assert _find_clips_root(path2) is None

def test_delete_job_removes_video(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250901" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "del.mp4"
    golden_clip(clip)
    job_id = _make_done_job(db_conn, clip)

    report = run_archive(db_conn, cfg, job_ids=[job_id], delete=True)
    assert report.deleted == 1
    assert report.failed == 0
    assert report.bytes_saved > 0
    assert not clip.exists()

    row = db.get_archived_original(db_conn, job_id)
    assert row is not None
    assert row["location"] == "deleted"
    assert int(row["original_bytes"]) > 0


def test_delete_job_no_cold_dir_needed(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    config = Config()
    config.storage.db_path = str(tmp_path / "t.db")
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    config.archive.cold_dir = None
    clips_root = tmp_path / "artifacts" / "clips" / "x" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "del.mp4"
    golden_clip(clip)
    job_id = _make_done_job(db_conn, clip)

    report = run_archive(db_conn, config, job_ids=[job_id], delete=True)
    assert report.deleted == 1


def test_delete_idempotent(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "y" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "del.mp4"
    golden_clip(clip)
    job_id = _make_done_job(db_conn, clip)

    first = run_archive(db_conn, cfg, job_ids=[job_id], delete=True)
    assert first.deleted == 1
    second = run_archive(db_conn, cfg, job_ids=[job_id], delete=True)
    assert second.deleted == 0
    assert second.skipped == 1


def test_restore_deleted_fails_clearly(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "z" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "del.mp4"
    golden_clip(clip)
    job_id = _make_done_job(db_conn, clip)

    run_archive(db_conn, cfg, job_ids=[job_id], delete=True)
    report = run_archive(db_conn, cfg, job_ids=[job_id], restore=True)
    assert report.restored == 0
    assert report.failed == 1
    assert "deleted" in report.failures[0]


def test_restore_after_cold_dir_moved(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path,
) -> None:
    clips_root = tmp_path / "artifacts" / "clips" / "20250910" / "NORMAL" / "front"
    clips_root.mkdir(parents=True)
    clip = clips_root / "move.mp4"
    golden_clip(clip)
    job_id = _make_done_job(db_conn, clip)

    run_archive(db_conn, cfg, job_ids=[job_id])
    row = db.get_archived_original(db_conn, job_id)
    assert row is not None
    assert row["location"] == "cold"
    assert Path(str(row["original_path"])).is_file()
    assert clip.is_file()

    moved = tmp_path / "cold-moved"
    (tmp_path / "cold").rename(moved)
    cfg.archive.cold_dir = str(moved)

    report = run_archive(db_conn, cfg, job_ids=[job_id], restore=True)
    assert report.restored == 1
    assert report.failed == 0
    assert clip.exists()
    assert db.get_archived_original(db_conn, job_id) is None
