from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from video_security.config import Config

FALLBACK_MIN_CROP_PX = 48


def _image_to_nsdata(image: np.ndarray) -> Any:
    import cv2
    from Foundation import NSData

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encode failed")
    raw = buf.tobytes()
    return NSData.dataWithBytes_length_(raw, len(raw))


def face_capture_quality(image: np.ndarray) -> float | None:
    from Vision import VNDetectFaceCaptureQualityRequest, VNImageRequestHandler

    if image.ndim != 3 or image.shape[2] < 3 or image.size == 0:
        return None
    try:
        handler = VNImageRequestHandler.alloc().initWithData_options_(
            _image_to_nsdata(image), None
        )
        req = VNDetectFaceCaptureQualityRequest.alloc().init()
        ok, _err = handler.performRequests_error_([req], None)
        if not ok or not req.results():
            return None
        best: float | None = None
        for obs in req.results():
            q = obs.captureQuality()
            if q is not None and (best is None or float(q) > best):
                best = float(q)
        return best
    except Exception:
        return None


def feature_print(image: np.ndarray) -> np.ndarray | None:
    from Vision import VNGenerateImageFeaturePrintRequest, VNImageRequestHandler

    if image.ndim != 3 or image.shape[2] < 3 or image.size == 0:
        return None
    try:
        handler = VNImageRequestHandler.alloc().initWithData_options_(
            _image_to_nsdata(image), None
        )
        req = VNGenerateImageFeaturePrintRequest.alloc().init()
        ok, _err = handler.performRequests_error_([req], None)
        if not ok or not req.results():
            return None
        obs = req.results()[0]
        data = bytes(obs.data())
        if len(data) % 4 != 0:
            return None
        vec = np.frombuffer(data, dtype=np.float32)
        if vec.size == 0:
            return None
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return None
        return (vec / norm).astype(np.float32)
    except Exception:
        return None


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(a, b))


def assign_person(
    conn: sqlite3.Connection, embedding: np.ndarray, threshold: float
) -> int:
    rows = conn.execute(
        "SELECT f.person_id, f.embedding FROM faces f "
        "WHERE f.person_id IS NOT NULL AND f.embedding IS NOT NULL"
    ).fetchall()
    best_pid: int | None = None
    best_dist = threshold
    if rows:
        by_pid: dict[int, list[np.ndarray]] = {}
        for r in rows:
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            by_pid.setdefault(int(r["person_id"]), []).append(vec)
        for pid, vecs in by_pid.items():
            mat = np.stack(vecs)
            sims = mat @ embedding
            dist = float(1.0 - sims.max())
            if dist < best_dist:
                best_dist = dist
                best_pid = pid
    if best_pid is not None:
        return best_pid
    cur = conn.execute("INSERT INTO persons (sightings) VALUES (0)")
    if cur.lastrowid is None:
        raise RuntimeError("person insert failed")
    return int(cur.lastrowid)


def reconcile_persons(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM persons WHERE (name IS NULL OR name = '') AND id NOT IN "
        "(SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)"
    )
    conn.execute(
        "UPDATE persons SET sightings = "
        "(SELECT COUNT(*) FROM faces WHERE faces.person_id = persons.id)"
    )
    conn.commit()


def create_person(conn: sqlite3.Connection, name: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO persons (name, sightings) VALUES (?, 0)", (name,)
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("person insert failed")
    return int(cur.lastrowid)


def rename_person(conn: sqlite3.Connection, person_id: int, name: str) -> bool:
    cur = conn.execute(
        "UPDATE persons SET name = ? WHERE id = ?", (name, person_id)
    )
    conn.commit()
    return cur.rowcount > 0


def merge_persons(conn: sqlite3.Connection, src_id: int, dst_id: int) -> int:
    if src_id == dst_id:
        raise ValueError("cannot merge a person into itself")
    src = conn.execute(
        "SELECT name FROM persons WHERE id = ?", (src_id,)
    ).fetchone()
    dst = conn.execute(
        "SELECT name FROM persons WHERE id = ?", (dst_id,)
    ).fetchone()
    if src is None:
        raise ValueError(f"person {src_id} not found")
    if dst is None:
        raise ValueError(f"person {dst_id} not found")
    cur = conn.execute(
        "UPDATE faces SET person_id = ? WHERE person_id = ?", (dst_id, src_id)
    )
    moved = int(cur.rowcount)
    if dst["name"] is None or str(dst["name"]) == "":
        if src["name"] is not None and str(src["name"]) != "":
            conn.execute(
                "UPDATE persons SET name = ? WHERE id = ?", (src["name"], dst_id)
            )
    conn.execute("DELETE FROM persons WHERE id = ?", (src_id,))
    reconcile_persons(conn)
    return moved


def move_face(
    conn: sqlite3.Connection, face_id: int, person_id: int
) -> bool:
    face = conn.execute(
        "SELECT id FROM faces WHERE id = ?", (face_id,)
    ).fetchone()
    person = conn.execute(
        "SELECT id FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    if face is None:
        raise ValueError(f"face {face_id} not found")
    if person is None:
        raise ValueError(f"person {person_id} not found")
    conn.execute(
        "UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id)
    )
    reconcile_persons(conn)
    return True


def register_face(
    conn: sqlite3.Connection,
    cfg: Config,
    job_id: int,
    event_id: int,
    kf_index: int,
    face_index: int,
    crop_path: str,
    image: np.ndarray,
) -> int | None:
    if not cfg.identity.enabled:
        return None
    quality = face_capture_quality(image)
    if quality is None:
        if min(image.shape[:2]) < FALLBACK_MIN_CROP_PX:
            return None
        quality = 1.0
    if quality < cfg.identity.min_quality:
        return None
    embedding = feature_print(image)
    if embedding is None:
        return None
    existing = conn.execute(
        "SELECT id FROM faces WHERE event_id = ? AND keyframe_index = ? "
        "AND face_index = ?",
        (event_id, kf_index, face_index),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])
    person_id = assign_person(conn, embedding, cfg.identity.distance_threshold)
    cur = conn.execute(
        "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
        "crop_path, quality, embedding, person_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            event_id,
            kf_index,
            face_index,
            crop_path,
            quality,
            embedding.tobytes(),
            person_id,
        ),
    )
    conn.commit()
    reconcile_persons(conn)
    if cur.lastrowid is None:
        raise RuntimeError("face insert failed")
    return int(cur.lastrowid)


def index_existing_faces(conn: sqlite3.Connection, cfg: Config) -> int:
    import cv2

    from video_security import db as vsdb

    rows = conn.execute(
        "SELECT e.id, e.job_id, e.keyframes_json, e.faces_json FROM events e "
        "WHERE e.faces_json GLOB '*[0-9]*'"
    ).fetchall()
    registered = 0
    for row in rows:
        try:
            faces = __import__("json").loads(row["faces_json"])
        except Exception:
            continue
        for i in range(len(faces)):
            boxes = faces[i] if isinstance(faces[i], list) else []
            for j in range(len(boxes)):
                crop_path = vsdb.face_crop_path(
                    cfg, row["job_id"], row["id"], i, j
                )
                if crop_path is None:
                    continue
                already = conn.execute(
                    "SELECT id FROM faces WHERE event_id = ? AND keyframe_index = ? "
                    "AND face_index = ?",
                    (row["id"], i, j),
                ).fetchone()
                if already is not None:
                    continue
                img = cv2.imread(str(crop_path))
                if img is None:
                    continue
                fid = register_face(
                    conn,
                    cfg,
                    row["job_id"],
                    row["id"],
                    i,
                    j,
                    str(crop_path),
                    img,
                )
                if fid is not None:
                    registered += 1
    return registered
