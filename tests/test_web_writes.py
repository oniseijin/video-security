from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from video_security.cli import app as cli_app
from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web.server import create_app

runner = CliRunner()

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300ffd9")


def _seed_db(db_path: Path) -> None:
    conn = connect(str(db_path))
    init_db(conn)
    faces_dir = db_path.parent / "artifacts" / "faces"
    crop1 = faces_dir / "1" / "face_10_0_0.jpg"
    crop2 = faces_dir / "2" / "face_12_0_0.jpg"
    crop1.parent.mkdir(parents=True, exist_ok=True)
    crop2.parent.mkdir(parents=True, exist_ok=True)
    crop1.write_bytes(JPEG)
    crop2.write_bytes(JPEG)
    conn.executescript(
        """
        INSERT INTO jobs (id, video_path, video_hash, status) VALUES
        (1, '/v/a.mp4', 'h1', 'done'),
        (2, '/v/b.mp4', 'h2', 'done');
        INSERT INTO events (id, job_id, event_type, start_sec, end_sec, clip_id,
                            keyframes_json, faces_json, detector_score, priority,
                            status, llm_result_id)
        VALUES
        (10, 1, 'intrusion', 5.0, 6.0, 0, NULL,
         '[[[0.1, 0.2, 0.3, 0.4]]]', 0.9, 0.9, 'detailed', NULL),
        (11, 1, 'loitering', 8.0, 9.0, 0, NULL, NULL, 0.5, 0.5, 'detailed', 1),
        (12, 2, 'intrusion', 2.0, 3.0, 0, NULL,
         '[[[0.2, 0.3, 0.4, 0.5]]]', 0.8, 0.8, 'detailed', NULL);
        INSERT INTO analysis_results (id, job_id, event_id, model_digest,
                                      prompt_version, analysis_type, raw_response,
                                      confidence, retry_count)
        VALUES (1, 1, 11, 'sha256:a', 'v1', 'detail', '{"description": "x"}', NULL, 0);
        INSERT INTO persons (id, name, sightings) VALUES (1, 'Kenji', 1), (2, NULL, 1);
        """
    )
    for event_id, crop in ((10, crop1), (12, crop2)):
        conn.execute(
            "UPDATE events SET keyframes_json = ? WHERE id = ?",
            (json.dumps([str(crop)]), event_id),
        )
    conn.execute(
        "INSERT INTO faces (id, job_id, event_id, keyframe_index, face_index, "
        "crop_path, quality, person_id) VALUES (1, 1, 10, 0, 0, ?, 0.9, 1)",
        (str(crop1),),
    )
    conn.execute(
        "INSERT INTO faces (id, job_id, event_id, keyframe_index, face_index, "
        "crop_path, quality, person_id) VALUES (2, 2, 12, 0, 0, ?, 0.8, 2)",
        (str(crop2),),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "t.db"
    _seed_db(db_path)
    return db_path


@pytest.fixture()
def client(db_file: Path, tmp_path: Path) -> Iterator[TestClient]:
    cfg = Config()
    cfg.storage.db_path = str(db_file)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    yield TestClient(create_app(cfg))


_PROJECTIONS = {
    "persons": "SELECT id, name, sightings FROM persons ORDER BY id",
    "faces": "SELECT id, person_id FROM faces ORDER BY id",
    "jobs": "SELECT id, flag_note FROM jobs ORDER BY id",
}


def _dump(
    db_path: Path, tables: tuple[str, ...] = ("persons", "faces")
) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(str(db_path))
    out = {t: conn.execute(_PROJECTIONS[t]).fetchall() for t in tables}
    conn.close()
    return out


def _person_rows(db_path: Path) -> list[tuple[Any, ...]]:
    return _dump(db_path, ("persons",))["persons"]


def _set_job_status(db_path: Path, job_id: int, status: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    conn.commit()
    conn.close()


def _post(client: TestClient, path: str, body: Any = None) -> Any:
    return client.post(path, json=body if body is not None else {})


def test_faces_read_includes_face_ids(client: TestClient) -> None:
    resp = client.get("/api/faces")
    assert resp.status_code == 200
    items = resp.json()["items"]
    by_event = {item["event_id"]: item for item in items}
    assert by_event[10]["face_ids"] == [1]
    assert by_event[10]["person_ids"] == [1]
    assert by_event[12]["face_ids"] == [2]


def test_person_rename_web_and_cli_parity(
    client: TestClient, db_file: Path, tmp_path: Path
) -> None:
    cli_db = tmp_path / "cli.db"
    _seed_db(cli_db)

    resp = _post(client, "/api/persons/1/name", {"name": "A"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "person_id": 1, "name": "A"}

    result = runner.invoke(cli_app, ["--db", str(cli_db), "person", "name", "1", "A"])
    assert result.exit_code == 0

    assert _person_rows(db_file) == _person_rows(cli_db)
    assert _person_rows(db_file) == [(1, "A", 1), (2, None, 1)]
    listed = client.get("/api/persons").json()["items"]
    assert listed[0]["name"] == "A"


def test_person_create(client: TestClient, db_file: Path) -> None:
    resp = _post(client, "/api/persons", {"name": "B"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "person_id": 3, "name": "B"}
    assert _person_rows(db_file)[2] == (3, "B", 0)
    listed = client.get("/api/persons").json()
    assert listed["total"] == 3
    assert any(p["person_id"] == 3 and p["sightings"] == 0 for p in listed["items"])


def test_person_merge_web_and_cli_parity(
    client: TestClient, db_file: Path, tmp_path: Path
) -> None:
    cli_db = tmp_path / "cli.db"
    _seed_db(cli_db)

    resp = _post(client, "/api/persons/merge", {"source_id": 1, "target_id": 2})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "source_id": 1,
        "target_id": 2,
        "moved": 1,
    }

    result = runner.invoke(cli_app, ["--db", str(cli_db), "person", "merge", "1", "2"])
    assert result.exit_code == 0

    assert _dump(db_file) == _dump(cli_db)
    assert _person_rows(db_file) == [(2, "Kenji", 2)]

    detail = client.get("/api/persons/2").json()
    assert detail["name"] == "Kenji"
    assert detail["total"] == 2


def test_merge_rejects_self_and_unknown(client: TestClient, db_file: Path) -> None:
    before = _dump(db_file)
    resp = _post(client, "/api/persons/merge", {"source_id": 1, "target_id": 1})
    assert resp.status_code == 409
    assert "error" in resp.json()
    resp = _post(client, "/api/persons/merge", {"source_id": 9, "target_id": 1})
    assert resp.status_code == 404
    resp = _post(client, "/api/persons/merge", {"source_id": 1, "target_id": 9})
    assert resp.status_code == 404
    assert _dump(db_file) == before


def test_assign_face_to_existing_person(client: TestClient, db_file: Path) -> None:
    resp = _post(client, "/api/faces/2/assign", {"person_id": 1})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "face_id": 2,
        "person_id": 1,
        "created_person": False,
    }
    conn = sqlite3.connect(str(db_file))
    moved = conn.execute("SELECT person_id FROM faces WHERE id = 2").fetchone()[0]
    conn.close()
    assert moved == 1
    assert _person_rows(db_file) == [(1, "Kenji", 2)]


def test_assign_face_creates_new_person(client: TestClient, db_file: Path) -> None:
    resp = _post(client, "/api/faces/1/assign", {"new_person_name": "Casey"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "face_id": 1,
        "person_id": 3,
        "created_person": True,
    }
    conn = sqlite3.connect(str(db_file))
    moved = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()[0]
    name = conn.execute("SELECT name FROM persons WHERE id = 3").fetchone()[0]
    conn.close()
    assert moved == 3
    assert name == "Casey"
    assert _person_rows(db_file) == [(1, "Kenji", 0), (2, None, 1), (3, "Casey", 1)]


def test_assign_validation_and_no_partial_writes(
    client: TestClient, db_file: Path
) -> None:
    before = _dump(db_file)
    assert (
        _post(client, "/api/faces/1/assign", {"person_id": 1, "new_person_name": "X"})
        .status_code
        == 422
    )
    neither = _post(client, "/api/faces/1/assign", {})
    assert neither.status_code == 422
    assert "error" in neither.json()
    assert _post(client, "/api/faces/1/assign", {"new_person_name": "   "}).status_code == 422
    assert _post(client, "/api/persons/1/name", {"name": ""}).status_code == 422
    assert _post(client, "/api/persons/abc/name", {"name": "X"}).status_code == 422
    assert _dump(db_file) == before


def test_unknown_ids_404(client: TestClient, db_file: Path) -> None:
    before = _dump(db_file)
    cases = [
        ("/api/persons/99/name", {"name": "X"}),
        ("/api/faces/99/assign", {"person_id": 1}),
        ("/api/faces/1/assign", {"person_id": 99}),
        ("/api/events/99/suppress", None),
        ("/api/events/99/restore", None),
        ("/api/jobs/99/flag", {"note": "x"}),
        ("/api/jobs/99/unflag", None),
    ]
    for path, body in cases:
        resp = _post(client, path, body)
        assert resp.status_code == 404, path
        assert "error" in resp.json(), path
    assert _dump(db_file) == before


def test_active_batch_guard(client: TestClient, db_file: Path) -> None:
    _set_job_status(db_file, 1, "extracting")
    resp = _post(client, "/api/persons/1/name", {"name": "A"})
    assert resp.status_code == 409
    assert "error" in resp.json()
    assert _person_rows(db_file) == [(1, "Kenji", 1), (2, None, 1)]

    _set_job_status(db_file, 1, "pending")
    assert _post(client, "/api/persons/1/name", {"name": "A"}).status_code == 409

    _set_job_status(db_file, 1, "done")
    resp = _post(client, "/api/persons/1/name", {"name": "A"})
    assert resp.status_code == 200
    assert _person_rows(db_file)[0] == (1, "A", 1)


def test_event_suppress_and_restore(client: TestClient, db_file: Path) -> None:
    resp = _post(client, "/api/events/11/suppress")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "event_id": 11, "status": "suppressed"}
    conn = sqlite3.connect(str(db_file))
    row = conn.execute(
        "SELECT status, llm_result_id FROM events WHERE id = 11"
    ).fetchone()
    conn.close()
    assert row == ("suppressed", 1)
    assert client.get("/api/events/11").json()["status"] == "suppressed"

    resp = _post(client, "/api/events/11/restore")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "event_id": 11, "status": "detailed"}
    conn = sqlite3.connect(str(db_file))
    row = conn.execute(
        "SELECT status, llm_result_id FROM events WHERE id = 11"
    ).fetchone()
    conn.close()
    assert row == ("detailed", 1)


def test_job_flag_and_unflag(client: TestClient, db_file: Path, tmp_path: Path) -> None:
    resp = _post(client, "/api/jobs/1/flag", {"note": "check plates"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "job_id": 1, "flag_note": "check plates"}
    assert client.get("/api/jobs/1").json()["flag_note"] == "check plates"

    no_note = _post(client, "/api/jobs/2/flag", {"note": None})
    assert no_note.status_code == 200
    assert no_note.json()["flag_note"] == ""
    assert client.get("/api/jobs/2").json()["flag_note"] == ""

    cli_db = tmp_path / "cli.db"
    _seed_db(cli_db)
    result = runner.invoke(cli_app, ["--db", str(cli_db), "job", "flag", "1"])
    assert result.exit_code == 0
    conn = sqlite3.connect(str(cli_db))
    cli_note = conn.execute("SELECT flag_note FROM jobs WHERE id = 1").fetchone()[0]
    conn.close()
    assert cli_note == ""

    resp = _post(client, "/api/jobs/1/unflag")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "job_id": 1, "flag_note": None}
    conn = sqlite3.connect(str(db_file))
    note = conn.execute("SELECT flag_note FROM jobs WHERE id = 1").fetchone()[0]
    conn.close()
    assert note is None
    assert client.get("/api/jobs/1").json()["flag_note"] is None


def test_reads_still_work_after_mutations(client: TestClient) -> None:
    assert _post(client, "/api/persons/1/name", {"name": "A"}).status_code == 200
    assert _post(client, "/api/faces/2/assign", {"person_id": 1}).status_code == 200
    assert _post(client, "/api/events/11/suppress").status_code == 200
    assert _post(client, "/api/jobs/1/flag", {"note": "x"}).status_code == 200
    for path in (
        "/api/health",
        "/api/stats",
        "/api/jobs",
        "/api/jobs/1",
        "/api/events/11",
        "/api/persons",
        "/api/persons/1",
        "/api/faces",
        "/api/jobs/1/faces",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
    assert client.get("/api/persons/1").json()["total"] == 2
    assert client.get("/api/jobs/1").json()["flag_note"] == "x"
