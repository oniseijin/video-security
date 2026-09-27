from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

import video_security.db as db_module
from tests.golden import golden_variant
from video_security.cli import app
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.engine import video_hash
from video_security.importer import run_import

runner = CliRunner()


def _seed_job(base: Path, conn: sqlite3.Connection, video_path: str) -> int:
    job = db_module.create_job(conn, video_path, "hash-xyz")
    conn.execute(
        "INSERT INTO photos_imports (uuid, job_id, video_hash, filename) "
        "VALUES ('uuid-1', ?, 'hash-xyz', 'a.mp4')",
        (job.id,),
    )
    conn.commit()
    return job.id


def _config_file(base: Path, artifacts: Path, db_file: Path) -> Path:
    config_path = base / "config.toml"
    config_path.write_text(
        f"[storage]\n"
        f"db_path = {str(db_file)!r}\n"
        f"artifact_dir = {str(artifacts)!r}\n",
        encoding="utf-8",
    )
    return config_path


def test_flag_and_unflag(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    job_id = _seed_job(tmp_path, conn, "/outside/video.mp4")
    conn.close()

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "flag", str(job_id), "--note", "review me"]
    )
    assert result.exit_code == 0
    assert "flagged: review me" in result.stdout

    conn = connect(str(db_file))
    row = conn.execute(
        "SELECT flag_note FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["flag_note"] == "review me"
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "job", "unflag", str(job_id)])
    assert result.exit_code == 0
    conn = connect(str(db_file))
    row = conn.execute(
        "SELECT flag_note FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["flag_note"] is None
    conn.close()


def test_flag_missing_job(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    result = runner.invoke(app, ["--db", str(db_file), "job", "flag", "99"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_remove_job_records_and_clears(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)

    artifacts = tmp_path / "artifacts"
    clip_dest = artifacts / "clips" / "20260925" / "personal" / "front" / "a.mp4"
    clip_dest.parent.mkdir(parents=True)
    golden_variant(clip_dest, b"personal-clip")
    job_id = _seed_job(tmp_path, conn, str(clip_dest))
    conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, "
        "detector_score, priority) VALUES (?, 'intrusion', 1.0, 2.0, 0, 0.9, 0.9)",
        (job_id,),
    )
    frames_dir = artifacts / "frames" / str(job_id)
    frames_dir.mkdir(parents=True)
    (frames_dir / "event_1_0.jpg").write_bytes(b"fake")
    report_file = artifacts / "reports" / f"job_{job_id}.html"
    report_file.parent.mkdir(parents=True)
    report_file.write_bytes(b"<html></html>")
    conn.commit()
    conn.close()

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "--config", str(_config_file(tmp_path, artifacts, db_file)),
            "job", "remove", str(job_id),
            "--reason", "family video",
        ],
    )
    assert result.exit_code == 0
    assert "removed job" in result.stdout
    assert "family video" in result.stdout
    assert "clip deleted" in result.stdout

    conn = connect(str(db_file))
    assert conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM photos_imports WHERE job_id = ?", (job_id,)
    ).fetchone()[0] == 0
    removal = conn.execute("SELECT * FROM removals").fetchone()
    assert removal["job_id"] == job_id
    assert removal["reason"] == "family video"
    assert removal["cold_path"] is None
    conn.close()

    assert not clip_dest.exists()
    assert not frames_dir.exists()
    assert not report_file.exists()


def test_remove_keeps_clip_outside_artifact_dir(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    outside = tmp_path / "outside" / "video.mp4"
    outside.parent.mkdir()
    golden_variant(outside, b"outside-clip")
    job_id = _seed_job(tmp_path, conn, str(outside))
    conn.close()

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "--config", str(_config_file(tmp_path, tmp_path / "artifacts", db_file)),
            "job", "remove", str(job_id),
        ],
    )
    assert result.exit_code == 0
    assert "clip file kept" in result.stdout
    assert outside.exists()


def test_removals_listing(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    db_module.insert_removal(conn, 7, "hash-a", "/clips/a.mp4", "personal")
    db_module.insert_removal(
        conn, 8, "hash-b", "/clips/b.mp4", "personal", cold_path="/cold/b.mp4"
    )
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "job", "removals"])
    assert result.exit_code == 0
    assert "job 7" in result.stdout
    assert "/clips/b.mp4" in result.stdout
    assert "cold original kept: /cold/b.mp4" in result.stdout
    assert "hash-a" in result.stdout
    assert "hash-b" in result.stdout


def test_remove_bulk_source_photos(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    artifacts = tmp_path / "artifacts"
    conn = connect(str(db_file))
    init_db(conn)
    clips: dict[str, Path] = {}
    jobs: dict[str, int] = {}
    for name in ("a", "b", "c"):
        dest = artifacts / "clips" / "20260927" / "personal" / "front" / f"{name}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        golden_variant(dest, f"clip-{name}".encode())
        clips[name] = dest
        jobs[name] = db_module.create_job(conn, str(dest), f"hash-{name}").id
    for uuid, name in (("uuid-a", "a"), ("uuid-b", "b")):
        conn.execute(
            "INSERT INTO photos_imports (uuid, job_id, video_hash, filename) "
            "VALUES (?, ?, ?, 'x.mp4')",
            (uuid, jobs[name], f"hash-{name}"),
        )
    conn.commit()
    conn.close()

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "--config", str(_config_file(tmp_path, artifacts, db_file)),
            "job", "remove", "--source", "photos",
        ],
    )
    assert result.exit_code == 0
    assert "removed 2 jobs (--source photos)" in result.stdout

    conn = connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM photos_imports").fetchone()[0] == 0
    rows = conn.execute(
        "SELECT job_id FROM removals ORDER BY job_id"
    ).fetchall()
    assert [r["job_id"] for r in rows] == sorted([jobs["a"], jobs["b"]])
    assert conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE id = ?", (jobs["c"],)
    ).fetchone()[0] == 1
    conn.close()
    assert not clips["a"].exists()
    assert not clips["b"].exists()
    assert clips["c"].exists()


def test_remove_bulk_import_id(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    artifacts = tmp_path / "artifacts"
    conn = connect(str(db_file))
    init_db(conn)
    clips: dict[str, Path] = {}
    jobs: dict[str, int] = {}
    for name in ("a", "b", "c"):
        dest = artifacts / "clips" / "20260927" / "personal" / "front" / f"{name}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        golden_variant(dest, f"clip-{name}".encode())
        clips[name] = dest
        jobs[name] = db_module.create_job(conn, str(dest), f"hash-{name}").id
        if name != "c":
            db_module.set_job_import_meta(conn, jobs[name], "imp-1", None)
    conn.close()

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "--config", str(_config_file(tmp_path, artifacts, db_file)),
            "job", "remove", "--import-id", "imp-1",
        ],
    )
    assert result.exit_code == 0
    assert "removed 2 jobs (--import-id imp-1)" in result.stdout

    conn = connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM removals").fetchone()[0] == 2
    remaining = conn.execute("SELECT id FROM jobs").fetchall()
    assert [r["id"] for r in remaining] == [jobs["c"]]
    conn.close()
    assert not clips["a"].exists()
    assert not clips["b"].exists()
    assert clips["c"].exists()


def test_remove_bulk_empty_selection(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    conn.close()

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "remove", "--source", "photos"]
    )
    assert result.exit_code == 0
    assert "no photos-imported jobs found" in result.stdout

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "remove", "--import-id", "missing-imp"]
    )
    assert result.exit_code == 0
    assert "no jobs with import-id missing-imp" in result.stdout


def test_remove_bulk_selector_errors(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    job_id = _seed_job(tmp_path, conn, "/outside/video.mp4")
    conn.close()

    result = runner.invoke(app, ["--db", str(db_file), "job", "remove"])
    assert result.exit_code == 1
    assert "Error:" in result.output

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "job", "remove", str(job_id), "--import-id", "imp-1",
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "remove", "--source", "icloud"]
    )
    assert result.exit_code == 1
    assert "unknown source 'icloud'" in result.output


def test_removals_forget_allows_reimport(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)

    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    clip = golden_variant(card / "EVENT" / "250807121252.MP4", b"removed-clip")
    h = video_hash(clip)
    db_module.insert_removal(conn, 5, h, str(clip), "personal")
    conn.close()

    cfg = Config()
    cfg.storage.db_path = str(db_file)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    cfg.import_.preflight_gb = 0

    report = run_import(card, cfg, connect(str(db_file)))
    assert report.imported == 0
    assert report.skipped_removed == 1

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "removals", "--forget", h]
    )
    assert result.exit_code == 0
    assert f"forgot 1 removal(s) for hash {h}" in result.stdout

    result = runner.invoke(
        app, ["--db", str(db_file), "job", "removals", "--forget", h]
    )
    assert result.exit_code == 1
    assert f"no removals for hash {h}" in result.output

    report = run_import(card, cfg, connect(str(db_file)))
    assert report.imported == 1
    assert report.skipped_removed == 0


def test_import_allow_removed_overrides_gate(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)

    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    clip = golden_variant(card / "EVENT" / "250807121252.MP4", b"unremoved-clip")
    db_module.insert_removal(conn, 5, video_hash(clip), str(clip), "personal")
    conn.close()

    cfg = Config()
    cfg.storage.db_path = str(db_file)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    cfg.import_.preflight_gb = 0

    report = run_import(card, cfg, connect(str(db_file)), allow_removed=True)
    assert report.imported == 1
    assert report.skipped_removed == 0


def test_import_dry_run_cli_summary(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    golden_variant(card / "EVENT" / "250807121252.MP4", b"dry-run-clip")
    conn.close()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    result = runner.invoke(
        app,
        [
            "--db", str(db_file),
            "--config", str(_config_file(tmp_path, artifacts, db_file)),
            "import", str(card), "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "would import 1 clips (~0.0 GB)" in result.stdout
    assert "skipped 0 (already imported)" in result.stdout

    conn = connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()
    assert not list(artifacts.rglob("*"))


def test_import_skips_removed_hash(tmp_path: Path) -> None:
    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)

    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    clip = golden_variant(card / "EVENT" / "250807121252.MP4", b"removed-clip")
    db_module.insert_removal(conn, 5, video_hash(clip), str(clip), "personal")
    conn.close()

    cfg = Config()
    cfg.storage.db_path = str(db_file)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    cfg.import_.preflight_gb = 0

    report = run_import(card, cfg, connect(str(db_file)))
    assert report.imported == 0
    assert report.skipped_removed == 1
    assert report.skipped == 0
