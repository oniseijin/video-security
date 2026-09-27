from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import anyio
import pytest
from anyio.abc import TaskGroup
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.web import progress
from video_security.web.server import create_app, open_readonly


def _seed(db_path: Path) -> None:
    conn = connect(str(db_path))
    init_db(conn)
    conn.executescript(
        """
        INSERT INTO jobs (id, video_path, video_hash, status, current_stage,
                          current_frame, total_frames) VALUES
        (1, '/v/front_a.mp4', 'h1', 'done', 'done', 0, NULL),
        (2, '/v/rear_a.mp4', 'h2', 'extracting', 'decode', 128, 900),
        (3, '/v/front_b.mp4', 'h3', 'pending', 'pending', 0, NULL);
        INSERT INTO events (id, job_id, event_type, start_sec, end_sec, clip_id,
                            detector_score, priority, status)
        VALUES (10, 1, 'intrusion', 1.0, 2.0, 0, 0.9, 0.9, 'detailed');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "t.db"
    _seed(db_path)
    return db_path


def _make_app(db_file: Path, tmp_path: Path) -> FastAPI:
    cfg = Config()
    cfg.storage.db_path = str(db_file)
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    return create_app(cfg)


@pytest.fixture()
def client(db_file: Path, tmp_path: Path) -> Iterator[TestClient]:
    yield TestClient(_make_app(db_file, tmp_path))


def _set_job(db_path: Path, job_id: int, status: str, frame: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE jobs SET status = ?, current_frame = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, frame, job_id),
    )
    conn.commit()
    conn.close()


def _data_lines(raw: bytes) -> list[bytes]:
    return [
        line[len(b"data: ") :]
        for line in raw.splitlines()
        if line.startswith(b"data: ")
    ]


class _SseSession:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self.buf = b""
        self.started: MutableMapping[str, Any] | None = None
        self._request_read = False
        self._disconnect = anyio.Event()
        self._body_send: MemoryObjectSendStream[bytes]
        self._body_recv: MemoryObjectReceiveStream[bytes]
        self._body_send, self._body_recv = anyio.create_memory_object_stream(128)

    async def _receive(self) -> dict[str, Any]:
        if not self._request_read:
            self._request_read = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.started = message
        elif message["type"] == "http.response.body":
            await self._body_send.send(message.get("body", b""))

    async def _run(self) -> None:
        try:
            scope: dict[str, Any] = {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "path": "/api/progress/stream",
                "raw_path": b"/api/progress/stream",
                "root_path": "",
                "scheme": "http",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 123),
                "server": ("127.0.0.1", 80),
            }
            await self._app(scope, self._receive, self._send)
        finally:
            self._body_send.close()

    async def start(self, tg: TaskGroup) -> None:
        tg.start_soon(self._run)

    async def disconnect(self) -> None:
        self._disconnect.set()

    async def next_chunk(self, timeout: float = 5.0) -> bytes:
        with anyio.fail_after(timeout):
            return await self._body_recv.receive()

    async def next_event(self, timeout: float = 5.0) -> bytes:
        with anyio.fail_after(timeout):
            while b"\n\n" not in self.buf:
                self.buf += await self._body_recv.receive()
            raw, self.buf = self.buf.split(b"\n\n", 1)
            return raw


def test_progress_snapshot_shape(client: TestClient) -> None:
    resp = client.get("/api/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stats"]["jobs"]["total"] == 3
    assert data["stats"]["jobs"]["by_status"] == {
        "done": 1,
        "extracting": 1,
        "pending": 1,
    }
    assert data["stats"]["events"] == 1
    assert data["active"] == [
        {
            "id": 2,
            "status": "extracting",
            "current_stage": "decode",
            "current_frame": 128,
            "total_frames": 900,
            "label": "rear_a.mp4",
        },
    ]


def test_progress_stats_matches_stats_endpoint(client: TestClient) -> None:
    assert client.get("/api/progress").json()["stats"] == client.get(
        "/api/stats"
    ).json()


def test_progress_routes_registered(db_file: Path, tmp_path: Path) -> None:
    paths = {getattr(route, "path", "") for route in _make_app(db_file, tmp_path).routes}
    assert "/api/progress" in paths
    assert "/api/progress/stream" in paths


async def _run_until_disconnect(app: FastAPI) -> None:
    session = _SseSession(app)
    async with anyio.create_task_group() as tg:
        await session.start(tg)
        await session.next_event()
        await session.disconnect()


def test_progress_stream_first_frame(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(progress, "TICK_SEC", 0.05)

    async def scenario(app: FastAPI) -> tuple[MutableMapping[str, Any], bytes]:
        session = _SseSession(app)
        async with anyio.create_task_group() as tg:
            await session.start(tg)
            raw = await session.next_event()
            await session.disconnect()
        assert session.started is not None
        return session.started, raw

    started, raw = anyio.run(scenario, _make_app(db_file, tmp_path))
    assert started["status"] == 200
    headers = {k.decode().lower(): v.decode() for k, v in started["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert b"event: progress" in raw
    frames = _data_lines(raw)
    assert len(frames) == 1
    payload = json.loads(frames[0])
    assert payload["stats"]["jobs"]["total"] == 3
    assert payload["active"] == [
        {
            "id": 2,
            "status": "extracting",
            "current_stage": "decode",
            "current_frame": 128,
            "total_frames": 900,
            "label": "rear_a.mp4",
        },
    ]


def test_progress_stream_change_detection(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(progress, "TICK_SEC", 0.05)

    async def scenario(app: FastAPI, db_path: Path) -> None:
        session = _SseSession(app)
        async with anyio.create_task_group() as tg:
            await session.start(tg)
            first = json.loads(_data_lines(await session.next_event())[0])
            assert first["active"][0]["current_frame"] == 128
            _set_job(db_path, 3, "extracting", 5)
            second = json.loads(_data_lines(await session.next_event())[0])
            assert second["active"][1]["current_frame"] == 5
            with pytest.raises(TimeoutError):
                await session.next_event(timeout=0.5)
            await session.disconnect()

    anyio.run(scenario, _make_app(db_file, tmp_path), db_file)


def test_progress_stream_keepalive(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(progress, "TICK_SEC", 0.05)
    monkeypatch.setattr(progress, "HEARTBEAT_SEC", 0.2)

    async def scenario(app: FastAPI) -> None:
        session = _SseSession(app)
        async with anyio.create_task_group() as tg:
            await session.start(tg)
            assert _data_lines(await session.next_event())
            seen = False
            for _ in range(50):
                if b": keepalive" in await session.next_chunk(timeout=0.5):
                    seen = True
                    break
            assert seen
            await session.disconnect()

    anyio.run(scenario, _make_app(db_file, tmp_path))


def test_progress_stream_disconnect_closes_connection(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(progress, "TICK_SEC", 0.05)
    opened: list[sqlite3.Connection] = []

    def _track(path: str, check_same_thread: bool = True) -> sqlite3.Connection:
        conn = open_readonly(path, check_same_thread)
        opened.append(conn)
        return conn

    monkeypatch.setattr(progress, "open_readonly", _track)
    anyio.run(_run_until_disconnect, _make_app(db_file, tmp_path))
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
