from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.golden import golden_clip
from video_security import db
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
            "event_type": "hard_brake",
            "description": "g-sensor hard brake",
            "confidence": "high",
            "evidence_rationale": "longitudinal g spike",
            "recommended_action": "log_only",
        }

    def model_digest(self, model: str) -> str:
        return "sha256:fake"


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def _checksum(body: str) -> str:
    v = 0
    for ch in body:
        v ^= ord(ch)
    return f"{v:02X}"


def _line(body: str) -> str:
    return f"${body}*{_checksum(body)}"


def _nmea_with_hard_brake(start: datetime) -> str:
    lines: list[str] = []
    for i in range(40):
        t = start + timedelta(seconds=i)
        lines.append(
            _line(
                f"GPRMC,{t.strftime('%H%M%S')}.00,A,3539.28502,N,"
                f"14001.73333,E,4.3,41.0,{t.strftime('%d%m%y')},,,A"
            )
        )
        ay = "-0.6" if 4 <= i <= 6 else "-0.05"
        lines.append(_line(f"GSENS,0.02,{ay},-1.0"))
    return "\n".join(lines) + "\n"


def test_analyze_with_nmea_sidecar(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    start = datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)
    (tmp_path / "g.NMEA").write_text(_nmea_with_hard_brake(start))
    job, _ = enqueue_video(db_conn, clip)
    db_conn.execute(
        "UPDATE jobs SET recording_start_utc = ? WHERE id = ?",
        (start.isoformat(), job.id),
    )
    db_conn.commit()
    fresh = db.get_job_by_hash(db_conn, job.video_hash)
    assert fresh is not None
    job = fresh

    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    report = analyze_video(job, config, db_conn, client=FakeOllama())

    gps_rows = db_conn.execute(
        "SELECT * FROM clip_gps_data WHERE job_id = ? ORDER BY time_sec",
        (job.id,),
    ).fetchall()
    assert len(gps_rows) >= 40
    assert gps_rows[0]["lat"] is not None
    assert gps_rows[0]["speed_kmh"] is not None
    ay_rows = [r for r in gps_rows if r["ay"] is not None]
    assert len(ay_rows) >= 39

    events = db_conn.execute(
        "SELECT * FROM events WHERE job_id = ? AND event_type = 'hard_brake'",
        (job.id,),
    ).fetchall()
    assert len(events) == 1
    ev = events[0]
    assert ev["detector_score"] > 0.35
    assert ev["priority"] == 0.8
    assert ev["track_id"] is None
    assert 3.5 <= ev["start_sec"] <= 5.5
    assert ev["keyframes_json"] not in (None, "", "[]")

    assert report.job_id == job.id


def test_analyze_without_sidecar_no_gps_rows(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    analyze_video(job, config, db_conn, no_llm=True)
    rows = db_conn.execute("SELECT * FROM clip_gps_data").fetchall()
    assert rows == []


def test_rear_clip_uses_front_twin_nmea(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    rear_dir = tmp_path / "clips" / "NORMAL" / "rear"
    rear_dir.mkdir(parents=True)
    clip = golden_clip(rear_dir / "g.mp4")
    start = datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)
    front_dir = tmp_path / "clips" / "NORMAL" / "front"
    front_dir.mkdir(parents=True)
    (front_dir / "g.NMEA").write_text(_nmea_with_hard_brake(start))
    job, _ = enqueue_video(db_conn, clip)
    db_conn.execute(
        "UPDATE jobs SET recording_start_utc = ? WHERE id = ?",
        (start.isoformat(), job.id),
    )
    db_conn.commit()
    fresh = db.get_job_by_hash(db_conn, job.video_hash)
    assert fresh is not None
    job = fresh

    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    analyze_video(job, config, db_conn, client=FakeOllama())

    gps_rows = db_conn.execute(
        "SELECT COUNT(*) FROM clip_gps_data WHERE job_id = ?", (job.id,)
    ).fetchone()[0]
    assert gps_rows >= 40
