from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.identity import (
    assign_person,
    cosine_distance,
    create_person,
    merge_persons,
    move_face,
    reconcile_persons,
    register_face,
    rename_person,
)


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def _cfg() -> Config:
    cfg = Config()
    cfg.identity.enabled = True
    cfg.identity.min_quality = 0.1
    cfg.identity.distance_threshold = 0.4
    return cfg


def _img(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(96, 96, 3), dtype=np.uint8)


def test_cosine_distance() -> None:
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 0.0], dtype=np.float32)
    c = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_distance(a, b) == pytest.approx(0.0, abs=1e-6)
    assert cosine_distance(a, c) == pytest.approx(1.0, abs=1e-6)


def test_assign_person_clusters_same_vector(
    db_conn: sqlite3.Connection,
) -> None:
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    p1 = assign_person(db_conn, v, 0.4)
    db_conn.execute(
        "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
        "crop_path, embedding, person_id) VALUES (1, 1, 0, 0, '/x.jpg', ?, ?)",
        (v.tobytes(), p1),
    )
    db_conn.commit()
    p2 = assign_person(db_conn, v, 0.4)
    assert p2 == p1
    w = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    p3 = assign_person(db_conn, w, 0.4)
    assert p3 != p1


def test_register_face_quality_gate(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.05
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print", lambda _img: None
    )
    fid = register_face(
        db_conn, _cfg(), 1, 10, 0, 0, "/x.jpg", _img(1)
    )
    assert fid is None
    assert db_conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0


def test_register_face_none_quality_fallback(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: None
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda _img: np.ones(4, dtype=np.float32) / 2.0,
    )
    small = np.zeros((32, 32, 3), dtype=np.uint8)
    assert register_face(db_conn, _cfg(), 1, 10, 0, 0, "/s.jpg", small) is None
    assert db_conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0

    big = np.zeros((64, 64, 3), dtype=np.uint8)
    fid = register_face(db_conn, _cfg(), 1, 11, 0, 0, "/b.jpg", big)
    assert fid is not None
    row = db_conn.execute(
        "SELECT quality FROM faces WHERE id = ?", (fid,)
    ).fetchone()
    assert row["quality"] == 1.0


def test_register_face_clustering(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda img: (np.ones(4, dtype=np.float32) / 2.0)
        if img[0, 0, 0] < 128
        else np.array([0, 0, 0, 1], dtype=np.float32),
    )
    img_a = _img(1)
    img_a[0, 0, 0] = 10
    img_a2 = _img(2)
    img_a2[0, 0, 0] = 10
    img_b = _img(3)
    img_b[0, 0, 0] = 200
    f1 = register_face(db_conn, _cfg(), 1, 10, 0, 0, "/a.jpg", img_a)
    f2 = register_face(db_conn, _cfg(), 1, 11, 0, 0, "/a2.jpg", img_a2)
    f3 = register_face(db_conn, _cfg(), 1, 12, 0, 0, "/b.jpg", img_b)
    assert f1 is not None and f2 is not None and f3 is not None
    rows = db_conn.execute(
        "SELECT id, person_id FROM faces ORDER BY id"
    ).fetchall()
    assert rows[0]["person_id"] == rows[1]["person_id"]
    assert rows[1]["person_id"] != rows[2]["person_id"]
    persons = db_conn.execute(
        "SELECT id, sightings FROM persons ORDER BY id"
    ).fetchall()
    assert {p["sightings"] for p in persons} == {1, 2}


def test_reconcile_persons_removes_empty(
    db_conn: sqlite3.Connection,
) -> None:
    v = np.array([1.0, 0.0], dtype=np.float32)
    p1 = assign_person(db_conn, v, 0.4)
    db_conn.execute(
        "INSERT INTO faces (job_id, event_id, keyframe_index, face_index, "
        "crop_path, embedding, person_id) VALUES (1, 1, 0, 0, '/x.jpg', ?, ?)",
        (v.tobytes(), p1),
    )
    db_conn.execute(
        "INSERT INTO persons (sightings) VALUES (5)"
    )
    db_conn.commit()
    reconcile_persons(db_conn)
    ids = [r[0] for r in db_conn.execute("SELECT id FROM persons")]
    assert p1 in ids
    assert len(ids) == 1
    sightings = db_conn.execute(
        "SELECT sightings FROM persons WHERE id = ?", (p1,)
    ).fetchone()[0]
    assert sightings == 1


def test_feature_print_real_vision() -> None:
    pytest.importorskip("Vision")
    from video_security.identity import feature_print

    img1 = np.zeros((64, 64, 3), dtype=np.uint8)
    img1[20:40, 20:40] = 255
    img2 = img1.copy()
    v1 = feature_print(img1)
    v2 = feature_print(img2)
    if v1 is None or v2 is None:
        pytest.skip("Vision feature print unavailable")
    assert v1.shape == v2.shape
    assert cosine_distance(v1, v2) < 0.05


def test_index_existing_faces(
    db_conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cv2

    cfg = _cfg()
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    faces_dir = tmp_path / "artifacts" / "faces" / "1"
    faces_dir.mkdir(parents=True)
    crop = faces_dir / "face_10_0_0.jpg"
    cv2.imwrite(str(crop), np.zeros((60, 80, 3), dtype=np.uint8))
    db_conn.execute(
        "INSERT INTO events (id, job_id, event_type, start_sec, end_sec, "
        "clip_id, track_id, keyframes_json, faces_json, detector_score, "
        "priority, status) VALUES (10, 1, 'intrusion', 1.0, 2.0, 0, NULL, "
        "'[]', '[[[0.1, 0.2, 0.3, 0.4]]]', 0.5, 0.5, 'detailed')"
    )
    db_conn.commit()
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda _img: np.ones(4, dtype=np.float32) / 2.0,
    )
    from video_security.identity import index_existing_faces

    n = index_existing_faces(db_conn, cfg)
    assert n == 1
    row = db_conn.execute(
        "SELECT crop_path, person_id FROM faces"
    ).fetchone()
    assert row["crop_path"] == str(crop)
    assert row["person_id"] is not None
    n2 = index_existing_faces(db_conn, cfg)
    assert n2 == 0
    _ = json


def test_rename_person(db_conn: sqlite3.Connection) -> None:
    v = np.array([1.0, 0.0], dtype=np.float32)
    p1 = assign_person(db_conn, v, 0.4)
    assert rename_person(db_conn, p1, "Kenji") is True
    row = db_conn.execute(
        "SELECT name FROM persons WHERE id = ?", (p1,)
    ).fetchone()
    assert row["name"] == "Kenji"
    assert rename_person(db_conn, 9999, "Nobody") is False


def test_merge_persons_moves_faces_and_carries_name(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda img: (np.ones(4, dtype=np.float32) / 2.0)
        if img[0, 0, 0] < 128
        else np.array([0, 0, 0, 1], dtype=np.float32),
    )
    img_a = _img(1)
    img_a[0, 0, 0] = 10
    img_b = _img(3)
    img_b[0, 0, 0] = 200
    f1 = register_face(db_conn, _cfg(), 1, 10, 0, 0, "/a.jpg", img_a)
    f2 = register_face(db_conn, _cfg(), 1, 11, 0, 0, "/a2.jpg", img_a)
    f3 = register_face(db_conn, _cfg(), 1, 12, 0, 0, "/b.jpg", img_b)
    assert f1 is not None and f2 is not None and f3 is not None

    def person_of(face_id: int) -> int:
        return int(
            db_conn.execute(
                "SELECT person_id FROM faces WHERE id = ?", (face_id,)
            ).fetchone()["person_id"]
        )

    p_a, p_b = person_of(f1), person_of(f3)
    assert p_a != p_b
    assert rename_person(db_conn, p_b, "Mika") is True

    moved = merge_persons(db_conn, p_b, p_a)
    assert moved == 1
    ids = [r[0] for r in db_conn.execute("SELECT id FROM persons")]
    assert p_b not in ids and p_a in ids
    row = db_conn.execute(
        "SELECT name, sightings FROM persons WHERE id = ?", (p_a,)
    ).fetchone()
    assert row["sightings"] == 3
    assert row["name"] == "Mika"

    with pytest.raises(ValueError):
        merge_persons(db_conn, p_a, p_a)
    with pytest.raises(ValueError):
        merge_persons(db_conn, 9999, p_a)


def test_move_face_reassigns_cluster(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.identity.face_capture_quality", lambda _img: 0.9
    )
    monkeypatch.setattr(
        "video_security.identity.feature_print",
        lambda img: (np.ones(4, dtype=np.float32) / 2.0)
        if img[0, 0, 0] < 128
        else np.array([0, 0, 0, 1], dtype=np.float32),
    )
    img_a = _img(1)
    img_a[0, 0, 0] = 10
    img_b = _img(3)
    img_b[0, 0, 0] = 200
    f1 = register_face(db_conn, _cfg(), 1, 10, 0, 0, "/a.jpg", img_a)
    f2 = register_face(db_conn, _cfg(), 1, 11, 0, 0, "/b.jpg", img_b)
    assert f1 is not None and f2 is not None
    p1 = db_conn.execute(
        "SELECT person_id FROM faces WHERE id = ?", (f1,)
    ).fetchone()["person_id"]
    p2 = db_conn.execute(
        "SELECT person_id FROM faces WHERE id = ?", (f2,)
    ).fetchone()["person_id"]
    assert p1 != p2

    assert move_face(db_conn, f2, int(p1)) is True
    new_p2 = db_conn.execute(
        "SELECT person_id FROM faces WHERE id = ?", (f2,)
    ).fetchone()["person_id"]
    assert new_p2 == p1

    with pytest.raises(ValueError):
        move_face(db_conn, 9999, int(p1))
    with pytest.raises(ValueError):
        move_face(db_conn, f2, 9999)


def test_create_person_and_reconcile_keeps_named(
    db_conn: sqlite3.Connection,
) -> None:
    named = create_person(db_conn, "Mika")
    unnamed = create_person(db_conn)
    assert isinstance(named, int) and isinstance(unnamed, int)
    reconcile_persons(db_conn)
    ids = [r[0] for r in db_conn.execute("SELECT id FROM persons")]
    assert named in ids
    assert unnamed not in ids
