from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from video_security.backfill import (
    backfill_face_crops,
    backfill_plate_crops,
    read_frame,
)
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.prefilter.ocr import OCRResult
from video_security.prefilter.vehicles import Detection

FRAME = np.full((720, 1280, 3), 127, dtype=np.uint8)


def _detect(_frame: np.ndarray) -> list[Detection]:
    return [Detection(track_id=1, class_id=2, bbox=(100, 100, 300, 250), conf=0.9)]


def _seed(base: Path) -> Path:
    db_path = base / "t.db"
    conn = connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (id, video_path, video_hash, status) "
        "VALUES (1, '/tmp/clip.mp4', 'h1', 'done')"
    )
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, norm_text, best_frame, "
        "ocr_votes_json, crop_path, crop_src, crop_box) VALUES "
        "(1, 4, 0, '277E0', NULL, ?, NULL, NULL, NULL), "
        "(1, 7, 0, 'XYZ99', 900, NULL, NULL, NULL, NULL), "
        "(1, 8, 0, 'DONE1', 900, NULL, '/already/crop.jpg', "
        "'/already/track_8_src.jpg', '[0.1, 0.2, 0.3, 0.1]')",
        (json.dumps({"votes": 2, "best": {"frame": 217, "bbox": [0.5, 0.2, 0.2, 0.04]}}),),
    )
    conn.commit()
    conn.close()
    return db_path


def _config(base: Path) -> Config:
    cfg = Config()
    cfg.storage.db_path = str(base / "t.db")
    cfg.storage.artifact_dir = str(base / "artifacts")
    return cfg


def _reader(_video: str, frame_number: int) -> np.ndarray | None:
    if frame_number == 9999:
        return None
    return FRAME.copy()


def _ocr(img: np.ndarray) -> list[OCRResult]:
    return [
        OCRResult("277 E0", 1.0, (0.52, 0.18, 0.21, 0.04)),
        OCRResult("XYZ-99", 0.9, (0.1, 0.6, 0.2, 0.05)),
    ]


def test_backfill_writes_crops(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)
    report = backfill_plate_crops(conn, cfg, frame_reader=_reader, ocr_fn=_ocr, detector=_detect)
    assert report.attempted == 2
    assert report.written == 2
    assert report.failed == 0
    assert report.skipped == 0
    crop_a = tmp_path / "artifacts" / "plates" / "1" / "track_4.jpg"
    crop_b = tmp_path / "artifacts" / "plates" / "1" / "track_7.jpg"
    assert crop_a.is_file()
    assert crop_b.is_file()
    src_a = tmp_path / "artifacts" / "frames" / "1" / "track_4_src.jpg"
    src_b = tmp_path / "artifacts" / "frames" / "1" / "track_7_src.jpg"
    assert src_a.is_file()
    assert src_b.is_file()
    row = conn.execute(
        "SELECT crop_path, crop_src, crop_box FROM plates WHERE track_id = 4"
    ).fetchone()
    assert row["crop_path"] == str(crop_a)
    assert row["crop_src"] == str(src_a)
    box = json.loads(row["crop_box"])
    assert len(box) == 4
    assert all(0.0 <= v <= 1.0 for v in box)
    assert box[2] > 0.0 and box[3] > 0.0
    marker = tmp_path / "artifacts" / "plates" / "1" / ".metadata_never_index"
    assert marker.is_file()
    conn.close()


def test_backfill_idempotent(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)
    backfill_plate_crops(conn, cfg, frame_reader=_reader, ocr_fn=_ocr, detector=_detect)
    report = backfill_plate_crops(conn, cfg, frame_reader=_reader, ocr_fn=_ocr, detector=_detect)
    assert report.attempted == 0
    assert report.written == 0
    conn.close()


def test_backfill_regenerate_recrops(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)

    report = backfill_plate_crops(
        conn, cfg, frame_reader=_reader, ocr_fn=_ocr, detector=_detect
    )
    assert report.attempted == 2

    def ocr_all(img: np.ndarray) -> list[OCRResult]:
        return _ocr(img) + [OCRResult("DONE 1", 0.9, (0.3, 0.4, 0.2, 0.05))]

    report = backfill_plate_crops(
        conn,
        cfg,
        frame_reader=_reader,
        ocr_fn=ocr_all,
        detector=_detect,
        regenerate=True,
    )
    assert report.attempted == 3
    assert report.written == 3
    row = conn.execute(
        "SELECT crop_path FROM plates WHERE track_id = 8"
    ).fetchone()
    assert row["crop_path"] == str(
        tmp_path / "artifacts" / "plates" / "1" / "track_8.jpg"
    )
    conn.close()


def test_backfill_relocation_failure(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)

    def no_match(img: np.ndarray) -> list[OCRResult]:
        return [OCRResult("OTHER", 1.0, (0.1, 0.1, 0.1, 0.1))]

    report = backfill_plate_crops(conn, cfg, frame_reader=_reader, ocr_fn=no_match)
    assert report.attempted == 2
    assert report.written == 0
    assert report.failed == 2
    assert all("not relocated" in f for f in report.failures)
    conn.close()


def test_backfill_unreadable_frame(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)
    report = backfill_plate_crops(
        conn, cfg,
        frame_reader=lambda v, f: None,
        ocr_fn=_ocr,
        detector=_detect,
    )
    assert report.failed == 2
    assert all("not relocated" in f for f in report.failures)
    conn.close()


def test_backfill_limit(tmp_path: Path) -> None:
    db_path = _seed(tmp_path)
    conn = connect(str(db_path))
    cfg = _config(tmp_path)
    report = backfill_plate_crops(
        conn, cfg, limit=1, frame_reader=_reader, ocr_fn=_ocr, detector=_detect
    )
    assert report.attempted == 1
    assert report.written == 1
    conn.close()


def test_read_frame_garbage_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert read_frame(str(bad), 10) is None
    assert read_frame(str(tmp_path / "missing.mp4"), 10) is None


def _jpeg_bytes() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.full((400, 600, 3), 120, dtype=np.uint8))
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def _seed_faces(base: Path, with_faces: bool = True) -> Path:
    db_path = base / "faces.db"
    conn = connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (id, video_path, video_hash, status) "
        "VALUES (1, '/tmp/clip.mp4', 'h1', 'done')"
    )
    conn.execute(
        "INSERT INTO events (id, job_id, event_type, start_sec, end_sec, clip_id, "
        "detector_score, priority, status, keyframes_json, faces_json) "
        "VALUES (10, 1, 'suspicious_behavior', 5.0, 6.0, 0, 0.9, 0.5, 'detailed', "
        "?, ?)",
        (
            json.dumps([str(base / "artifacts" / "frames" / "1" / "event_10_0.jpg")]),
            json.dumps([[[0.25, 0.25, 0.3, 0.3]]]) if with_faces else None,
        ),
    )
    conn.commit()
    conn.close()
    frames = base / "artifacts" / "frames" / "1"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "event_10_0.jpg").write_bytes(_jpeg_bytes())
    return db_path


def _faces_config(base: Path) -> Config:
    cfg = Config()
    cfg.storage.db_path = str(base / "faces.db")
    cfg.storage.artifact_dir = str(base / "artifacts")
    return cfg


def test_backfill_face_crops(tmp_path: Path) -> None:
    import cv2

    db_path = _seed_faces(tmp_path)
    conn = connect(str(db_path))
    cfg = _faces_config(tmp_path)
    report = backfill_face_crops(conn, cfg)
    assert report.attempted == 1
    assert report.written == 1
    crop = tmp_path / "artifacts" / "faces" / "1" / "face_10_0_0.jpg"
    assert crop.is_file()
    img = cv2.imread(str(crop))
    assert img is not None
    assert (tmp_path / "artifacts" / "faces" / "1" / ".metadata_never_index").is_file()
    second = backfill_face_crops(conn, cfg)
    assert second.attempted == 1
    assert second.written == 0
    assert second.skipped == 1
    conn.close()


def test_backfill_face_crops_no_faces(tmp_path: Path) -> None:
    db_path = _seed_faces(tmp_path, with_faces=False)
    conn = connect(str(db_path))
    cfg = _faces_config(tmp_path)
    report = backfill_face_crops(conn, cfg)
    assert report.attempted == 0
    conn.close()


def test_backfill_face_crops_detect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.prefilter.faces.detect_faces",
        lambda _img: [(0.25, 0.25, 0.3, 0.3)],
    )
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda _img: np.ones(4, dtype=np.float32) / 2.0,
    )
    db_path = _seed_faces(tmp_path, with_faces=False)
    conn = connect(str(db_path))
    cfg = _faces_config(tmp_path)
    report = backfill_face_crops(conn, cfg, detect=True)
    assert report.attempted == 1
    assert report.written == 1
    crop = tmp_path / "artifacts" / "faces" / "1" / "face_10_0_0.jpg"
    assert crop.is_file()
    row = conn.execute(
        "SELECT faces_json FROM events WHERE id = 10"
    ).fetchone()
    assert json.loads(row["faces_json"]) == [[[0.25, 0.25, 0.3, 0.3]]]
    faces_rows = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    assert faces_rows == 1
    persons = conn.execute(
        "SELECT sightings FROM persons"
    ).fetchall()
    assert len(persons) == 1
    assert persons[0]["sightings"] == 1
    second = backfill_face_crops(conn, cfg, detect=True)
    assert second.written == 0
    assert second.skipped == 1
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 1
    conn.close()
