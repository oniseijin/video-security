from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from video_security.config import Config, EngineConfig, StorageConfig
from video_security.db import (
    create_job,
    delete_job_rows,
    insert_event,
    insert_frame,
    insert_transcript_segment,
)
from video_security.engine import (
    BatchEngine,
    EngineError,
    SignalGuard,
    disk_free_gb,
    on_ac_power,
    video_hash,
)


def _write_random_file(path: Path, size: int, seed: int = 42) -> None:
    import random
    rng = random.Random(seed)
    chunk = 1024 * 1024
    with open(path, "wb") as f:
        remaining = size
        while remaining > 0:
            n = min(chunk, remaining)
            f.write(bytes(rng.randint(0, 255) for _ in range(n)))
            remaining -= n


def test_video_hash(tmp_path: Path) -> None:
    path1 = tmp_path / "video1.bin"
    path2 = tmp_path / "video2.bin"
    _write_random_file(path1, 20 * 1024 * 1024)
    _write_random_file(path2, 20 * 1024 * 1024)
    h1 = video_hash(path1)
    h2 = video_hash(path2)
    assert len(h1) == 64
    assert h1 == h2
    with open(path1, "r+b") as f:
        f.seek(9 * 1024 * 1024)
        f.write(b"\xFF")
    h3 = video_hash(path1)
    assert h3 != h1
    path_small = tmp_path / "small.bin"
    _write_random_file(path_small, 1024 * 1024)
    h4 = video_hash(path_small)
    assert len(h4) == 64


def test_disk_free_gb(tmp_path: Path) -> None:
    assert disk_free_gb(Path.home()) > 0
    assert disk_free_gb(tmp_path) > 0


def test_claim_next_job(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    j1 = create_job(conn, "/v/1.mp4", "h1")
    j2 = create_job(conn, "/v/2.mp4", "h2")
    conn.close()
    claimed1 = engine.claim_next_job()
    assert claimed1 is not None
    assert claimed1.status == "extracting"
    assert claimed1.id == j1.id
    claimed2 = engine.claim_next_job()
    assert claimed2 is not None
    assert claimed2.status == "extracting"
    assert claimed2.id == j2.id
    claimed3 = engine.claim_next_job()
    assert claimed3 is None


def test_reclaim_stale_jobs(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/x.mp4", "hx")
    conn.execute("UPDATE jobs SET status = 'filtering' WHERE id = ?", (job.id,))
    conn.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (job.id,),
    )
    conn.commit()
    conn.close()
    count = engine.reclaim_stale_jobs(max_age_sec=3600)
    assert count == 1
    conn = engine._connect()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job.id,)).fetchone()
    assert row is not None and row["status"] == "pending"
    conn.close()


def test_preflight_passes(tmp_path: Path) -> None:
    config = Config(
        storage=StorageConfig(db_path=str(tmp_path / "t.db"), artifact_dir=str(tmp_path / "art")),
    )
    engine = BatchEngine(config)
    engine.preflight()
    assert (tmp_path / "art").exists()


def test_preflight_fails_tiny(tmp_path: Path) -> None:
    config = Config(
        storage=StorageConfig(db_path=str(tmp_path / "t.db")),
        engine=EngineConfig(disk_preflight_gb=999999),
    )
    engine = BatchEngine(config)
    with pytest.raises(EngineError):
        engine.preflight()


def test_mark_failed_retry_then_dead(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/f.mp4", "hf")
    conn.close()
    engine.mark_failed(job.id, attempts_cap=3)
    conn = engine._connect()
    row = conn.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    conn.close()
    engine.mark_failed(job.id, attempts_cap=3)
    engine.mark_failed(job.id, attempts_cap=3)
    conn = engine._connect()
    row2 = conn.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row2 is not None
    assert row2["status"] == "failed"
    assert row2["attempts"] == 3
    conn.close()


def test_delete_job_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/d.mp4", "hd")
    conn.close()
    conn = engine._connect()
    insert_frame(conn, job.id, 1, 1, 0.0, 0, "day")
    insert_event(conn, job.id, "motion", 0.0, 1.0, 1, None, "[]", 0.9, 0.5)
    insert_transcript_segment(conn, job.id, 1, 1, 0.0, 1.0, "hello", None)
    conn.close()
    conn = engine._connect()
    delete_job_rows(conn, job.id)
    for table in ["frames", "events", "transcript_segments"]:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE job_id = ?", (job.id,)
        ).fetchone()[0]
        assert count == 0
    conn.close()


def test_prune_old_jobs(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path), engine=EngineConfig(retention_days=1))
    engine = BatchEngine(config)
    conn = engine._connect()
    old_job = create_job(conn, "/v/old.mp4", "ho")
    fresh_job = create_job(conn, "/v/fresh.mp4", "hf2")
    conn.execute(
        "UPDATE jobs SET created_at = datetime('now', '-10 days') WHERE id = ?",
        (old_job.id,),
    )
    conn.commit()
    conn.close()
    pruned = engine.prune_old_jobs()
    assert pruned == 1
    conn = engine._connect()
    remaining = conn.execute("SELECT id FROM jobs").fetchall()
    conn.close()
    assert len(remaining) == 1
    assert remaining[0]["id"] == fresh_job.id


def test_signal_guard() -> None:
    guard = SignalGuard()
    with guard:
        os.kill(os.getpid(), signal.SIGTERM)
    assert guard.stop_requested is True
    with SignalGuard() as g:
        pass
    assert signal.getsignal(signal.SIGTERM) is not g._handle


def test_on_ac_power() -> None:
    result = on_ac_power()
    assert isinstance(result, bool)


def test_migration_upgrade(tmp_path: Path) -> None:
    from video_security.db import MIGRATIONS as M
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == len(M)
    engine2 = BatchEngine(config)
    conn2 = engine2._connect()
    version2: int = conn2.execute("PRAGMA user_version").fetchone()[0]
    assert version2 == len(M)
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    col_names = [r["name"] for r in info]
    assert "attempts" in col_names
    conn.close()
    conn2.close()

def test_claim_and_reclaim_phases(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/p.mp4", "hp")
    conn.execute("UPDATE jobs SET status = 'harvested' WHERE id = ?", (job.id,))
    conn.commit()
    conn.close()

    assert engine.claim_next_job(phase=1) is None
    claimed = engine.claim_next_job(phase=2)
    assert claimed is not None
    assert claimed.status == "triage"
    assert claimed.id == job.id

    conn = engine._connect()
    conn.execute(
        "UPDATE jobs SET status = 'detail' WHERE id = ?", (job.id,)
    )
    conn.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (job.id,),
    )
    conn.commit()
    conn.close()
    assert engine.reclaim_stale_jobs(max_age_sec=3600) == 1
    conn = engine._connect()
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row is not None and row["status"] == "triaged"
    conn.close()

    claimed3 = engine.claim_next_job(phase=3)
    assert claimed3 is not None
    assert claimed3.status == "detail"


def test_reclaim_stale_triage_keeps_harvest(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/t.mp4", "ht")
    conn.execute("UPDATE jobs SET status = 'triage' WHERE id = ?", (job.id,))
    conn.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (job.id,),
    )
    conn.commit()
    conn.close()
    engine.reclaim_stale_jobs(max_age_sec=3600)
    conn = engine._connect()
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row is not None and row["status"] == "harvested"
    conn.close()


def test_mark_failed_returns_to_phase_rest(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    engine = BatchEngine(config)
    conn = engine._connect()
    job = create_job(conn, "/v/f.mp4", "hf")
    conn.execute("UPDATE jobs SET status = 'detail' WHERE id = ?", (job.id,))
    conn.commit()
    conn.close()
    engine.mark_failed(job.id)
    conn = engine._connect()
    row = conn.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row is not None and row["status"] == "triaged"
    assert row["attempts"] == 1
    conn.close()
