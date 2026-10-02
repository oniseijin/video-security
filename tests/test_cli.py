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

    result = runner.invoke(
        app, ["--db", str(db_file), "person", "new", "Wife"]
    )
    assert result.exit_code == 0
    assert "person 4 created: Wife" in result.output
    conn = db_module.connect(str(db_file))
    row = conn.execute("SELECT name FROM persons WHERE id = 4").fetchone()
    assert row["name"] == "Wife"
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


def _sweep_fixture(tmp_path):
    """Two pending jobs + engine/conn ready for _phase_sweeps."""
    from video_security.config import Config, StorageConfig
    from video_security.db import connect, create_job, init_db

    db_path = str(tmp_path / "t.db")
    config = Config(storage=StorageConfig(db_path=db_path))
    conn = connect(db_path)
    init_db(conn)
    j1 = create_job(conn, "/v/a.mp4", "h1")
    j2 = create_job(conn, "/v/b.mp4", "h2")
    return config, conn, j1, j2


def test_sweep_does_not_reclaim_job_failed_same_run(tmp_path, monkeypatch):
    """P0 regression: one transient failure must not burn every attempt of
    every queued job inside a single sweep (claim cool-down)."""
    import video_security.pipeline as pipeline_mod
    from video_security.cli import _phase_sweeps
    from video_security.engine import BatchEngine, SignalGuard

    config, conn, j1, j2 = _sweep_fixture(tmp_path)
    calls = {"harvest": 0}

    def fake_harvest(job, cfg, conn):
        calls["harvest"] += 1
        raise pipeline_mod.PipelineError("mlx-serve down")

    monkeypatch.setattr(pipeline_mod, "harvest_job", fake_harvest)
    engine = BatchEngine(config)
    claims = {"n": 0}
    orig_claim = engine.claim_next_job

    def counting_claim(phase=1, skip_ids=None):
        claims["n"] += 1
        return orig_claim(phase, skip_ids=skip_ids)

    engine.claim_next_job = counting_claim  # type: ignore[method-assign]
    _phase_sweeps(engine, conn, config, [1], None, lambda: False, SignalGuard())

    # j1 failed once and was NOT instantly re-claimed; j2 was claimed too
    assert calls["harvest"] == 2
    assert claims["n"] == 3  # j1, j2, then one dry claim that skips j1
    from video_security.db import connect

    conn2 = connect(config.storage.db_path)
    row = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j1.id,)
    ).fetchone()
    assert row["status"] == "pending"  # attempt 1 < cap: retried next run
    assert row["attempts"] == 1
    row2 = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j2.id,)
    ).fetchone()
    assert row2["status"] == "pending"
    assert row2["attempts"] == 1
    conn2.close()


def test_sweep_attempts_cap_marks_failed(tmp_path, monkeypatch):
    """Three runs (attempts 3) still terminate a permanently-broken job."""
    import video_security.pipeline as pipeline_mod
    from video_security.cli import _phase_sweeps
    from video_security.db import connect
    from video_security.engine import BatchEngine, SignalGuard

    config, conn, j1, _j2 = _sweep_fixture(tmp_path)

    def fake_harvest(job, cfg, conn):
        raise pipeline_mod.PipelineError("permanent")

    monkeypatch.setattr(pipeline_mod, "harvest_job", fake_harvest)
    engine = BatchEngine(config)
    for _ in range(3):
        _phase_sweeps(engine, conn, config, [1], None, lambda: False, SignalGuard())
    conn2 = connect(config.storage.db_path)
    row = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j1.id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    conn2.close()


def test_watermark_aborts_sweep_without_failing_jobs(tmp_path, monkeypatch):
    """P1 regression: disk-watermark breach must abort the sweep and requeue
    the job without an attempts increment — not fail-mark every job."""
    from video_security.cli import _phase_sweeps
    from video_security.db import connect
    from video_security.engine import BatchEngine, SignalGuard, WatermarkError

    config, conn, j1, j2 = _sweep_fixture(tmp_path)

    def fake_harvest(job, cfg, conn):
        raise WatermarkError("disk watermark breached")

    monkeypatch.setattr("video_security.pipeline.harvest_job", fake_harvest)
    engine = BatchEngine(config)
    claims = {"n": 0}
    orig_claim = engine.claim_next_job

    def counting_claim(phase=1, skip_ids=None):
        claims["n"] += 1
        return orig_claim(phase, skip_ids=skip_ids)

    engine.claim_next_job = counting_claim  # type: ignore[method-assign]
    _phase_sweeps(engine, conn, config, [1], None, lambda: False, SignalGuard())

    assert claims["n"] == 1  # sweep aborted after the first breach
    conn2 = connect(config.storage.db_path)
    row = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j1.id,)
    ).fetchone()
    assert row["status"] == "pending"  # requeued, no attempt burned
    assert row["attempts"] == 0
    row2 = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j2.id,)
    ).fetchone()
    assert row2["status"] == "pending"
    assert row2["attempts"] == 0
    conn2.close()


def test_sweep_unexpected_exception_isolates_job(tmp_path, monkeypatch):
    """P1 regression: a non-pipeline error (cv2/torch/sqlite/...) fails one
    job instead of killing the whole sweep."""
    from video_security.cli import _phase_sweeps
    from video_security.db import connect
    from video_security.engine import BatchEngine, SignalGuard

    config, conn, j1, j2 = _sweep_fixture(tmp_path)

    def fake_harvest(job, cfg, conn):
        if job.id == j1.id:
            raise ZeroDivisionError("division by zero (fps=0)")
        from types import SimpleNamespace

        from video_security.db import update_job_status

        update_job_status(conn, job.id, "harvested")
        return SimpleNamespace(events=0, plates=0, kept_frames=0)

    monkeypatch.setattr("video_security.pipeline.harvest_job", fake_harvest)
    engine = BatchEngine(config)
    _phase_sweeps(engine, conn, config, [1], None, lambda: False, SignalGuard())
    conn2 = connect(config.storage.db_path)
    row = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j1.id,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    row2 = conn2.execute(
        "SELECT status FROM jobs WHERE id = ?", (j2.id,)
    ).fetchone()
    assert row2["status"] == "harvested"  # sweep survived
    conn2.close()


def test_job_requeue_failed_cmd(tmp_path):
    from video_security.db import connect

    config, conn, j1, _j2 = _sweep_fixture(tmp_path)
    conn.execute("UPDATE jobs SET status = 'failed', attempts = 3 WHERE id = ?", (j1.id,))
    conn.commit()
    conn.close()
    db_file = tmp_path / "t.db"
    result = runner.invoke(app, ["--db", str(db_file), "job", "requeue-failed", "--dry-run"])
    assert result.exit_code == 0
    assert "would requeue job" in result.stdout
    result = runner.invoke(app, ["--db", str(db_file), "job", "requeue-failed"])
    assert result.exit_code == 0
    assert "requeued 1 failed job(s)" in result.stdout
    conn2 = connect(str(db_file))
    row = conn2.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (j1.id,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 3  # attempts preserved: one retry per requeue
    conn2.close()
