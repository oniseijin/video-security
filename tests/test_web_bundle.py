from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import STATIC_DIR, create_app

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
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "t.db"
    _seed(db_path)
    cfg = Config()
    cfg.storage.db_path = str(db_path)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    yield TestClient(create_app(cfg))


def test_bundle_index_exists() -> None:
    assert INDEX.is_file()
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="root"' in html
    assert "VS // CONSOLE" in html
    assert "vs-theme" in html


def test_root_serves_bundle(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="root"' in resp.text
    assert "VS // CONSOLE" in resp.text


def test_spa_fallback_serves_bundle(client: TestClient) -> None:
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'id="root"' in resp.text


def test_bundle_asset_served(client: TestClient) -> None:
    html = INDEX.read_text(encoding="utf-8")
    asset = next(
        line.split('src="')[1].split('"')[0]
        for line in html.splitlines()
        if 'type="module"' in line and "src=" in line
    ).lstrip("./")
    resp = client.get(f"/{asset}")
    assert resp.status_code == 200
    assert len(resp.content) > 1000
    assert "immutable" in resp.headers["Cache-Control"]
