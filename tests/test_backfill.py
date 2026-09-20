from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from video_security.backfill import backfill_plate_crops, read_frame
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
        "ocr_votes_json, crop_path) VALUES "
        "(1, 4, 0, '277E0', NULL, ?, NULL), "
        "(1, 7, 0, 'XYZ99', 900, NULL, NULL), "
        "(1, 8, 0, 'DONE1', 900, NULL, '/already/crop.jpg')",
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
    row = conn.execute(
        "SELECT crop_path FROM plates WHERE track_id = 4"
    ).fetchone()
    assert row["crop_path"] == str(crop_a)
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
