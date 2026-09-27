from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import create_app, open_readonly


def _seed(db_path: Path) -> None:
    conn = connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash, status, import_id, current_stage) "
        "VALUES ('/v/a.mp4', 'h1', 'done', 'imp-1', 'done'), "
        "('/v/b.mp4', 'h2', 'pending', 'imp-1', 'pending'), "
        "('/v/c.mp4', 'h3', 'filtering', 'imp-1', 'filtering')"
    )
    conn.execute(
        "INSERT INTO events (job_id, event_type, start_sec, end_sec, clip_id, "
        "detector_score, priority, status) "
        "VALUES (1, 'intrusion', 1.0, 2.0, 0, 0.9, 0.9, 'detailed')"
    )
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, norm_text, crop_path) "
        "VALUES (1, 1, 0, 'X1', '/tmp/x.jpg'), (1, 2, 0, 'Y2', NULL)"
    )
    conn.execute(
        "INSERT INTO frames (job_id, clip_id, frame_number, timestamp_sec) "
        "VALUES (1, 0, 1, 0.03)"
    )
    conn.execute(
        "INSERT INTO transcript_segments (job_id, clip_id, segment_id, "
        "start_time, end_time, text) VALUES (1, 0, 1, 0.0, 1.0, 'hello')"
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "t.db"
    _seed(db_path)
    cfg = Config()
    cfg.storage.db_path = str(db_path)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    yield TestClient(create_app(cfg))


def _get_json(client: TestClient, path: str) -> Any:
    resp = client.get(path)
    assert resp.status_code == 200
    return resp.json()


def _status_of(client: TestClient, path: str) -> tuple[int, str]:
    resp = client.get(path)
    return resp.status_code, resp.text


def test_health(client: TestClient) -> None:
    data = _get_json(client, "/api/health")
    assert data["ok"] is True
    assert data["db"] == "readonly"
    assert isinstance(data["version"], str)


def test_stats(client: TestClient) -> None:
    data = _get_json(client, "/api/stats")
    assert data["jobs"]["total"] == 3
    assert data["jobs"]["by_status"] == {"done": 1, "pending": 1, "filtering": 1}
    assert data["events"] == 1
    assert data["plates"] == 2
    assert data["plates_with_crops"] == 1
    assert data["frames_kept"] == 1
    assert data["transcript_segments"] == 1
    assert data["active_job"]["id"] == 3
    assert data["active_job"]["stage"] == "filtering"
    assert data["storage"]["artifact_dir"].endswith("artifacts")
    assert len(data["imports"]) == 1
    assert data["imports"][0]["jobs"] == 3
    assert data["imports"][0]["done"] == 1


def test_unknown_api_404(client: TestClient) -> None:
    status, body = _status_of(client, "/api/nope")
    assert status == 404
    assert json.loads(body)["error"] == "not found"


def test_unknown_media_404(client: TestClient) -> None:
    status, body = _status_of(client, "/media/nope")
    assert status == 404
    assert json.loads(body)["error"] == "not found"


def test_traversal_blocked(client: TestClient) -> None:
    status, body = _status_of(client, "/..%2f..%2f..%2fetc%2fpasswd")
    assert status in (200, 404)
    assert "root:" not in body


def test_root_serves_html(client: TestClient) -> None:
    status, body = _status_of(client, "/")
    assert status == 200
    assert "<html" in body.lower()


def test_spa_fallback(client: TestClient) -> None:
    status, body = _status_of(client, "/jobs")
    assert status == 200
    assert "<html" in body.lower()


def test_head_matches_get_without_body(client: TestClient) -> None:
    resp = client.head("/api/health")
    assert resp.status_code == 200
    assert resp.content == b""
    resp = client.head("/jobs")
    assert resp.status_code == 200
    assert resp.content == b""


def test_post_is_405(client: TestClient) -> None:
    assert client.post("/api/jobs").status_code == 405


def test_readonly_rejects_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    _seed(db_path)
    conn = open_readonly(str(db_path))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO jobs (video_path, video_hash) VALUES ('x', 'y')")
    conn.close()
