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
    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    (card / "EVENT" / "250807121252.MP4").write_bytes(b"clip")
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
