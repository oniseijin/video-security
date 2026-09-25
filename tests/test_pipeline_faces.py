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

def test_boxes_json_stored_on_events(
    db_conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv2

    from video_security.prefilter.vehicles import Detection

    def fake_detector(_img: object) -> list[Detection]:
        return [
            Detection(track_id=1, class_id=0, bbox=(200, 200, 400, 800), conf=0.9),
            Detection(track_id=2, class_id=17, bbox=(800, 600, 1200, 900), conf=0.8),
        ]

    monkeypatch.setattr(
        "video_security.pipeline.load_detector",
        lambda _config, _camera=None: fake_detector,
    )
    monkeypatch.setattr(
        "video_security.pipeline.detect_faces",
        lambda _img: [],
    )
    from tests.golden import golden_clip

    clip = golden_clip(tmp_path / "t.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    config.threat.score_threshold = 0.35
    analyze_video(job, config, db_conn, no_llm=True)
    events = db_conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job.id,)
    ).fetchall()
    assert len(events) > 0
    kinds_seen: set[str] = set()
    for ev in events:
        assert ev["boxes_json"] is not None
        boxes_data = json.loads(ev["boxes_json"])
        assert isinstance(boxes_data, list)
        kf_paths = json.loads(ev["keyframes_json"])
        assert len(boxes_data) == len(kf_paths)
        for per_kf, kf_path in zip(boxes_data, kf_paths, strict=True):
            assert isinstance(per_kf, list)
            img = cv2.imread(str(kf_path))
            assert img is not None
            h, w = img.shape[:2]
            for entry in per_kf:
                assert set(entry) == {"kind", "track_id", "box"}
                kinds_seen.add(entry["kind"])
                if entry["kind"] == "person":
                    assert entry["track_id"] == 1
                    assert entry["box"] == pytest.approx(
                        [200 / w, 200 / h, 200 / w, 600 / h]
                    )
                else:
                    assert entry["track_id"] == 2
                    assert entry["box"] == pytest.approx(
                        [800 / w, 600 / h, 400 / w, 300 / h]
                    )
    assert kinds_seen == {"person", "animal"}


def test_face_crops_written(
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
    faces_root = tmp_path / "artifacts" / "faces" / str(job.id)
    wrote_any = False
    for ev in events:
        kf_data = json.loads(ev["keyframes_json"])
        for i in range(len(kf_data)):
            crop = faces_root / f"face_{ev['id']}_{i}_0.jpg"
            if crop.is_file():
                wrote_any = True
    assert wrote_any
    assert (faces_root / ".metadata_never_index").is_file()


def test_harvest_registers_face_rows(
    db_conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    monkeypatch.setattr(
        "video_security.pipeline.detect_faces",
        lambda _img: [(0.1, 0.2, 0.3, 0.4)],
    )
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda _img: np.ones(4, dtype=np.float32) / 2.0,
    )
    from tests.golden import golden_clip
    from video_security.pipeline import harvest_job

    clip = golden_clip(tmp_path / "t.mp4")
    job, _ = enqueue_video(db_conn, clip)
    config = Config()
    config.storage.artifact_dir = str(tmp_path / "artifacts")
    harvest_job(job, config, db_conn)
    n = db_conn.execute(
        "SELECT COUNT(*) FROM faces WHERE job_id = ?", (job.id,)
    ).fetchone()[0]
    assert n > 0
    persons = db_conn.execute(
        "SELECT COUNT(*) FROM persons WHERE sightings > 0"
    ).fetchone()[0]
    assert persons == 1
