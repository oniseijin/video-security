from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import create_app


def _make_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def video_client(tmp_path: Path) -> Iterator[TestClient]:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    video = tmp_path / "clip.mp4"
    _make_mp4(video)
    db_path = tmp_path / "t.db"
    conn = connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (id, video_path, video_hash, status) "
        "VALUES (1, ?, 'h1', 'done')",
        (str(video),),
    )
    conn.commit()
    conn.close()
    cfg = Config()
    cfg.storage.db_path = str(db_path)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    yield TestClient(create_app(cfg))


def _fetch(
    client: TestClient, path: str, headers: dict[str, str] | None = None
) -> Any:
    return client.get(path, headers=headers or {})


def test_video_full(video_client: TestClient, tmp_path: Path) -> None:
    resp = _fetch(video_client, "/api/jobs/1/video")
    assert resp.status_code == 200
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.headers["Content-Type"] == "video/mp4"
    assert len(resp.content) == (tmp_path / "clip.mp4").stat().st_size


def test_video_range_exact(video_client: TestClient, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=0-1023"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 0-1023/{total}"
    assert resp.headers["Content-Length"] == "1024"
    assert resp.content == (tmp_path / "clip.mp4").read_bytes()[:1024]


def test_video_range_suffix_and_open(video_client: TestClient, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=-100"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes {total - 100}-{total - 1}/{total}"
    assert resp.headers["Content-Length"] == "100"
    assert len(resp.content) == 100
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=10-"})
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 10-{total - 1}/{total}"
    assert resp.headers["Content-Length"] == str(total - 10)
    assert len(resp.content) == total - 10


def test_video_range_invalid(video_client: TestClient) -> None:
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=abc"})
    assert resp.status_code == 416
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=99999999-"})
    assert resp.status_code == 416


def test_video_head(video_client: TestClient, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    resp = video_client.head("/api/jobs/1/video")
    assert resp.status_code == 200
    assert resp.headers["Content-Length"] == str(total)
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.content == b""
    resp = video_client.head("/api/jobs/1/video", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers["Content-Length"] == "100"
    assert resp.content == b""


def test_video_missing(video_client: TestClient) -> None:
    resp = _fetch(video_client, "/api/jobs/999/video")
    assert resp.status_code == 404
    assert resp.json()["error"] == "job not found"


def test_video_no_rear_pair(video_client: TestClient) -> None:
    resp = _fetch(video_client, "/api/jobs/1/video?channel=rear")
    assert resp.status_code == 404


def test_video_range_middle_slice(video_client: TestClient, tmp_path: Path) -> None:
    resp = _fetch(video_client, "/api/jobs/1/video", {"Range": "bytes=5-9"})
    assert resp.status_code == 206
    assert resp.content == (tmp_path / "clip.mp4").read_bytes()[5:10]
