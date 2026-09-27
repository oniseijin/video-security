from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

from fastapi import APIRouter
from pydantic import BaseModel, field_validator, model_validator

from video_security import db as vsdb
from video_security.identity import (
    create_person,
    merge_persons,
    move_face,
    rename_person,
)
from video_security.web.server import ApiError

TERMINAL_JOB_STATUSES = ("done", "failed")
TRANSIENT_JOB_STATUSES = ("extracting", "filtering", "triage", "detail")
RECENT_WRITE_WINDOW_SEC = 600
TRANSIENT_ACTIVITY_WINDOW_SEC = 3600

_WRITE_LOCK = threading.Lock()


class _NamedBody(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class PersonNameBody(_NamedBody):
    pass


class PersonCreateBody(_NamedBody):
    pass


class MergeBody(BaseModel):
    source_id: int
    target_id: int


class AssignBody(BaseModel):
    person_id: int | None = None
    new_person_name: str | None = None

    @field_validator("new_person_name")
    @classmethod
    def _non_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.person_id is None) == (self.new_person_name is None):
            raise ValueError("specify exactly one of person_id, new_person_name")
        return self


class FlagBody(BaseModel):
    note: str | None = None


def _connect_rw(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(db_path).expanduser()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _require_idle(conn: sqlite3.Connection) -> None:
    terminal = ",".join("?" * len(TERMINAL_JOB_STATUSES))
    transient = ",".join("?" * len(TRANSIENT_JOB_STATUSES))
    busy = conn.execute(
        f"SELECT 1 FROM jobs WHERE status NOT IN ({terminal}) AND ("
        "updated_at > datetime('now', ?) OR ("
        f"status IN ({transient}) AND updated_at > datetime('now', ?)"
        ")) LIMIT 1",
        (
            *TERMINAL_JOB_STATUSES,
            f"-{RECENT_WRITE_WINDOW_SEC} seconds",
            *TRANSIENT_JOB_STATUSES,
            f"-{TRANSIENT_ACTIVITY_WINDOW_SEC} seconds",
        ),
    ).fetchone()
    if busy is not None:
        raise ApiError(
            409, "analyze batch active — mutations are disabled until it finishes"
        )


def _mutate(
    db_path: str, fn: Callable[[sqlite3.Connection], dict[str, Any]]
) -> dict[str, Any]:
    with _WRITE_LOCK:
        conn = _connect_rw(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_idle(conn)
            result = fn(conn)
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc) or "busy" in str(exc):
                raise ApiError(503, "database busy — mutation not applied") from exc
            raise
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
            return result
        finally:
            conn.close()


def _require_person(conn: sqlite3.Connection, person_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, f"person {person_id} not found")


def _rename(
    conn: sqlite3.Connection, person_id: int, name: str
) -> dict[str, Any]:
    _require_person(conn, person_id)
    if not rename_person(conn, person_id, name):
        raise ApiError(404, f"person {person_id} not found")
    return {"ok": True, "person_id": person_id, "name": name}


def _create(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    person_id = create_person(conn, name)
    return {"ok": True, "person_id": person_id, "name": name}


def _merge(
    conn: sqlite3.Connection, source_id: int, target_id: int
) -> dict[str, Any]:
    if source_id == target_id:
        raise ApiError(409, "cannot merge a person into itself")
    _require_person(conn, source_id)
    _require_person(conn, target_id)
    moved = merge_persons(conn, source_id, target_id)
    return {
        "ok": True,
        "source_id": source_id,
        "target_id": target_id,
        "moved": moved,
    }


def _assign(
    conn: sqlite3.Connection, face_id: int, body: AssignBody
) -> dict[str, Any]:
    face = conn.execute("SELECT id FROM faces WHERE id = ?", (face_id,)).fetchone()
    if face is None:
        raise ApiError(404, f"face {face_id} not found")
    created = False
    if body.person_id is not None:
        _require_person(conn, body.person_id)
        target = body.person_id
    else:
        target = create_person(conn, body.new_person_name)
        created = True
    move_face(conn, face_id, target)
    return {
        "ok": True,
        "face_id": face_id,
        "person_id": target,
        "created_person": created,
    }


def _event_row(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, status, llm_result_id FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, f"event {event_id} not found")
    return row


def _suppress(conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    row = _event_row(conn, event_id)
    vsdb.update_event_status(conn, event_id, "suppressed", row["llm_result_id"])
    return {"ok": True, "event_id": event_id, "status": "suppressed"}


def _restore(conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    row = _event_row(conn, event_id)
    status = vsdb.derived_event_status(conn, event_id)
    vsdb.update_event_status(conn, event_id, status, row["llm_result_id"])
    return {"ok": True, "event_id": event_id, "status": status}


def _flag(
    conn: sqlite3.Connection, job_id: int, note: str | None
) -> dict[str, Any]:
    stored = note if note is not None else ""
    if not vsdb.set_job_flag(conn, job_id, stored):
        raise ApiError(404, f"job {job_id} not found")
    return {"ok": True, "job_id": job_id, "flag_note": stored}


def _unflag(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    if not vsdb.set_job_flag(conn, job_id, None):
        raise ApiError(404, f"job {job_id} not found")
    return {"ok": True, "job_id": job_id, "flag_note": None}


def write_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.post("/api/persons", response_model=None)
    def create_person_ep(body: PersonCreateBody) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _create(conn, body.name))

    @router.post("/api/persons/merge", response_model=None)
    def merge_persons_ep(body: MergeBody) -> dict[str, Any]:
        return _mutate(
            db_path, lambda conn: _merge(conn, body.source_id, body.target_id)
        )

    @router.post("/api/persons/{person_id}/name", response_model=None)
    def rename_person_ep(person_id: int, body: PersonNameBody) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _rename(conn, person_id, body.name))

    @router.post("/api/faces/{face_id}/assign", response_model=None)
    def assign_face_ep(face_id: int, body: AssignBody) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _assign(conn, face_id, body))

    @router.post("/api/events/{event_id}/suppress", response_model=None)
    def suppress_event_ep(event_id: int) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _suppress(conn, event_id))

    @router.post("/api/events/{event_id}/restore", response_model=None)
    def restore_event_ep(event_id: int) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _restore(conn, event_id))

    @router.post("/api/jobs/{job_id}/flag", response_model=None)
    def flag_job_ep(job_id: int, body: FlagBody) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _flag(conn, job_id, body.note))

    @router.post("/api/jobs/{job_id}/unflag", response_model=None)
    def unflag_job_ep(job_id: int) -> dict[str, Any]:
        return _mutate(db_path, lambda conn: _unflag(conn, job_id))

    return router
