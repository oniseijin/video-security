from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from video_security.cli import app
from video_security.db import (
    clip_fps,
    connect,
    events_for_plate,
    init_db,
    insert_event,
    insert_frame,
    insert_plate,
    nearest_event_to_seconds,
)

runner = CliRunner()


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def _job(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (video_path, video_hash) VALUES ('/v/a.mp4', 'h1')"
        " RETURNING id"
    )
    return int(cur.fetchone()[0])


def test_events_for_plate_filters_by_track() -> None:
    conn = connect(":memory:")
    init_db(conn)
    job_id = _job(conn)
    insert_event(conn, job_id, "plate_capture", 10.0, 11.0, 0, 7, "[]", 1.0, 1.0)
    insert_event(conn, job_id, "suspicious_behavior", 20.0, 21.0, 0, 8, "[]", 0.5, 0.3)
    rows = events_for_plate(conn, job_id, 7)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "plate_capture"
    assert rows[0]["track_id"] == 7
    conn.close()


def test_nearest_event_to_seconds() -> None:
    conn = connect(":memory:")
    init_db(conn)
    job_id = _job(conn)
    insert_event(conn, job_id, "plate_capture", 10.0, 11.0, 0, 7, "[]", 1.0, 1.0)
    insert_event(conn, job_id, "plate_capture", 30.0, 31.0, 0, 7, "[]", 1.0, 1.0)
    row = nearest_event_to_seconds(conn, job_id, 7, 29.0)
    assert row is not None
    assert row["start_sec"] == 30.0
    assert nearest_event_to_seconds(conn, job_id, 99, 10.0) is None
    conn.close()


def test_clip_fps_from_frames() -> None:
    conn = connect(":memory:")
    init_db(conn)
    job_id = _job(conn)
    for i in range(90):
        insert_frame(conn, job_id, 0, i, i / 30.0, None, None)
    assert abs(clip_fps(conn, job_id) - 30.0) < 0.1
    assert clip_fps(conn, job_id + 1) == 30.0
    conn.close()


def test_search_links_plate_to_events(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    job_id = _job(conn)
    insert_event(conn, job_id, "plate_capture", 10.0, 11.0, 0, 7, "[]", 1.0, 1.0)
    insert_plate(
        conn, job_id, 7, 0, "習志野 れ 12-08", "習志野れ1208", 1.0, 300,
        json.dumps(["習志野 れ 12-08"]),
    )
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "search", "習志野"])
    assert result.exit_code == 0
    assert "習志野れ1208" in result.stdout
    assert "-> event" in result.stdout
    assert "plate_capture" in result.stdout
