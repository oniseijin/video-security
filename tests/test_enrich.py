from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from video_security.db import (
    JobRow,
    connect,
    create_job,
    init_db,
    insert_analysis_result,
    insert_event,
    insert_gps_row,
)
from video_security.enrich import (
    clip_mode,
    event_category,
    event_description,
    format_coord,
    gps_track,
    nearest_gps,
    scene_description,
)


@pytest.fixture
def db_and_job(tmp_path: Path) -> tuple[sqlite3.Connection, JobRow, Path]:
    db_file = str(tmp_path / "test.db")
    conn = connect(db_file)
    init_db(conn)
    job = create_job(conn, "/videos/test.mp4", "abc123")
    return conn, job, tmp_path


def test_clip_mode_dash_modes() -> None:
    assert clip_mode("/clips/20260920/NORMAL/front/x.MP4") == "NORMAL"
    assert clip_mode("/clips/EVENT/rear/y.MP4") == "EVENT"
    assert clip_mode("/clips/PARKING/z.MP4") == "PARKING"
    assert clip_mode("/clips/MANUAL/a.MP4") == "MANUAL"
    assert clip_mode("/clips/PICTURE/b.JPG") == "PICTURE"


def test_clip_mode_lowercase() -> None:
    assert clip_mode("/clips/normal/front/x.MP4") == "NORMAL"


def test_clip_mode_none() -> None:
    assert clip_mode("/videos/test.mp4") is None
    assert clip_mode("/foo/bar/baz.mp4") is None


def test_event_category_parking() -> None:
    assert event_category("PARKING", None) == "parking"


def test_event_category_driving(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 139.0, 60.0, 90.0, None, None, None)
    conn.commit()
    track = gps_track(conn, job.id)
    gps = track[0]
    assert event_category("NORMAL", gps) == "driving"
    conn.close()


def test_event_category_stationary(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 139.0, 2.0, 90.0, None, None, None)
    conn.commit()
    track = gps_track(conn, job.id)
    gps = track[0]
    assert event_category("NORMAL", gps) == "stationary"
    conn.close()


def test_event_category_unknown() -> None:
    assert event_category(None, None) == "unknown"


def test_scene_description_driving(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 139.0, 60.0, 90.0, None, None, None)
    conn.commit()
    track = gps_track(conn, job.id)
    gps = track[0]
    evt = conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, detector_score) "
        "VALUES (?, 'intrusion', 10.0, 15.0, 0, 0.85) RETURNING *",
        (job.id,),
    ).fetchone()
    desc = scene_description(evt, gps, "driving", 0, [])
    assert "Vehicle driving" in desc
    assert "km/h" in desc
    conn.close()


def test_scene_description_parking(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    evt = conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, detector_score) "
        "VALUES (?, 'suspicious_behavior', 5.0, 9.0, 0, 0.5) RETURNING *",
        (job.id,),
    ).fetchone()
    desc = scene_description(evt, None, "parking", 0, [])
    assert "Parked vehicle" in desc
    conn.close()


def test_scene_description_hard_corner_with_gforces(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 139.0, 30.0, 90.0, 0.4, 0.3, None)
    conn.commit()
    track = gps_track(conn, job.id)
    gps = track[0]
    evt = conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, detector_score) "
        "VALUES (?, 'hard_corner', 5.0, 7.0, 0, 0.7) RETURNING *",
        (job.id,),
    ).fetchone()
    desc = scene_description(evt, gps, "driving", 0, [])
    assert "lateral" in desc
    assert "g" in desc
    conn.close()


def test_nearest_gps_empty() -> None:
    assert nearest_gps([], 5.0) is None


def test_nearest_gps_single(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 139.0, 50.0, 90.0, None, None, None)
    conn.commit()
    track = gps_track(conn, job.id)
    result = nearest_gps(track, 0.0)
    assert result is not None
    assert result["time_sec"] == 0.0
    conn.close()


def test_nearest_gps_best_match(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    for i in range(5):
        insert_gps_row(
            conn, job.id, 0, float(i * 10), 35.0, 139.0, float(i * 20), 90.0,
            None, None, None,
        )
    conn.commit()
    track = gps_track(conn, job.id)
    result = nearest_gps(track, 12.0)
    assert result is not None
    assert result["time_sec"] == 10.0
    conn.close()


def test_format_coord(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    insert_gps_row(
        conn, job.id, 0, 5.0, 35.61050, 139.73950, 45.0, 90.0, None, None, None,
    )
    conn.commit()
    track = gps_track(conn, job.id)
    assert format_coord(track[0]) == "35.61050, 139.73950"
    conn.close()


def test_event_description_with_llm(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    evt_id = insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None, "[]", 0.85, 0.9,
    )
    ar_id = insert_analysis_result(
        conn, job.id, evt_id, "md5", "v1", "detail",
        '{"relevant": true, "description": "test description"}', 0.9, 0, None,
    )
    conn.execute("UPDATE events SET llm_result_id = ? WHERE id = ?", (ar_id, evt_id))
    conn.commit()
    evt = conn.execute("SELECT * FROM events WHERE id = ?", (evt_id,)).fetchone()
    desc = event_description(conn, evt)
    assert desc == "test description"
    conn.close()


def test_event_description_without_llm(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    evt_id = insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None, "[]", 0.85, 0.9,
    )
    conn.commit()
    evt = conn.execute("SELECT * FROM events WHERE id = ?", (evt_id,)).fetchone()
    assert event_description(conn, evt) == ""
    conn.close()


def test_gps_track(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, _tmp = db_and_job
    for i in range(3):
        insert_gps_row(
            conn, job.id, 0, float(i), 35.0 + i * 0.1, 139.0 - i * 0.1,
            50.0, 90.0, None, None, None,
        )
    conn.commit()
    track = gps_track(conn, job.id)
    assert len(track) == 3
    assert track[0]["time_sec"] == 0.0
    assert track[2]["time_sec"] == 2.0
    conn.close()