from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.watchlist import (
    add_watchlist,
    evaluate_job,
    list_watchlists,
    remove_watchlist,
)


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def _seed_job_data(conn: sqlite3.Connection, job_id: int = 1) -> None:
    conn.execute(
        "INSERT INTO jobs (id, video_path, video_hash, status) "
        "VALUES (?, '/v/a.mp4', 'h1', 'done')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, raw_text, norm_text, "
        "confidence, best_frame) VALUES (?, 7, 0, 'L12-08', 'L1208', 1.0, 100)",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO events (id, job_id, event_type, start_sec, end_sec, "
        "clip_id, track_id, keyframes_json, faces_json, detector_score, "
        "priority, status) VALUES (10, ?, 'plate_capture', 5.0, 6.0, 0, 7, "
        "'[]', NULL, 1.0, 0.6, 'detailed')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO frame_text (job_id, clip_id, frame_number, text, "
        "text_kind, confidence) VALUES (?, 0, 50, 'NO ENTRY', 'other', 0.9)",
        (job_id,),
    )
    conn.commit()


def test_add_list_remove(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        add_watchlist(db_conn, "bogus", "x", None)
    wl1 = add_watchlist(db_conn, "plate", "L1208", "neighbor car")
    wl2 = add_watchlist(db_conn, "person", "3", None)
    rows = list_watchlists(db_conn)
    assert [r["id"] for r in rows] == [wl1, wl2]
    assert rows[0]["hits"] == 0
    assert remove_watchlist(db_conn, wl1)
    assert not remove_watchlist(db_conn, 999)
    assert len(list_watchlists(db_conn)) == 1


def test_evaluate_plate_and_text(db_conn: sqlite3.Connection) -> None:
    _seed_job_data(db_conn)
    wl = add_watchlist(db_conn, "plate", "L12%", "watch")
    wl2 = add_watchlist(db_conn, "text", "entry", "sign")
    cfg = Config()
    hits = evaluate_job(db_conn, cfg, 1)
    kinds = {(h.watchlist_id, h.detail) for h in hits}
    assert (wl, "L1208") in kinds
    assert any(h.watchlist_id == wl2 and "NO ENTRY" in (h.detail or "") for h in hits)
    rows = db_conn.execute(
        "SELECT watchlist_id, event_id, detail FROM watchlist_hits"
    ).fetchall()
    plate_row = [r for r in rows if r["watchlist_id"] == wl][0]
    assert plate_row["event_id"] == 10
    assert plate_row["detail"] == "L1208"


def test_evaluate_person(db_conn: sqlite3.Connection) -> None:
    _seed_job_data(db_conn)
    conn2 = db_conn
    conn2.execute("INSERT INTO persons (sightings) VALUES (1)")
    conn2.execute(
        "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
        "crop_path, quality, embedding, person_id) "
        "VALUES (1, 10, 0, 0, '/x.jpg', 0.9, NULL, 1)"
    )
    conn2.commit()
    wl = add_watchlist(db_conn, "person", "1", "the guy")
    hits = evaluate_job(db_conn, Config(), 1)
    assert any(h.watchlist_id == wl and h.event_id == 10 for h in hits)


def test_evaluate_notify_fires(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_job_data(db_conn)
    add_watchlist(db_conn, "plate", "L1208", None)
    cfg = Config()
    cfg.watchlist.notify_command = "echo {message}"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr("subprocess.run", fake_run)
    hits = evaluate_job(db_conn, cfg, 1)
    assert hits
    assert len(calls) == 1
    assert "L1208" in calls[0][2]


def test_harvest_triggers_evaluation(
    db_conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_watchlist(db_conn, "plate", "YOLO42", "from harvest")
    from tests.golden import golden_clip
    from video_security.pipeline import enqueue_video, harvest_job

    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    harvest_job(job, config, db_conn)
    n = db_conn.execute(
        "SELECT COUNT(*) FROM watchlist_hits WHERE job_id = ?", (job.id,)
    ).fetchone()[0]
    assert n >= 1
    _ = json
