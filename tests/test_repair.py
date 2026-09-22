from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.golden import golden_clip
from video_security import db
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.ingest.frames import probe_video
from video_security.repair import run_repair

pytestmark = pytest.mark.skipif(
    shutil.which("untrunc") is None, reason="untrunc binary not installed"
)


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    config = Config()
    config.storage.db_path = str(tmp_path / "t.db")
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    return config


def _broken_clip(tmp_path: Path, name: str) -> Path:
    clips = tmp_path / "artifacts" / "clips" / "20250928" / "NORMAL" / "front"
    clips.mkdir(parents=True)
    good = golden_clip(clips / f"good-{name}.mp4")
    data = good.read_bytes()
    broken = clips / name
    broken.write_bytes(data[: int(len(data) * 0.65)])
    return broken


def test_repair_recovers_and_requeues(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    broken = _broken_clip(tmp_path, "broken.mp4")
    j = db.create_job(db_conn, str(broken), "brokenhash")
    conn = db_conn
    conn.execute(
        "UPDATE jobs SET status = 'failed', attempts = 3 WHERE id = ?", (j.id,)
    )
    conn.commit()

    report = run_repair(db_conn, [j.id])
    assert report.repaired == 1
    assert report.failed == 0

    row = db.get_job_by_id(db_conn, j.id)
    assert row is not None
    repaired = Path(row.video_path)
    assert repaired.name == "broken.repaired.mp4"
    assert broken.exists()
    assert row.status == "pending"
    attempts = db_conn.execute(
        "SELECT attempts FROM jobs WHERE id = ?", (j.id,)
    ).fetchone()["attempts"]
    assert int(attempts) == 0
    probe_video(repaired)


def test_repair_skips_healthy(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    clips = tmp_path / "artifacts" / "clips" / "20250929" / "NORMAL" / "front"
    clips.mkdir(parents=True)
    good = golden_clip(clips / "fine.mp4")
    j = db.create_job(db_conn, str(good), "finehash")

    report = run_repair(db_conn, [j.id])
    assert report.repaired == 0
    assert report.skipped == 1


def test_repair_requires_job() -> None:
    from video_security.engine import EngineError

    with pytest.raises(EngineError):
        run_repair(_noop_conn(), [])


def _noop_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_repair_missing_binary(
    db_conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from video_security.engine import EngineError

    broken = _broken_clip(tmp_path, "nobin.mp4")
    db.create_job(db_conn, str(broken), "nobinhash")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(EngineError, match="untrunc not found"):
        run_repair(db_conn, [1])
