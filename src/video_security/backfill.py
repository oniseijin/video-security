from __future__ import annotations

import dataclasses
import difflib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from video_security.config import Config
from video_security.fs import spotlight_ignore
from video_security.prefilter.ocr import (
    LEVEL_ACCURATE,
    OCRResult,
    upscale_crop,
    vision_ocr,
)
from video_security.prefilter.plates import normalize_plate
from video_security.prefilter.vehicles import (
    VEHICLE_CLASSES,
    Detection,
    crop_vehicle,
    load_detector,
)

FrameReader = Callable[[str, int], np.ndarray | None]
OcrFn = Callable[[np.ndarray], list[OCRResult]]
Detector = Callable[[np.ndarray], list[Detection]]

_MATCH_THRESHOLD = 0.6
_FRAME_CACHE_LIMIT = 24
_FRAME_OFFSETS = (0, -1, 1, -2, 2)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _match_score(observed: str, target: str) -> float:
    if observed == target:
        return 1.0
    do = _digits(observed)
    dt = _digits(target)
    if len(do) < 2 or len(dt) < 2:
        return 0.0
    if do == dt:
        return 0.95
    return difflib.SequenceMatcher(None, do, dt).ratio()


@dataclasses.dataclass
class BackfillReport:
    attempted: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[str] = dataclasses.field(default_factory=list)


def read_frame(video_path: str, frame_number: int, width: int = 1280) -> np.ndarray | None:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = cap.read()
        if not ok:
            return None
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(
                frame,
                (width, int(round(h * width / w))),
                interpolation=cv2.INTER_AREA,
            )
        return frame
    except Exception:
        return None
    finally:
        cap.release()


def crop_region(
    frame: np.ndarray,
    bbox: tuple[float, float, float, float],
    pad: float = 0.15,
) -> np.ndarray:
    h, w = frame.shape[:2]
    bx, by, bw, bh = bbox
    top = 1.0 - by - bh
    x1 = max(0.0, (bx - pad * bw) * w)
    y1 = max(0.0, (top - pad * bh) * h)
    x2 = min(float(w), (bx + bw * (1 + pad)) * w)
    y2 = min(float(h), (top + bh * (1 + pad)) * h)
    return frame[int(y1) : int(y2), int(x1) : int(x2)]


def _best_frame_of(row: sqlite3.Row) -> int | None:
    if row["best_frame"] is not None:
        return int(row["best_frame"])
    raw = row["ocr_votes_json"]
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return int(payload["best"]["frame"])
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def _locate_in_frame(
    frame: np.ndarray,
    norm: str,
    detector: Detector,
    ocr: OcrFn,
) -> np.ndarray | None:
    best_crop: np.ndarray | None = None
    best_score = 0.0
    for det in detector(frame):
        if det.class_id not in VEHICLE_CLASSES:
            continue
        vehicle_crop = upscale_crop(crop_vehicle(frame, det.bbox))
        for obs in ocr(vehicle_crop):
            score = _match_score(normalize_plate(str(obs.text)), norm)
            if score > best_score:
                best_score = score
                best_crop = crop_region(vehicle_crop, obs.bbox)
    if best_score >= _MATCH_THRESHOLD and best_crop is not None:
        return best_crop
    return None


def _keyframe_images(
    conn: sqlite3.Connection,
    job_id: int,
    track_id: int,
) -> list[np.ndarray]:
    images: list[np.ndarray] = []
    rows = conn.execute(
        "SELECT keyframes_json FROM events "
        "WHERE job_id = ? AND keyframes_json != '[]' "
        "AND (track_id = ? OR event_type = 'plate_capture') "
        "ORDER BY (track_id = ?) DESC, start_sec",
        (job_id, track_id, track_id),
    ).fetchall()
    for row in rows:
        try:
            paths = json.loads(row["keyframes_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(paths, list):
            continue
        for path_str in paths[:2]:
            p = Path(str(path_str))
            if not p.is_file():
                continue
            data = p.read_bytes()
            img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                images.append(img)
        if len(images) >= 6:
            break
    return images


def backfill_plate_crops(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
    frame_reader: FrameReader | None = None,
    ocr_fn: OcrFn | None = None,
    detector: Detector | None = None,
) -> BackfillReport:
    reader = frame_reader or read_frame
    ocr = ocr_fn or (
        lambda img: vision_ocr(img, level=LEVEL_ACCURATE, languages=["ja-JP", "en-US"])
    )
    if detector is None:
        detector = load_detector(config)
    rows = conn.execute(
        "SELECT p.job_id, p.track_id, p.clip_id, p.norm_text, p.best_frame, "
        "p.ocr_votes_json, j.video_path "
        "FROM plates p JOIN jobs j ON j.id = p.job_id "
        "WHERE p.crop_path IS NULL AND p.norm_text IS NOT NULL "
        "ORDER BY p.job_id, p.track_id"
    ).fetchall()
    if limit is not None:
        rows = rows[: max(0, limit)]
    report = BackfillReport(attempted=len(rows))
    artifact = Path(config.storage.artifact_dir).expanduser()
    cache: dict[tuple[str, int], np.ndarray | None] = {}
    for row in rows:
        norm = str(row["norm_text"])
        frame_number = _best_frame_of(row)
        if frame_number is None:
            report.skipped += 1
            continue
        video_path = str(row["video_path"])
        crop: np.ndarray | None = None
        for image in _keyframe_images(conn, int(row["job_id"]), int(row["track_id"])):
            crop = _locate_in_frame(image, norm, detector, ocr)
            if crop is not None:
                break
        if crop is None:
            for offset in _FRAME_OFFSETS:
                fn = frame_number + offset
                if fn < 0:
                    continue
                key = (video_path, fn)
                if key not in cache:
                    if len(cache) >= _FRAME_CACHE_LIMIT:
                        cache.clear()
                    cache[key] = reader(video_path, fn)
                video_frame = cache[key]
                if video_frame is None:
                    continue
                crop = _locate_in_frame(video_frame, norm, detector, ocr)
                if crop is not None:
                    break
        if crop is None or crop.size == 0:
            report.failed += 1
            report.failures.append(
                f"job {row['job_id']} track {row['track_id']}: "
                f"plate {norm} not relocated at frame {frame_number}"
            )
            continue
        plates_dir = artifact / "plates" / str(int(row["job_id"]))
        plates_dir.mkdir(parents=True, exist_ok=True)
        spotlight_ignore(plates_dir)
        out = plates_dir / f"track_{int(row['track_id'])}.jpg"
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            report.failed += 1
            report.failures.append(
                f"job {row['job_id']} track {row['track_id']}: jpeg encode failed"
            )
            continue
        out.write_bytes(buf.tobytes())
        conn.execute(
            "UPDATE plates SET crop_path = ? "
            "WHERE job_id = ? AND track_id = ? AND clip_id = ?",
            (str(out), int(row["job_id"]), int(row["track_id"]), int(row["clip_id"])),
        )
        report.written += 1
    conn.commit()
    return report
