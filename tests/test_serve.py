from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import WebServer, open_readonly


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
def base_url(tmp_path: Path) -> Iterator[str]:
    db_path = tmp_path / "t.db"
    _seed(db_path)
    cfg = Config()
    cfg.storage.db_path = str(db_path)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    server = WebServer(("127.0.0.1", 0), cfg)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{int(server.server_address[1])}"
    server.shutdown()
    server.server_close()


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _status_of(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_health(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/health")
    assert data["ok"] is True
    assert data["db"] == "readonly"
    assert isinstance(data["version"], str)


def test_stats(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/stats")
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


def test_unknown_api_404(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/api/nope")
    assert status == 404
    assert json.loads(body)["error"] == "not found"


def test_traversal_blocked(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/..%2f..%2f..%2fetc%2fpasswd")
    assert status in (200, 404)
    assert "root:" not in body


def test_root_serves_html(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/")
    assert status == 200
    assert "<html" in body.lower()


def test_spa_fallback(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/jobs")
    assert status == 200
    assert "<html" in body.lower()


def test_readonly_rejects_writes(base_url: str, tmp_path: Path) -> None:
    conn = open_readonly(str(tmp_path / "t.db"))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO jobs (video_path, video_hash) VALUES ('x', 'y')")
    conn.close()
