from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_security.db import (
    JobRow,
    connect,
    create_job,
    init_db,
    insert_analysis_result,
    insert_event,
    insert_frame_text,
    insert_plate,
    insert_transcript_segment,
    insert_vehicle_track,
)
from video_security.report import ReportError, generate_report


@pytest.fixture
def db_and_job(tmp_path: Path) -> tuple[sqlite3.Connection, JobRow, Path]:
    db_file = str(tmp_path / "test.db")
    conn = connect(db_file)
    init_db(conn)
    job = create_job(conn, "/videos/test.mp4", "abc123")
    return conn, job, tmp_path


def test_generate_report(db_and_job: tuple[sqlite3.Connection, JobRow, Path]) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    kf1 = kf_dir / "kf1.jpg"
    kf2 = kf_dir / "kf2.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf1), img)
    cv2.imwrite(str(kf2), img)

    evt1_id = insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf1), str(kf2)]), 0.85, 0.9,
    )
    ar_id = insert_analysis_result(
        conn, job.id, evt1_id, "md5", "v1", "detail",
        json.dumps({"relevant": True, "description": "suspicious person detected"}), 0.9, 0, None,
    )
    conn.execute("UPDATE events SET llm_result_id = ? WHERE id = ?", (ar_id, evt1_id))

    insert_event(
        conn, job.id, "audio_distress", 20.0, 22.0, 0, None,
        "[]", 0.7, 0.6,
    )

    insert_plate(
        conn, job.id, 1, 0, "YOLO42", "YOLO42", 0.95, 100,
        json.dumps(["YOLO42"]),
    )

    insert_transcript_segment(
        conn, job.id, 0, 0, 5.0, 10.0, "hello world", "en",
    )

    insert_vehicle_track(
        conn, job.id, 1, 0, 0, 100, 0.3, "E",
    )

    insert_frame_text(
        conn, job.id, 0, 50, "SOME TEXT", "signage", None, 0.8,
    )

    artifact_dir = tmp_path / "art"
    p = generate_report(conn, job.id, artifact_dir)
    assert p.exists()
    content = p.read_text()
    assert "intrusion" in content
    assert "audio_distress" in content
    assert "YOLO42" in content
    assert "suspicious person detected" in content
    assert content.count("<table>") >= 4
    assert content.count("<img") == 2
    assets = list((artifact_dir / "reports" / f"job_{job.id}_assets").iterdir())
    assert len(assets) == 2
    conn.close()


def test_report_missing_job(db_and_job: tuple[sqlite3.Connection, JobRow, Path]) -> None:
    conn, _job, tmp_path = db_and_job
    with pytest.raises(ReportError, match="job 999 not found"):
        generate_report(conn, 999, tmp_path / "art")
    conn.close()


def test_report_escapes(db_and_job: tuple[sqlite3.Connection, JobRow, Path]) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    kf = kf_dir / "kf.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    evt_id = insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
    )
    ar_id = insert_analysis_result(
        conn, job.id, evt_id, "md5", "v1", "detail",
        json.dumps({"relevant": True, "description": "alert <script>alert(1)</script>"}),
        0.9, 0, None,
    )
    conn.execute("UPDATE events SET llm_result_id = ? WHERE id = ?", (ar_id, evt_id))

    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert "&lt;script&gt;" in content
    assert "<script>" not in content
    conn.close()


def test_report_skips_missing_keyframes(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    kf = kf_dir / "kf.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    nonexistent = str(kf_dir / "ghost.jpg")
    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf), nonexistent]), 0.85, 0.9,
    )

    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert p.exists()
    assert "<img" in content
    conn.close()