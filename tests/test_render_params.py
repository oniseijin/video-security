from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_security import geo
from video_security.db import (
    JobRow,
    connect,
    create_job,
    init_db,
    insert_event,
    insert_gps_row,
)
from video_security.report import render_report_html


@pytest.fixture
def db_and_job(tmp_path: Path) -> tuple[sqlite3.Connection, JobRow, Path]:
    db_file = str(tmp_path / "test.db")
    conn = connect(db_file)
    init_db(conn)
    job = create_job(conn, "/videos/test.mp4", "abc123")
    return conn, job, tmp_path


def test_embed_suppresses_toggles(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "frames" / str(job.id)
    kf_dir.mkdir(parents=True)
    kf = kf_dir / "event_1_0.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
        faces_json="[[[0.1, 0.2, 0.3, 0.4]]]",
    )
    conn.commit()

    html = render_report_html(conn, job.id, tmp_path, embed=True)
    assert 'id="theme-toggle"' not in html
    assert 'id="face-toggle"' not in html
    assert 'id="lightbox"' in html
    assert 'face-box' in html
    conn.close()


def test_embed_non_embed_has_toggles(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "frames" / str(job.id)
    kf_dir.mkdir(parents=True)
    kf = kf_dir / "event_1_0.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
        faces_json="[[[0.1, 0.2, 0.3, 0.4]]]",
    )
    conn.commit()

    html = render_report_html(conn, job.id, tmp_path, embed=False)
    assert 'id="theme-toggle"' in html
    assert 'id="face-toggle"' in html
    conn.close()


def test_media_base(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "frames" / str(job.id)
    kf_dir.mkdir(parents=True)
    kf = kf_dir / "event_1_0.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
    )
    conn.commit()

    html = render_report_html(conn, job.id, tmp_path, media_base="/media")
    assert f'src="/media/frames/{job.id}/event_1_0.jpg"' in html
    conn.close()


def test_media_base_none_uses_relative(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "frames" / str(job.id)
    kf_dir.mkdir(parents=True)
    kf = kf_dir / "event_1_0.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    insert_event(
        conn, job.id, "intrusion", 10.0, 15.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
    )
    conn.commit()

    html = render_report_html(conn, job.id, tmp_path, media_base=None)
    assert f'../frames/{job.id}/event_1_0.jpg' in html
    conn.close()


def test_geocode_network_false_uses_cached(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, job, tmp_path = db_and_job

    kf_dir = tmp_path / "frames" / str(job.id)
    kf_dir.mkdir(parents=True)
    kf = kf_dir / "event_1_0.jpg"
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.imwrite(str(kf), img)

    for i in range(3):
        insert_gps_row(
            conn, job.id, 0, float(i), 35.0 + i * 0.01, 139.0 - i * 0.01,
            50.0, 90.0, None, None, None,
        )
    insert_event(
        conn, job.id, "intrusion", 1.0, 2.0, 0, None,
        json.dumps([str(kf)]), 0.85, 0.9,
    )
    conn.commit()

    geo.ensure_table(conn)
    monkeypatch.setattr(
        geo, "reverse_geocode",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("network call")),
    )
    html = render_report_html(conn, job.id, tmp_path, geocode=True, geocode_network=False)
    assert "network call" not in html
    conn.close()

def test_carto_key_injected_into_map(
    db_and_job: tuple[sqlite3.Connection, JobRow, Path],
) -> None:
    conn, job, tmp_path = db_and_job
    insert_gps_row(conn, job.id, 0, 0.0, 35.0, 140.0, 10.0, None, None, None, None)
    insert_gps_row(
        conn, job.id, 0, 2.0, 35.001, 140.001, 10.0, None, None, None, None
    )
    html = render_report_html(conn, job.id, tmp_path, carto_api_key="cb1_test_key")
    assert 'data-carto-key="cb1_test_key"' in html
    plain = render_report_html(conn, job.id, tmp_path)
    assert 'data-carto-key="' not in plain
