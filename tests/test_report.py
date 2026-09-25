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
    insert_gps_row,
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
        conn, job.id, "intrusion", 10.0, 15.0, 0, 7,
        json.dumps([str(kf1), str(kf2)]), 0.85, 0.9,
        faces_json="[[[0.1, 0.2, 0.3, 0.4]], []]",
    )
    conn.execute(
        "INSERT INTO persons (id, name, sightings) VALUES (1, 'Mika', 1)"
    )
    conn.execute(
        "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
        "crop_path, quality, person_id) VALUES "
        f"({job.id}, {evt1_id}, 0, 0, '/faces/x.jpg', 0.9, 1)"
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
        conn, job.id, 7, 0, "YOLO42", "YOLO42", 0.95, 100,
        json.dumps(["YOLO42"]),
        crop_path=str(tmp_path / "art" / "plates" / str(job.id) / "track_7.jpg"),
        crop_src=str(tmp_path / "art" / "frames" / str(job.id) / "track_7_src.jpg"),
        crop_box=json.dumps([0.2, 0.3, 0.4, 0.1]),
    )
    insert_plate(
        conn, job.id, 2, 0, "習志野 れ 12-08", "習志野れ1208", 0.9, 110,
        json.dumps(["習志野 れ 12-08"]),
    )

    for i in range(10):
        speed = 12.0 if i < 6 else 2.0
        insert_gps_row(
            conn, job.id, 0, i * 2.0, 35.61 + i * 0.0001, 139.74 - i * 0.0001,
            speed, 90.0, None, None, None,
        )
    conn.commit()

    insert_transcript_segment(
        conn, job.id, 0, 0, 5.0, 10.0, "hello world", "en",
    )

    insert_vehicle_track(
        conn, job.id, 1, 0, 0, 100, 0.3, "E",
    )
    insert_vehicle_track(
        conn, job.id, 7, 0, 0, 350, None, "E",
    )

    insert_frame_text(
        conn, job.id, 0, 50, "SOME TEXT", "signage", None, 0.8,
    )

    artifact_dir = tmp_path / "art"
    plates_dir = artifact_dir / "plates" / str(job.id)
    plates_dir.mkdir(parents=True, exist_ok=True)
    (plates_dir / "track_7.jpg").write_bytes(b"fakejpg")
    src_dir = artifact_dir / "frames" / str(job.id)
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "track_7_src.jpg").write_bytes(b"fakejpg")
    p = generate_report(conn, job.id, artifact_dir)
    assert p.exists()
    content = p.read_text()
    assert "intrusion" in content
    assert "audio_distress" in content
    assert "YOLO42" in content
    assert "習志野" in content
    assert 'class="plate-crop"' in content
    assert "data-full=" in content
    assert "plate-box" in content
    assert "Chiba" in content
    assert "suspicious person detected" in content
    assert "Mika" in content
    assert content.count("<table") >= 4
    assert content.count("<img") == 4
    assert 'data-theme="machine"' in content
    assert 'id="theme-toggle"' in content
    assert "subject subject--threat" in content
    assert '<tr class="row-suppressed">' not in content
    assert 'id="lightbox"' in content
    assert "lightbox-zoom" in content
    assert "lightbox-controls" in content
    assert 'data-zoom="in"' in content
    assert "cursor: zoom-in" in content
    assert "../keyframes/kf1.jpg" in content
    assert 'id="face-toggle"' in content
    assert "Faces On" in content
    assert "face-box" in content
    assert f'href="#event-{evt1_id}"' in content
    assert 'id="event-' in content
    assert "RECORDED" not in content
    assert "Archive import" not in content
    assert "<th>Category</th>" in content
    assert ">driving<" in content
    assert ">stationary<" in content
    assert "35.61050, 139.73950" in content
    assert "gps-svg" in content
    assert "Location Track" in content
    assert "km/h" in content
    assert "Vehicle stationary" in content
    assert "Read At" in content
    assert ">#7<" in content
    assert "tracked vehicle ID" in content
    assert '<th class="num">Vehicle</th>' in content
    assert "<th>Active</th>" in content
    assert 'title="frames 0-100"' in content
    assert "0:00&ndash;0:03" in content
    assert f'<a href="#event-{evt1_id}" title="view capture">{evt1_id}</a>' in content
    assert 'id="gps-map"' in content
    assert 'id="gps-data"' in content
    assert "leaflet@1.9.4" in content
    assert "nominatim" not in content
    assert not (artifact_dir / "reports" / f"job_{job.id}_assets").exists()
    conn.close()


def test_report_parking_mode_category(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job
    conn.execute(
        "UPDATE jobs SET video_path = ? WHERE id = ?",
        ("/clips/20260920/PARKING/front/x.MP4", job.id),
    )
    insert_event(
        conn, job.id, "suspicious_behavior", 5.0, 9.0, 0, None, "[]", 0.5, 0.3,
    )
    conn.commit()
    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert ">parking<" in content
    assert "Parked vehicle" in content
    conn.close()


def test_report_recording_vs_import_dates(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    kf = kf_dir / "kf.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
    )
    conn.execute(
        "UPDATE jobs SET recording_start_utc = ?, created_at = ? WHERE id = ?",
        ("2025-08-07T03:12:52+00:00", "2026-09-20 01:51:00", job.id),
    )
    conn.commit()

    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert "recorded 2025-08-07 03:12 UTC" in content
    assert "imported 2026-09-20" in content
    assert "Archive import" in content
    assert "days later" in content
    assert "<th>Recorded</th>" in content
    assert "2025-08-07 03:13:02" in content
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
    assert "<script>" in content
    assert "<script>alert" not in content
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


def test_event_tone() -> None:
    from video_security.report_theme import event_tone

    assert event_tone("impact") == "threat"
    assert event_tone("hard_corner") == "threat"
    assert event_tone("intrusion") == "threat"
    assert event_tone("suspicious_behavior") == "warning"
    assert event_tone("loitering") == "warning"
    assert event_tone("plate_capture") == "info"
    assert event_tone("person") == "asset"
    assert event_tone("motion") == "asset"


def test_report_device_line(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job
    conn.execute(
        "UPDATE jobs SET metadata_json = ? WHERE id = ?",
        ('{"device_kind": "meta_glasses", "device_model": "Meta"}', job.id),
    )
    conn.commit()
    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert "Device: meta glasses &middot; Meta" in content
    conn.close()


def test_report_no_device_line(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job
    p = generate_report(conn, job.id, tmp_path / "art")
    content = p.read_text()
    assert "Device:" not in content
    conn.close()