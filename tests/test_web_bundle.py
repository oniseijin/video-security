from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import STATIC_DIR, WebServer

INDEX = STATIC_DIR / "index.html"

pytestmark = pytest.mark.skipif(
    not INDEX.is_file(), reason="web bundle not built — run npm --prefix web run build"
)


def _seed(db_path: Path) -> None:
    conn = connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash, status, import_id, current_stage) "
        "VALUES ('/v/a.mp4', 'h1', 'done', 'imp-1', 'done')"
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


def _status_of(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_bundle_index_exists() -> None:
    assert INDEX.is_file()
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="root"' in html
    assert "VS // CONSOLE" in html
    assert "vs-theme" in html


def test_root_serves_bundle(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/")
    assert status == 200
    assert 'id="root"' in body
    assert "VS // CONSOLE" in body


def test_spa_fallback_serves_bundle(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/jobs")
    assert status == 200
    assert 'id="root"' in body


def test_bundle_asset_served(base_url: str) -> None:
    html = INDEX.read_text(encoding="utf-8")
    asset = next(
        line.split('src="')[1].split('"')[0]
        for line in html.splitlines()
        if 'type="module"' in line and "src=" in line
    ).lstrip("./")
    status, body = _status_of(f"{base_url}/{asset}")
    assert status == 200
    assert len(body) > 1000
