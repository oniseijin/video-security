from __future__ import annotations

import dataclasses
import difflib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from video_security.prefilter.plates import (
    normalize_plate,
    plate_crop_rect,
    plate_src_rect,
)
from video_security.prefilter.vehicles import (
    VEHICLE_CLASSES,
    Detection,
    box_kind,
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
    pad: float | list[float] = 0.15,
) -> np.ndarray:
    h, w = frame.shape[:2]
    if isinstance(pad, list):
        x1, y1, x2, y2 = plate_crop_rect(bbox, w, h, pad)
        return frame[y1:y2, x1:x2]
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
    pad: list[float],
) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """Matched plate: (crop pixels, full-frame padded rect top-left px)."""
    best_crop: np.ndarray | None = None
    best_rect: tuple[int, int, int, int] | None = None
    best_score = 0.0
    for det in detector(frame):
        if det.class_id not in VEHICLE_CLASSES:
            continue
        vehicle_crop = upscale_crop(crop_vehicle(frame, det.bbox))
        for obs in ocr(vehicle_crop):
            score = _match_score(normalize_plate(str(obs.text)), norm)
            if score > best_score:
                best_score = score
                best_crop = crop_region(vehicle_crop, obs.bbox, pad)
                best_rect = plate_src_rect(frame.shape, det.bbox, obs.bbox, pad)
    if best_score >= _MATCH_THRESHOLD and best_crop is not None and best_rect is not None:
        return best_crop, best_rect
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


def _face_crop(img: np.ndarray, box: list[float]) -> np.ndarray:
    h, w = img.shape[:2]
    bx, by, bw, bh = box[0], box[1], box[2], box[3]
    x1 = max(0.0, (bx - 0.15 * bw) * w)
    y1 = max(0.0, (by - 0.15 * bh) * h)
    x2 = min(float(w), (bx + bw * 1.15) * w)
    y2 = min(float(h), (by + bh * 1.15) * h)
    return upscale_crop(img[int(y1) : int(y2), int(x1) : int(x2)])


def _write_face_crop_file(img: np.ndarray, box: list[float], out: Path) -> None:
    crop = _face_crop(img, box)
    if crop.size == 0:
        return
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        out.parent.mkdir(parents=True, exist_ok=True)
        spotlight_ignore(out.parent)
        out.write_bytes(buf.tobytes())


def _detect_faces_for_event(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    event_id: int,
    kf_paths: list[str],
    artifact: Path,
) -> int:
    from video_security import db as vsdb
    from video_security.identity import register_face
    from video_security.prefilter.faces import detect_faces

    wrote = 0
    faces_per_kf: list[list[list[float]]] = []
    for i, path_str in enumerate(kf_paths):
        boxes_out: list[list[float]] = []
        p = Path(str(path_str))
        img = None
        if p.is_file():
            img = cv2.imdecode(
                np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
            )
        if img is not None:
            for j, box in enumerate(detect_faces(img)):
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    continue
                norm = [float(v) for v in box]
                boxes_out.append(norm)
                out = artifact / "faces" / str(job_id) / f"face_{event_id}_{i}_{j}.jpg"
                _write_face_crop_file(img, norm, out)
                if out.is_file():
                    wrote += 1
                    register_face(
                        conn,
                        config,
                        job_id,
                        event_id,
                        i,
                        j,
                        str(out),
                        _face_crop(img, norm),
                    )
        faces_per_kf.append(boxes_out)
    vsdb.update_event_faces(conn, event_id, json.dumps(faces_per_kf))
    return wrote


def backfill_face_crops(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
    detect: bool = False,
) -> BackfillReport:
    where = "e.keyframes_json != '[]'"
    if not detect:
        where += " AND e.faces_json GLOB '*[0-9]*'"
    rows = conn.execute(
        f"SELECT e.id, e.job_id, e.keyframes_json, e.faces_json FROM events e "
        f"WHERE {where} "
        "ORDER BY e.job_id, e.id"
    ).fetchall()
    if limit is not None:
        rows = rows[: max(0, limit)]
    report = BackfillReport(attempted=len(rows))
    artifact = Path(config.storage.artifact_dir).expanduser()
    for row in rows:
        event_id = int(row["id"])
        job_id = int(row["job_id"])
        try:
            kf_paths = json.loads(row["keyframes_json"])
            faces = json.loads(row["faces_json"]) if row["faces_json"] else []
        except (json.JSONDecodeError, TypeError):
            report.failed += 1
            report.failures.append(f"job {job_id} event {event_id}: unparsable json")
            continue
        if not isinstance(kf_paths, list) or not isinstance(faces, list):
            report.failed += 1
            report.failures.append(f"job {job_id} event {event_id}: unexpected shape")
            continue
        has_detections = any(isinstance(f, list) and f for f in faces)
        if detect and not has_detections:
            wrote = _detect_faces_for_event(
                conn, config, job_id, event_id, kf_paths, artifact
            )
            if wrote > 0:
                report.written += wrote
            else:
                report.skipped += 1
            continue
        wrote = 0
        for i, path_str in enumerate(kf_paths):
            boxes = faces[i] if i < len(faces) else []
            if not boxes:
                continue
            p = Path(str(path_str))
            if not p.is_file():
                continue
            img = cv2.imdecode(
                np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if img is None:
                continue
            for j, box in enumerate(boxes):
                if not isinstance(box, list) or len(box) != 4:
                    continue
                out = artifact / "faces" / str(job_id) / f"face_{event_id}_{i}_{j}.jpg"
                if out.is_file():
                    continue
                _write_face_crop_file(img, box, out)
                if out.is_file():
                    wrote += 1
        if wrote > 0:
            report.written += wrote
        else:
            report.skipped += 1
    return report


def backfill_event_boxes(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
    detector: Detector | None = None,
) -> BackfillReport:
    from video_security import db as vsdb

    if detector is None:
        detector = load_detector(config, persist=False)
    rows = conn.execute(
        "SELECT e.id, e.job_id, e.keyframes_json FROM events e "
        "WHERE e.keyframes_json != '[]' AND e.boxes_json IS NULL "
        "ORDER BY e.job_id, e.id"
    ).fetchall()
    if limit is not None:
        rows = rows[: max(0, limit)]
    report = BackfillReport(attempted=len(rows))
    for row in rows:
        event_id = int(row["id"])
        try:
            kf_paths = json.loads(row["keyframes_json"])
        except (json.JSONDecodeError, TypeError):
            report.failed += 1
            report.failures.append(f"event {event_id}: unparsable keyframes json")
            continue
        if not isinstance(kf_paths, list):
            report.failed += 1
            report.failures.append(f"event {event_id}: unexpected shape")
            continue
        boxes_per_kf: list[list[dict[str, Any]]] = []
        found_any = False
        for path_str in kf_paths:
            kf_boxes: list[dict[str, Any]] = []
            p = Path(str(path_str))
            img = None
            if p.is_file():
                img = cv2.imdecode(
                    np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
                )
            if img is not None:
                ih, iw = img.shape[:2]
                for det in detector(img):
                    kind = box_kind(det.class_id)
                    if kind is None:
                        continue
                    x1 = max(0, det.bbox[0])
                    y1 = max(0, det.bbox[1])
                    x2 = min(iw, det.bbox[2])
                    y2 = min(ih, det.bbox[3])
                    if x2 <= x1 or y2 <= y1:
                        continue
                    kf_boxes.append(
                        {
                            "kind": kind,
                            "track_id": det.track_id,
                            "box": [
                                x1 / iw,
                                y1 / ih,
                                (x2 - x1) / iw,
                                (y2 - y1) / ih,
                            ],
                        }
                    )
            if kf_boxes:
                found_any = True
            boxes_per_kf.append(kf_boxes)
        vsdb.update_event_boxes(conn, event_id, json.dumps(boxes_per_kf))
        if found_any:
            report.written += 1
        else:
            report.skipped += 1
    return report


def backfill_plate_crops(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
    frame_reader: FrameReader | None = None,
    ocr_fn: OcrFn | None = None,
    detector: Detector | None = None,
    regenerate: bool = False,
) -> BackfillReport:
    reader = frame_reader or read_frame
    ocr = ocr_fn or (
        lambda img: vision_ocr(img, level=LEVEL_ACCURATE, languages=["ja-JP", "en-US"])
    )
    if detector is None:
        detector = load_detector(config)
    where = "p.norm_text IS NOT NULL"
    if not regenerate:
        where += " AND (p.crop_path IS NULL OR p.crop_src IS NULL)"
    rows = conn.execute(
        f"SELECT p.job_id, p.track_id, p.clip_id, p.norm_text, p.best_frame, "
        f"p.ocr_votes_json, p.crop_path, j.video_path "
        f"FROM plates p JOIN jobs j ON j.id = p.job_id "
        f"WHERE {where} "
        f"ORDER BY p.job_id, p.track_id"
    ).fetchall()
    if limit is not None:
        rows = rows[: max(0, limit)]
    report = BackfillReport(attempted=len(rows))
    artifact = Path(config.storage.artifact_dir).expanduser()
    pad = config.prefilter.plate_crop_pad
    cache: dict[tuple[str, int], np.ndarray | None] = {}
    for row in rows:
        norm = str(row["norm_text"])
        frame_number = _best_frame_of(row)
        if frame_number is None:
            report.skipped += 1
            continue
        video_path = str(row["video_path"])
        want_crop = regenerate or row["crop_path"] is None
        located: tuple[np.ndarray, tuple[int, int, int, int]] | None = None
        source_frame: np.ndarray | None = None
        for image in _keyframe_images(conn, int(row["job_id"]), int(row["track_id"])):
            located = _locate_in_frame(image, norm, detector, ocr, pad)
            if located is not None:
                source_frame = image
                break
        if located is None:
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
                located = _locate_in_frame(video_frame, norm, detector, ocr, pad)
                if located is not None:
                    source_frame = video_frame
                    break
        if located is None or source_frame is None or located[0].size == 0:
            report.failed += 1
            report.failures.append(
                f"job {row['job_id']} track {row['track_id']}: "
                f"plate {norm} not relocated at frame {frame_number}"
            )
            continue
        crop, src_rect = located
        plates_dir = artifact / "plates" / str(int(row["job_id"]))
        plates_dir.mkdir(parents=True, exist_ok=True)
        spotlight_ignore(plates_dir)
        out = plates_dir / f"track_{int(row['track_id'])}.jpg"
        wrote = False
        if want_crop:
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
            wrote = True
        sx1, sy1, sx2, sy2 = src_rect
        if sx2 - sx1 > 0 and sy2 - sy1 > 0:
            src_out = (
                artifact
                / "frames"
                / str(int(row["job_id"]))
                / f"track_{int(row['track_id'])}_src.jpg"
            )
            src_out.parent.mkdir(parents=True, exist_ok=True)
            ok_src, sbuf = cv2.imencode(
                ".jpg", source_frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            if ok_src:
                src_out.write_bytes(sbuf.tobytes())
                fh, fw = source_frame.shape[:2]
                crop_box = json.dumps(
                    [
                        sx1 / fw,
                        sy1 / fh,
                        (sx2 - sx1) / fw,
                        (sy2 - sy1) / fh,
                    ]
                )
                conn.execute(
                    "UPDATE plates SET crop_src = ?, crop_box = ? "
                    "WHERE job_id = ? AND track_id = ? AND clip_id = ?",
                    (
                        str(src_out),
                        crop_box,
                        int(row["job_id"]),
                        int(row["track_id"]),
                        int(row["clip_id"]),
                    ),
                )
                wrote = True
        if wrote:
            report.written += 1
    conn.commit()
    return report
