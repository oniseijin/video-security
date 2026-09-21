from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.golden import golden_clip
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.llm.ollama import OllamaClient
from video_security.pipeline import analyze_video, enqueue_video


class FakeOllama(OllamaClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        return {
            "relevant": True,
            "event_type": "suspicious_behavior",
            "description": "person present",
            "confidence": "high",
            "evidence_rationale": "person visible",
            "recommended_action": "log_only",
        }

    def model_digest(self, model: str) -> str:
        return "sha256:fake"


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def test_enqueue_dedup(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    job1, created1 = enqueue_video(db_conn, clip)
    assert created1
    job2, created2 = enqueue_video(db_conn, clip)
    assert not created2
    assert job1.id == job2.id


def test_analyze_golden_no_llm(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    report = analyze_video(job, config, db_conn, no_llm=True)
    assert report.job_id == job.id
    assert report.kept_frames > 10
    assert report.vehicle_tracks >= 1
    assert report.plates >= 1
    assert report.events >= 1
    assert report.triaged == 0

    rows = db_conn.execute(
        "SELECT status, COUNT(*) FROM events GROUP BY status"
    ).fetchall()
    statuses = {r[0]: r[1] for r in rows}
    assert statuses.get("pending", 0) >= 1

    plates = db_conn.execute("SELECT norm_text FROM plates").fetchall()
    assert any(r[0] == "YOLO42" for r in plates)

    job_row = db_conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert job_row[0] == "harvested"
    evidence = db_conn.execute(
        "SELECT evidence_json FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert evidence[0] is not None


def test_analyze_golden_with_mock_llm(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    client = FakeOllama()
    report = analyze_video(job, config, db_conn, client=client)
    assert report.events >= 1
    assert client.calls >= 2
    assert report.detailed >= 1

    detailed = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE status = 'detailed'"
    ).fetchone()[0]
    assert detailed >= 1

    results = db_conn.execute(
        "SELECT analysis_type, model_digest FROM analysis_results"
    ).fetchall()
    types = {r[0] for r in results}
    assert "triage" in types
    assert "detail" in types
    assert all(r[1] == "sha256:fake" for r in results)

    raw = db_conn.execute(
        "SELECT raw_response FROM analysis_results WHERE analysis_type = 'triage'"
    ).fetchone()[0]
    parsed = json.loads(raw)
    assert parsed["relevant"] is True

    kf_events = db_conn.execute(
        "SELECT keyframes_json FROM events WHERE keyframes_json != '[]'"
    ).fetchall()
    assert kf_events
    for (kfj,) in kf_events:
        paths = json.loads(kfj)
        for p in paths:
            assert Path(p).exists()


def test_analyze_missing_video(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    real = tmp_path / "gone.mp4"
    real.write_bytes(b"x" * 1024)
    job, _ = enqueue_video(db_conn, real)
    real.unlink()
    from video_security.pipeline import PipelineError

    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    with pytest.raises(PipelineError):
        analyze_video(job, config, db_conn, no_llm=True)


def test_phase_split_flow(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    from video_security.pipeline import detail_job, harvest_job, triage_job

    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")

    report = harvest_job(job, config, db_conn)
    assert report.events >= 1
    row = db_conn.execute(
        "SELECT status, evidence_json FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert row[0] == "harvested"
    evidence = json.loads(row[1])
    assert "event_counts" in evidence
    assert evidence["event_counts"]

    client = FakeOllama()
    n = triage_job(job, config, db_conn, client)
    assert n >= 1
    assert (
        db_conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()[0]
        == "triaged"
    )
    triaged_events = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND status = 'triaged'",
        (job.id,),
    ).fetchone()[0]
    assert triaged_events >= 1

    m = detail_job(job, config, db_conn, client)
    assert m >= 1
    assert (
        db_conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()[0]
        == "done"
    )
    detailed_events = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND status = 'detailed'",
        (job.id,),
    ).fetchone()[0]
    assert detailed_events >= 1


def test_phase_two_skips_harvested_pending(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    from video_security.pipeline import triage_job

    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")

    n = triage_job(job, config, db_conn, FakeOllama())
    assert n == 0
    assert (
        db_conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()[0]
        == "triaged"
    )


def test_evidence_summary_text() -> None:
    from video_security.pipeline import evidence_summary_text

    text = evidence_summary_text(
        {
            "plates": [{"norm": "YOLO42", "confidence": 1.0}],
            "vehicle_tracks": [
                {"direction": "E", "weaving_score": 0.1},
                {"direction": "W", "weaving_score": 0.2},
            ],
            "faces": 3,
            "scene_texts": [{"text": "STOP", "kind": "sign"}],
            "audio": {"segments": 4, "loud_regions": 1, "keyword_hits": 0},
            "event_counts": {"plate_capture": 1},
        }
    )
    assert "YOLO42" in text
    assert "vehicle tracks: 2" in text
    assert "faces detected: 3" in text
    assert "plate_capture x1" in text
    assert evidence_summary_text(None) == ""
    assert evidence_summary_text({}) == ""


def test_evidence_summary_device_context() -> None:
    from video_security.pipeline import evidence_summary_text

    text = evidence_summary_text({"device_context": "first-person smart-glasses bodycam footage"})
    assert "first-person smart-glasses bodycam footage" in text

    text_no = evidence_summary_text({"plates": [{"norm": "ABC", "confidence": 0.9}]})
    assert "device" not in text_no


def test_evidence_includes_device_context_from_metadata(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    from video_security.pipeline import harvest_job

    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    db_conn.execute(
        "UPDATE jobs SET metadata_json = ? WHERE id = ?",
        ('{"device_kind": "meta_glasses",'
         ' "device_make": "Ray-Ban",'
         ' "device_model": "Meta"}', job.id),
    )
    db_conn.commit()

    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    harvest_job(job, config, db_conn)

    evidence = db_conn.execute(
        "SELECT evidence_json FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    assert evidence is not None
    ev_data = json.loads(evidence["evidence_json"])
    assert "device_context" in ev_data
    assert "first-person smart-glasses bodycam footage" in str(ev_data["device_context"])
