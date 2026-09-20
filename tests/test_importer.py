from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from video_security import db
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.importer import run_import


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
    config.import_.preflight_gb = 0
    return config


def _make_card(root: Path) -> None:
    for mode in ("NORMAL", "EVENT"):
        (root / mode).mkdir(parents=True)
        (root / "REAR" / mode).mkdir(parents=True)
        (root / "System" / "NMEA" / mode).mkdir(parents=True)
    (root / "EVENT" / "250807121252.MP4").write_bytes(b"front-event-data")
    (root / "REAR" / "EVENT" / "250807121252.MP4").write_bytes(b"rear-event-data")
    (root / "System" / "NMEA" / "EVENT" / "250807121252.NMEA").write_text("$X\n")
    (root / "NORMAL" / "250921170102.MP4").write_bytes(b"front-normal-data")
    (root / "NORMAL" / "250921170300.MP4").write_bytes(b"other-normal-data")


def _import_date_dir(artifacts: Path) -> Path:
    clips = artifacts / "clips"
    dates = sorted(d.name for d in clips.iterdir())
    assert len(dates) == 1
    return clips / dates[0]


def test_run_import_registers_jobs(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    report = run_import(card, cfg, db_conn)
    assert report.imported == 4
    assert report.skipped == 1
    assert report.failed == 0
    assert len(report.jobs) == 4

    jobs = db.list_jobs(db_conn)
    assert len(jobs) == 4
    event_front = [j for j in jobs if j.video_path.endswith("front/250807121252.MP4")]
    assert len(event_front) == 1
    job = event_front[0]
    assert job.import_id is not None
    assert job.import_id.endswith("-card")
    assert job.recording_start_utc == "2025-08-07T03:12:52+00:00"

    clips_rows = db_conn.execute(
        "SELECT * FROM clips ORDER BY job_id"
    ).fetchall()
    assert len(clips_rows) == 4
    channels = {r["channel"] for r in clips_rows}
    assert channels == {"front", "rear"}
    event_rows = [
        r for r in clips_rows if r["filename"] == "250807121252.MP4"
    ]
    assert {r["channel"] for r in event_rows} == {"front", "rear"}
    assert all(r["priority"] == 1.0 for r in event_rows)

    sessions = db_conn.execute("SELECT * FROM sessions").fetchall()
    assert len(sessions) == 2
    paired = [
        r for r in sessions if r["session_id"] == "250807121252"
    ]
    assert len(paired) == 2
    clips_json = paired[0]["clips_json"]
    assert "rear" in clips_json and "front" in clips_json


def test_run_import_copy_layout_and_sidecar(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    run_import(card, cfg, db_conn)

    artifacts = Path(cfg.storage.artifact_dir)
    day = _import_date_dir(artifacts)
    front = day / "EVENT" / "front" / "250807121252.MP4"
    rear = day / "EVENT" / "rear" / "250807121252.MP4"
    nmea = day / "EVENT" / "front" / "250807121252.NMEA"
    assert front.read_bytes() == b"front-event-data"
    assert rear.read_bytes() == b"rear-event-data"
    assert nmea.exists()
    assert (day / "NORMAL" / "front" / "250921170102.MP4").exists()
    assert not (day / "NORMAL" / "rear").exists()
    assert not list(day.rglob("*.vs-partial"))


def test_run_import_incremental_dedup(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    first = run_import(card, cfg, db_conn)
    assert first.imported == 4
    second = run_import(card, cfg, db_conn)
    assert second.imported == 0
    assert second.skipped == 4
    assert len(db.list_jobs(db_conn)) == 4


def test_run_import_bad_verify_fails(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    from video_security import importer

    def bad_copy(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"corrupt")

    monkeypatch.setattr(importer, "_copy_atomic", bad_copy)
    report = run_import(card, cfg, db_conn)
    assert report.imported == 0
    assert report.failed == 5
    assert len(db.list_jobs(db_conn)) == 0


def test_run_import_preflight_low_disk(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    cfg.import_.preflight_gb = 99999
    from video_security.engine import EngineError

    with pytest.raises(EngineError):
        run_import(card, cfg, db_conn)


def test_run_import_source_missing(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    from video_security.engine import EngineError

    with pytest.raises(EngineError):
        run_import(tmp_path / "nope", cfg, db_conn)


def test_run_import_artifact_not_mounted(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path
) -> None:
    card = tmp_path / "card"
    _make_card(card)
    cfg.storage.artifact_dir = str(tmp_path / "not-mounted")
    from video_security.engine import EngineError

    with pytest.raises(EngineError):
        run_import(card, cfg, db_conn)
