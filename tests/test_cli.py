from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from video_security.cli import app, parse_duration

runner = CliRunner()


def test_config_output() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "storage" in result.stdout
    assert "db_path" in result.stdout
    assert "engine" in result.stdout
    assert "heartbeat_sec" in result.stdout
    assert "llm_triage" in result.stdout
    assert "llm_detail" in result.stdout
    assert "whisper" in result.stdout
    assert "prefilter" in result.stdout


def test_list_empty_db(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    result = runner.invoke(app, ["--db", str(db_file), "list"])
    assert result.exit_code == 0
    assert "no jobs" in result.stdout.lower() or result.stdout.strip() == ""


def test_list_with_jobs(tmp_path: Path) -> None:
    import video_security.db as db_module

    db_file = tmp_path / "test.db"
    conn = db_module.connect(str(db_file))
    db_module.init_db(conn)
    db_module.create_job(conn, "/videos/a.mp4", "hash-a")
    conn.close()
    result = runner.invoke(app, ["--db", str(db_file), "list"])
    assert result.exit_code == 0
    assert "/videos/a.mp4" in result.stdout
    assert "pending" in result.stdout


def test_analyze_missing_video(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["--db", str(tmp_path / "t.db"), "analyze", "/path/to/video.mp4"]
    )
    assert result.exit_code == 1


def test_analyze_no_llm_missing_video(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["--db", str(tmp_path / "t.db"), "analyze", "/path/to/video.mp4", "--no-llm"]
    )
    assert result.exit_code == 1


def test_import_command(tmp_path: Path) -> None:
    from tests.golden import golden_variant

    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    golden_variant(card / "EVENT" / "250807121252.MP4", b"clip")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        f'[storage]\ndb_path = "{tmp_path / "t.db"}"\n'
        f'artifact_dir = "{artifacts}"\n[import]\npreflight_gb = 0\n'
    )
    result = runner.invoke(app, ["--config", str(cfg_file), "import", str(card)])
    assert result.exit_code == 0
    assert "imported 1 new clips" in result.stdout
    jobs = [p for p in artifacts.rglob("*.MP4")]
    assert len(jobs) == 1


def test_import_missing_source(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["--db", str(tmp_path / "t.db"), "import", str(tmp_path / "nope")]
    )
    assert result.exit_code == 1


def test_search_no_query(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--db", str(tmp_path / "t.db"), "search"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_report_missing_job(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--db", str(tmp_path / "t.db"), "report", "42"])
    assert result.exit_code == 1


def test_parse_duration_valid() -> None:
    assert parse_duration("2h") == 7200.0
    assert parse_duration("90m") == 5400.0
    assert parse_duration("45s") == 45.0
    assert parse_duration("1h30m15s") == 5415.0
    assert parse_duration("0h0m1s") == 1.0
    assert parse_duration("1h0m0s") == 3600.0


def test_parse_duration_invalid() -> None:
    with pytest.raises(ValueError):
        parse_duration("")
    with pytest.raises(ValueError):
        parse_duration("invalid")
    with pytest.raises(ValueError):
        parse_duration("1x")
    with pytest.raises(ValueError):
        parse_duration("0h0m0s")


def test_parse_duration_components_only() -> None:
    assert parse_duration("1h") == 3600.0
    assert parse_duration("30m") == 1800.0
    assert parse_duration("15s") == 15.0


def test_nonexistent_config_file() -> None:
    result = runner.invoke(app, ["--config", "/nonexistent/path/config.toml", "analyze"])
    assert result.exit_code == 1


def test_help_shows_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["analyze", "import", "search", "report", "list", "config"]:
        assert cmd in result.stdout

def test_analyze_phases_validation(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    result = runner.invoke(
        app, ["--db", str(db_file), "analyze", "--phases", "banana"]
    )
    assert result.exit_code == 1
    assert "invalid --phases" in result.output

    result = runner.invoke(
        app, ["--db", str(db_file), "analyze", "--phases", "1,4"]
    )
    assert result.exit_code == 1
    assert "--phases must be" in result.output


def test_analyze_phase1_only_no_jobs(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    result = runner.invoke(
        app, ["--db", str(db_file), "analyze", "--phases", "1"]
    )
    assert result.exit_code == 0
    assert "phase 1 sweep complete: 0 jobs" in result.stdout


def test_search_date_range(tmp_path: Path) -> None:
    import video_security.db as db_module

    db_file = tmp_path / "t.db"
    conn = db_module.connect(str(db_file))
    db_module.init_db(conn)
    j1 = db_module.create_job(conn, "/v/a.mp4", "h1")
    conn.execute(
        "UPDATE jobs SET recording_start_utc = '2026-09-20 10:00:00' "
        "WHERE id = ?",
        (j1.id,),
    )
    db_module.insert_event(
        conn, j1.id, "intrusion", 5.0, 6.0, 0, None, "[]", 0.5, 0.5
    )
    conn.commit()
    conn.close()
    result = runner.invoke(
        app,
        [
            "--db",
            str(db_file),
            "search",
            "--from",
            "2026-09-20",
            "--to",
            "2026-09-21",
        ],
    )
    assert result.exit_code == 0
    assert "EVENTS 2026-09-20..2026-09-21" in result.output
    assert "intrusion" in result.output
    empty = runner.invoke(
        app, ["--db", str(db_file), "search", "--from", "2020-01-01", "--to", "2020-01-02"]
    )
    assert empty.exit_code == 0
    assert "intrusion" not in empty.output


def test_event_suppress_and_restore(tmp_path: Path) -> None:
    import video_security.db as db_module

    db_file = tmp_path / "t.db"
    conn = db_module.connect(str(db_file))
    db_module.init_db(conn)
    j1 = db_module.create_job(conn, "/v/a.mp4", "h1")
    ev = db_module.insert_event(
        conn, j1.id, "intrusion", 5.0, 6.0, 0, 72, "[]", 0.9, 0.9
    )
    db_module.insert_analysis_result(
        conn, j1.id, ev, "d1", "1", "detail", '{"description": "x"}', None, 0, None
    )
    conn.execute(
        "UPDATE events SET status = 'detailed', llm_result_id = 1 WHERE id = ?",
        (ev,),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "event", "suppress", str(ev)])
    assert result.exit_code == 0
    assert "suppressed" in result.output

    conn = db_module.connect(str(db_file))
    row = conn.execute(
        "SELECT status, llm_result_id FROM events WHERE id = ?", (ev,)
    ).fetchone()
    conn.close()
    assert row["status"] == "suppressed"
    assert row["llm_result_id"] == 1

    result = runner.invoke(
        app, ["--db", str(db_file), "event", "suppress", str(ev), "--restore"]
    )
    assert result.exit_code == 0
    conn = db_module.connect(str(db_file))
    row = conn.execute(
        "SELECT status, llm_result_id FROM events WHERE id = ?", (ev,)
    ).fetchone()
    conn.close()
    assert row["status"] == "detailed"
    assert row["llm_result_id"] == 1

    missing = runner.invoke(app, ["--db", str(db_file), "event", "suppress", "9999"])
    assert missing.exit_code == 1


def test_person_commands(tmp_path: Path) -> None:
    import video_security.db as db_module

    db_file = tmp_path / "t.db"
    conn = db_module.connect(str(db_file))
    db_module.init_db(conn)
    j1 = db_module.create_job(conn, "/v/a.mp4", "h1")
    conn.execute(
        "INSERT INTO persons (id, sightings) VALUES (1, 2), (2, 1), (3, 1)"
    )
    for eid, pid in ((10, 1), (11, 1), (12, 2), (13, 3)):
        conn.execute(
            "INSERT INTO events (id, job_id, event_type, start_sec, end_sec, "
            "clip_id, detector_score, priority, status) VALUES "
            f"({eid}, {j1.id}, 'intrusion', 5.0, 6.0, 0, 0.9, 0.9, 'detailed')"
        )
        conn.execute(
            "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
            "crop_path, person_id) VALUES "
            f"({j1.id}, {eid}, 0, 0, '/x.jpg', {pid})"
        )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "person", "name", "1", "Mika"])
    assert result.exit_code == 0
    conn = db_module.connect(str(db_file))
    assert conn.execute("SELECT name FROM persons WHERE id = 1").fetchone()["name"] == "Mika"
    face3 = conn.execute(
        "SELECT id FROM faces WHERE person_id = 3"
    ).fetchone()["id"]
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "person", "merge", "2", "1"])
    assert result.exit_code == 0
    assert "1 face moved" in result.output or "1 faces moved" in result.output
    conn = db_module.connect(str(db_file))
    rows = conn.execute("SELECT id, name, sightings FROM persons").fetchall()
    assert len(rows) == 2
    merged = next(r for r in rows if r["id"] == 1)
    assert merged["name"] == "Mika"
    assert merged["sightings"] == 3
    conn.close()

    result = runner.invoke(
        app, ["--db", str(db_file), "person", "move", str(face3), "1"]
    )
    assert result.exit_code == 0
    conn = db_module.connect(str(db_file))
    moved = conn.execute(
        "SELECT person_id FROM faces WHERE id = ?", (face3,)
    ).fetchone()["person_id"]
    assert moved == 1
    conn.close()

    missing = runner.invoke(app, ["--db", str(db_file), "person", "name", "99", "X"])
    assert missing.exit_code == 1
    bad_merge = runner.invoke(app, ["--db", str(db_file), "person", "merge", "99", "1"])
    assert bad_merge.exit_code == 1


def test_diff_prompt_versions(tmp_path: Path) -> None:
    import video_security.db as db_module

    db_file = tmp_path / "t.db"
    conn = db_module.connect(str(db_file))
    db_module.init_db(conn)
    j1 = db_module.create_job(conn, "/v/a.mp4", "h1")
    ev = db_module.insert_event(
        conn, j1.id, "intrusion", 5.0, 6.0, 0, None, "[]", 0.5, 0.5
    )
    db_module.insert_analysis_result(
        conn, j1.id, ev, "d1", "1", "detail",
        '{"description": "person near gate"}', None, 0, None,
    )
    db_module.insert_analysis_result(
        conn, j1.id, ev, "d1", "2", "detail",
        '{"description": "two people near gate"}', None, 0, None,
    )
    conn.commit()
    conn.close()
    result = runner.invoke(
        app, ["--db", str(db_file), "diff", str(j1.id), "1", "2"]
    )
    assert result.exit_code == 0
    assert "compared 1 events, 1 changed" in result.output
    assert "person near gate" in result.output
    assert "two people near gate" in result.output
