from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.pipeline import analyze_video, enqueue_video


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def test_faces_json_stored_on_events(
    db_conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.pipeline.detect_faces",
        lambda _img: [(0.1, 0.2, 0.3, 0.4)],
    )
    from tests.golden import golden_clip

    clip = golden_clip(tmp_path / "t.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    analyze_video(job, config, db_conn, no_llm=True)
    events = db_conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job.id,)
    ).fetchall()
    assert len(events) > 0
    for ev in events:
        assert ev["faces_json"] is not None
        faces_data = json.loads(ev["faces_json"])
        assert isinstance(faces_data, list)
        assert len(faces_data) > 0
        for per_kf in faces_data:
            assert isinstance(per_kf, list)
            for face in per_kf:
                assert face == [0.1, 0.2, 0.3, 0.4]
        kf_data = json.loads(ev["keyframes_json"])
        assert len(faces_data) == len(kf_data)