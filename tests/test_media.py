from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import WebServer


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
def video_url(tmp_path: Path) -> Iterator[str]:
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
    server = WebServer(("127.0.0.1", 0), cfg)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{int(server.server_address[1])}"
    server.shutdown()
    server.server_close()


def _fetch(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        return urllib.request.urlopen(req)
    except urllib.error.HTTPError as exc:
        return exc


def test_video_full(video_url: str, tmp_path: Path) -> None:
    resp = _fetch(f"{video_url}/api/jobs/1/video")
    assert resp.status == 200
    assert resp.headers["Accept-Ranges"] == "bytes"
    data = resp.read()
    assert len(data) == (tmp_path / "clip.mp4").stat().st_size


def test_video_range_exact(video_url: str, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    resp = _fetch(
        f"{video_url}/api/jobs/1/video", {"Range": "bytes=0-1023"}
    )
    assert resp.status == 206
    assert resp.headers["Content-Range"] == f"bytes 0-1023/{total}"
    assert resp.headers["Content-Length"] == "1024"
    data = resp.read()
    assert len(data) == 1024
    assert data == (tmp_path / "clip.mp4").read_bytes()[:1024]


def test_video_range_suffix_and_open(video_url: str, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    resp = _fetch(f"{video_url}/api/jobs/1/video", {"Range": "bytes=-100"})
    assert resp.status == 206
    assert resp.headers["Content-Range"] == f"bytes {total - 100}-{total - 1}/{total}"
    assert len(resp.read()) == 100
    resp = _fetch(f"{video_url}/api/jobs/1/video", {"Range": "bytes=10-"})
    assert resp.status == 206
    assert resp.headers["Content-Range"] == f"bytes 10-{total - 1}/{total}"
    assert len(resp.read()) == total - 10


def test_video_range_invalid(video_url: str) -> None:
    resp = _fetch(f"{video_url}/api/jobs/1/video", {"Range": "bytes=abc"})
    assert resp.status == 416
    resp = _fetch(f"{video_url}/api/jobs/1/video", {"Range": "bytes=99999999-"})
    assert resp.status == 416


def test_video_head(video_url: str, tmp_path: Path) -> None:
    total = (tmp_path / "clip.mp4").stat().st_size
    req = urllib.request.Request(
        f"{video_url}/api/jobs/1/video", method="HEAD"
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Length"] == str(total)
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.read() == b""
    req = urllib.request.Request(
        f"{video_url}/api/jobs/1/video", method="HEAD", headers={"Range": "bytes=0-99"}
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers["Content-Length"] == "100"
        assert resp.read() == b""


def test_video_missing(video_url: str) -> None:
    resp = _fetch(f"{video_url}/api/jobs/999/video")
    assert resp.status == 404
    body = json.loads(resp.read().decode("utf-8"))
    assert body["error"] == "job not found"


def test_video_no_rear_pair(video_url: str) -> None:
    resp = _fetch(f"{video_url}/api/jobs/1/video?channel=rear")
    assert resp.status == 404
