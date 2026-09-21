from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from video_security import db
from video_security.adapters import detect_adapter, get_adapter
from video_security.adapters.photos import PhotosAdapter
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.importer import run_import


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    config.import_.preflight_gb = 0
    return config


def _video(tmp_path: Path, name: str = "IMG_1234.MOV", data: bytes = b"mov-bytes") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _seam_env(monkeypatch: pytest.MonkeyPatch, rows: list[list[Any]]) -> None:
    monkeypatch.setenv("VIDEO_SECURITY_TEST_PHOTOS_ASSETS", json.dumps(rows))


def test_detect_adapter_photoslibrary(tmp_path: Path) -> None:
    lib = tmp_path / "My Library.photoslibrary"
    lib.mkdir()
    assert detect_adapter(lib) == "photos"


def test_get_adapter_photos(cfg: Config) -> None:
    adapter = get_adapter("photos", cfg)
    assert isinstance(adapter, PhotosAdapter)


def test_discover_skips_hidden_and_cloud_only(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = _video(tmp_path, "IMG_1.MOV")
    v2 = _video(tmp_path, "IMG_2.MOV", b"other")
    _seam_env(
        monkeypatch,
        [
            ["uuid-1", v1, "2026-09-01T10:00:00", False],
            ["uuid-2", None, "2026-09-01T11:00:00", False],
            ["uuid-3", v2, "2026-09-01T12:00:00", True],
            ["uuid-4", str(tmp_path / "missing.MOV"), "2026-09-01T13:00:00", False],
        ],
    )
    adapter = PhotosAdapter(cfg)
    clips = adapter.discover_clips(tmp_path)
    assert [c.source_uuid for c in clips] == ["uuid-1"]
    assert adapter.skipped_cloud_only == 2
    clip = clips[0]
    assert clip.mode == "PHOTOS"
    assert clip.channel == "front"
    assert clip.priority == pytest.approx(0.7)
    assert clip.recording_start_utc is not None
    assert clip.pair_path is None


def test_discover_filters_non_video_suffixes(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = _video(tmp_path, "IMG_3.MOV")
    photo = tmp_path / "IMG_4.JPG"
    photo.write_bytes(b"jpg")
    _seam_env(
        monkeypatch,
        [
            ["uuid-1", v, "2026-09-01T10:00:00", False],
            ["uuid-2", str(photo), "2026-09-01T10:05:00", False],
        ],
    )
    clips = PhotosAdapter(cfg).discover_clips(tmp_path)
    assert [c.source_uuid for c in clips] == ["uuid-1"]


def test_discover_since_filter(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = _video(tmp_path, "old.MOV")
    v2 = _video(tmp_path, "new.MOV", b"newer")
    _seam_env(
        monkeypatch,
        [
            ["uuid-old", v1, "2026-08-01T10:00:00", False],
            ["uuid-new", v2, "2026-09-15T10:00:00", False],
        ],
    )
    adapter = PhotosAdapter(cfg, since=datetime(2026, 9, 1))
    clips = adapter.discover_clips(tmp_path)
    assert [c.source_uuid for c in clips] == ["uuid-new"]


def test_run_import_photos_end_to_end(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "Photos Library.photoslibrary"
    lib.mkdir()
    v1 = _video(tmp_path, "IMG_1.MOV")
    _seam_env(
        monkeypatch,
        [
            ["uuid-1", v1, "2026-09-01T10:00:00", False],
            ["uuid-cloud", None, "2026-09-02T10:00:00", False],
        ],
    )
    report = run_import(lib, cfg, db_conn)
    assert report.imported == 1
    assert report.skipped_cloud == 1

    job_id = report.jobs[0]
    row = db.get_photos_import(db_conn, "uuid-1")
    assert row is not None
    assert int(row["job_id"]) == job_id

    clips_dir = tmp_path / "artifacts" / "clips"
    date_dir = next(iter(clips_dir.iterdir()))
    assert date_dir.name == f"{datetime.now().strftime('%Y%m%d')}"
    dest = date_dir / "PHOTOS" / "front" / "IMG_1.MOV"
    assert dest.is_file()
    assert dest.read_bytes() == b"mov-bytes"

    job = db_conn.execute(
        "SELECT import_id, metadata_json FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["import_id"] == f"{date_dir.name}-Photos-Library"
    meta = json.loads(job["metadata_json"])
    assert "device_kind" in meta

    second = run_import(lib, cfg, db_conn)
    assert second.imported == 0
    assert second.skipped == 1
    assert second.skipped_cloud == 1


def test_run_import_collision_renames(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib.photoslibrary"
    lib.mkdir()
    v = _video(tmp_path, "IMG_DUP.MOV", b"new-content")
    _seam_env(monkeypatch, [["uuid-1", v, "2026-09-01T10:00:00", False]])

    clips_dir = tmp_path / "artifacts" / "clips"
    date_dir = clips_dir / datetime.now().strftime("%Y%m%d") / "PHOTOS" / "front"
    date_dir.mkdir(parents=True)
    (date_dir / "IMG_DUP.MOV").write_bytes(b"existing-different-content")

    report = run_import(lib, cfg, db_conn)
    assert report.imported == 1
    dest = date_dir / "IMG_DUP.MOV"
    assert dest.read_bytes() == b"existing-different-content"
    prow = db.get_photos_import(db_conn, "uuid-1")
    assert prow is not None
    h = prow["video_hash"]
    renamed = date_dir / f"IMG_DUP-{h[:8]}.MOV"
    assert renamed.is_file()
    assert renamed.read_bytes() == b"new-content"


def test_run_import_hash_hit_records_uuid(
    db_conn: sqlite3.Connection, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib.photoslibrary"
    lib.mkdir()
    v1 = _video(tmp_path, "A.MOV", b"same")
    v2 = _video(tmp_path, "B.MOV", b"same")
    _seam_env(
        monkeypatch,
        [
            ["uuid-a", v1, "2026-09-01T10:00:00", False],
            ["uuid-b", v2, "2026-09-01T11:00:00", False],
        ],
    )
    report = run_import(lib, cfg, db_conn)
    assert report.imported == 1
    assert report.skipped == 1
    rows = db_conn.execute(
        "SELECT uuid, job_id FROM photos_imports ORDER BY uuid"
    ).fetchall()
    assert len(rows) == 2
    assert {r["uuid"] for r in rows} == {"uuid-a", "uuid-b"}
    assert all(int(r["job_id"]) == report.jobs[0] for r in rows)
