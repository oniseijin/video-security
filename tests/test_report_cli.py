from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from video_security.cli import app
from video_security.db import connect, init_db, insert_frame_text

runner = CliRunner()


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def test_report_not_found(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "report", "999"])
    assert result.exit_code == 1


def test_search_empty_db(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "search", "hello"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["--db", str(db_file), "search", "--kind", "dangerous"])
    assert result.exit_code == 0


def test_search_fts_match(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    cur = conn.execute(
        "INSERT INTO jobs (video_path, video_hash) VALUES ('/v/a.mp4', 'h1') RETURNING id"
    )
    job_id = cur.fetchone()[0]
    insert_frame_text(conn, job_id, 0, 10, "delivery truck arrived", "other", None, 0.9)
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "search", "truck"])
    assert result.exit_code == 0
    assert "delivery truck arrived" in result.stdout
    assert "/v/a.mp4" in result.stdout


def test_search_no_match(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "search", "AND"])
    assert result.exit_code == 0
    assert "TEXT MATCHES:" in result.stdout


def test_report_generates(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = _db(tmp_path)
    cur = conn.execute(
        "INSERT INTO jobs (video_path, video_hash) VALUES ('/v/a.mp4', 'h1') RETURNING id"
    )
    job_id = cur.fetchone()[0]
    conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, detector_score)"
        " VALUES (?, 'audio_distress', 1.0, 2.0, 0, 0.5)",
        (job_id,),
    )
    conn.commit()
    conn.close()
    result = runner.invoke(
        app,
        [
            "--db",
            str(db_file),
            "report",
            str(job_id),
        ],
    )
    assert result.exit_code == 0
    out = Path(result.stdout.strip().splitlines()[-1])
    assert out.exists()
    content = out.read_text()
    assert "audio_distress" in content
    assert json.loads("null") is None
