from __future__ import annotations

from pathlib import Path

from video_security import db
from video_security.config import Config, EngineConfig, StorageConfig
from video_security.engine import BatchEngine
from video_security.fs import MARKER_NAME, spotlight_ignore


def test_spotlight_ignore_creates_dir_and_marker(tmp_path: Path) -> None:
    d = tmp_path / "nested" / "dir"
    spotlight_ignore(d)
    assert (d / MARKER_NAME).is_file()
    spotlight_ignore(d)
    assert (d / MARKER_NAME).is_file()


def test_connect_marks_db_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "db"
    db.connect(str(db_path)).close()
    assert (tmp_path / "state" / MARKER_NAME).is_file()


def test_preflight_marks_artifact_dir(tmp_path: Path) -> None:
    cfg = Config(
        storage=StorageConfig(
            db_path=str(tmp_path / "db"), artifact_dir=str(tmp_path / "art")
        ),
        engine=EngineConfig(disk_preflight_gb=0, disk_watermark_gb=0),
    )
    BatchEngine(cfg).preflight()
    assert (tmp_path / "art" / MARKER_NAME).is_file()
