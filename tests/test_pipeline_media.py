from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

from video_security.config import Config
from video_security.db import MIGRATIONS, connect, init_db
from video_security.prefilter.ocr import OCRResult
from video_security.prefilter.vehicles import crop_vehicle


def test_legacy_ocr_votes_json_still_parses(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    conn.execute(
        "INSERT INTO plates "
        "(job_id, track_id, clip_id, raw_text, norm_text, confidence, best_frame, ocr_votes_json) "
        "VALUES (1, 2, 0, 'ABC123', 'ABC123', 0.9, 100, '{\"votes\": 5}')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT ocr_votes_json FROM plates WHERE job_id = 1"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["ocr_votes_json"])
    assert payload["votes"] == 5
    assert "best" not in payload
    conn.close()


def test_idempotent_double_init_db(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    init_db(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == len(MIGRATIONS)
    cols_plates = [
        r[1] for r in conn.execute("PRAGMA table_info(plates)").fetchall()
    ]
    assert "crop_path" in cols_plates
    cols_tracks = [
        r[1] for r in conn.execute("PRAGMA table_info(vehicle_tracks)").fetchall()
    ]
    assert "strip_json" in cols_tracks
    indexes = {
        r[1]
        for r in conn.execute("PRAGMA index_list(events)").fetchall()
    }
    for idx in ("idx_events_job_start", "idx_events_type"):
        assert idx in indexes
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash, status, import_id) "
        "VALUES ('/v/test.mp4', 'h1', 'done', 'imp-1')"
    )
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, norm_text) VALUES (1, 1, 0, 'X')"
    )
    conn.commit()
    init_db(conn)
    conn.close()


def test_retention_removes_plates_dir(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash) VALUES ('/v/t.mp4', 'h1')"
    )
    conn.commit()
    plates_dir = tmp_path / "artifacts" / "plates" / "1"
    plates_dir.mkdir(parents=True)
    (plates_dir / "track_1.jpg").write_bytes(b"fake")
    from video_security.db import delete_job_rows

    delete_job_rows(conn, 1, str(tmp_path / "artifacts"))
    assert not plates_dir.exists()
    conn.close()


def _synthetic_frame(w: int = 640, h: int = 480) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_plate_crop_written_with_bbox(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    config = Config()
    config.storage.artifact_dir = str(artifact_dir)
    plates_dir = artifact_dir / "plates" / "1"
    plates_dir.mkdir(parents=True)

    jpeg_cache = {
        0: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
        5: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
        10: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
    }

    track_bboxes: dict[int, tuple[int, int, int, int]] = {
        0: (100, 100, 300, 250),
        5: (100, 100, 300, 250),
        10: (100, 100, 300, 250),
    }

    bbox_in_vehicle: tuple[float, float, float, float] = (0.3, 0.2, 0.4, 0.2)

    from video_security.prefilter.plates import extract_plate_reads

    def fake_ocr(_img: np.ndarray) -> list[OCRResult]:
        return [OCRResult("ABC123", 0.95, bbox_in_vehicle)]

    images = {fn: _synthetic_frame() for fn in track_bboxes}
    read = extract_plate_reads(track_bboxes, images, 7, config, ocr_fn=fake_ocr)
    assert read is not None
    assert read.best_bbox == bbox_in_vehicle

    best_jpeg = jpeg_cache[read.best_frame]
    img = cv2.imdecode(np.frombuffer(best_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    vehicle_crop = crop_vehicle(img, track_bboxes[read.best_frame])
    vh, vw = vehicle_crop.shape[:2]
    assert read.best_bbox is not None
    from video_security.prefilter.plates import plate_crop_rect

    px1, py1, px2, py2 = plate_crop_rect(
        read.best_bbox, vw, vh, config.prefilter.plate_crop_pad
    )
    plate_crop = vehicle_crop[py1:py2, px1:px2]
    assert plate_crop.size > 0
    assert plate_crop.shape[0] < vh

    crop_out = plates_dir / f"track_{read.track_id}.jpg"
    ok, buf = cv2.imencode(".jpg", plate_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    crop_out.write_bytes(buf.tobytes())
    assert crop_out.exists()

    votes_payload: dict[str, object] = {
        "votes": read.votes,
        "best": {"frame": read.best_frame, "bbox": list(read.best_bbox)},
    }
    parsed = json.loads(json.dumps(votes_payload))
    assert parsed["votes"] == 3
    assert parsed["best"]["frame"] == read.best_frame
    assert parsed["best"]["bbox"] == list(read.best_bbox)


def _encode_jpeg_bytes(image: np.ndarray, quality: int = 80) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


def test_night_clip_produces_raw_keyframes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    config = Config()
    config.storage.artifact_dir = str(artifact_dir)

    jpeg_cache: OrderedDict[int, bytes] = OrderedDict({
        0: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
    })
    raw_jpeg_cache: OrderedDict[int, bytes] = OrderedDict({
        0: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
    })

    from video_security.pipeline import _select_keyframes

    paths = _select_keyframes(
        [0], jpeg_cache, artifact_dir, 42, 7, raw_jpeg_cache=raw_jpeg_cache
    )
    assert len(paths) == 1
    enhanced = Path(paths[0])
    assert enhanced.exists()
    raw_path = artifact_dir / "frames" / "42" / "event_7_0_raw.jpg"
    assert raw_path.exists()
    assert raw_path.read_bytes() == raw_jpeg_cache[0]


def test_night_clip_no_raw_when_cache_empty(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    jpeg_cache: OrderedDict[int, bytes] = OrderedDict({
        0: _encode_jpeg_bytes(_synthetic_frame(640, 480)),
    })
    from video_security.pipeline import _select_keyframes

    paths = _select_keyframes(
        [0], jpeg_cache, artifact_dir, 42, 7, raw_jpeg_cache=None
    )
    assert len(paths) == 1
    assert (Path(paths[0])).exists()
    raw_path = artifact_dir / "frames" / "42" / "event_7_0_raw.jpg"
    assert not raw_path.exists()


def test_track_strips_written(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    config = Config()
    config.storage.artifact_dir = str(artifact_dir)
    jpeg_cache = {
        fn: _encode_jpeg_bytes(_synthetic_frame(640, 480))
        for fn in range(5, 16)
    }

    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash, status) VALUES ('/v/t.mp4', 'h1', 'done')"
    )
    conn.execute(
        "INSERT INTO vehicle_tracks "
        "(job_id, track_id, clip_id, first_frame, last_frame) "
        "VALUES (1, 2, 0, 5, 15)"
    )
    conn.commit()

    from video_security.fs import spotlight_ignore

    frames_dir = artifact_dir / "frames" / "1"
    spotlight_ignore(frames_dir)
    strip_paths: list[str] = []
    num_frames = 15 - 5 + 1
    sample_count = min(5, num_frames)
    for i in range(sample_count):
        fn = 5 + int(i * (num_frames - 1) / max(1, sample_count - 1))
        jpeg_bytes = jpeg_cache[fn]
        strip_out = frames_dir / f"track_2_{i}.jpg"
        strip_out.write_bytes(jpeg_bytes)
        strip_paths.append(str(strip_out))
    from video_security.db import update_vehicle_track_strip

    update_vehicle_track_strip(conn, 1, 2, json.dumps(strip_paths))

    row = conn.execute(
        "SELECT strip_json FROM vehicle_tracks WHERE job_id = 1 AND track_id = 2"
    ).fetchone()
    assert row is not None
    strip_paths_loaded = json.loads(row["strip_json"])
    assert len(strip_paths_loaded) == 5
    for p in strip_paths_loaded:
        assert Path(p).exists()
    conn.close()


def test_track_strips_fewer_than_five(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    jpeg_cache = {
        fn: _encode_jpeg_bytes(_synthetic_frame(640, 480))
        for fn in range(5, 8)
    }

    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    conn.execute(
        "INSERT INTO jobs (video_path, video_hash, status) VALUES ('/v/t.mp4', 'h1', 'done')"
    )
    conn.execute(
        "INSERT INTO vehicle_tracks "
        "(job_id, track_id, clip_id, first_frame, last_frame) "
        "VALUES (1, 3, 0, 5, 7)"
    )
    conn.commit()

    from video_security.fs import spotlight_ignore

    frames_dir = artifact_dir / "frames" / "1"
    spotlight_ignore(frames_dir)
    strip_paths: list[str] = []
    for i, fn in enumerate([5, 7]):
        strip_out = frames_dir / f"track_3_{i}.jpg"
        strip_out.write_bytes(jpeg_cache[fn])
        strip_paths.append(str(strip_out))
    from video_security.db import update_vehicle_track_strip

    update_vehicle_track_strip(conn, 1, 3, json.dumps(strip_paths))

    row = conn.execute(
        "SELECT strip_json FROM vehicle_tracks WHERE job_id = 1 AND track_id = 3"
    ).fetchone()
    assert row is not None
    loaded = json.loads(row["strip_json"])
    assert len(loaded) == 2
    conn.close()