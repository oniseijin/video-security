from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

from video_security.config import Config
from video_security.web.api import stats_payload
from video_security.web.server import _json_response, open_readonly
from video_security.web.writes import TRANSIENT_JOB_STATUSES

TICK_SEC = 1.0
HEARTBEAT_SEC = 15.0


def active_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(TRANSIENT_JOB_STATUSES))
    rows = conn.execute(
        "SELECT id, status, current_stage, current_frame, total_frames, video_path "
        f"FROM jobs WHERE status IN ({placeholders}) ORDER BY id",
        TRANSIENT_JOB_STATUSES,
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "status": str(row["status"]),
            "current_stage": row["current_stage"],
            "current_frame": int(row["current_frame"] or 0),
            "total_frames": (
                int(row["total_frames"])
                if row["total_frames"] is not None
                else None
            ),
            "label": PurePosixPath(str(row["video_path"])).name,
        }
        for row in rows
    ]


def progress_payload(conn: sqlite3.Connection, cfg: Config) -> dict[str, Any]:
    return {"stats": stats_payload(conn, cfg), "active": active_jobs(conn)}


def _sse_frame(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode()
    return b"event: progress\ndata: " + data + b"\n\n"


async def progress_stream(
    conn: sqlite3.Connection, cfg: Config
) -> AsyncIterator[bytes]:
    last: bytes | None = None
    last_sent = time.monotonic()
    try:
        while True:
            payload = await asyncio.to_thread(progress_payload, conn, cfg)
            frame = _sse_frame(payload)
            if frame != last:
                last = frame
                last_sent = time.monotonic()
                yield frame
            elif time.monotonic() - last_sent >= HEARTBEAT_SEC:
                last_sent = time.monotonic()
                yield b": keepalive\n\n"
            await asyncio.sleep(TICK_SEC)
    finally:
        conn.close()


def add_progress_routes(app: FastAPI, db_path: str, cfg: Config) -> None:
    @app.get("/api/progress/stream", include_in_schema=False, response_model=None)
    async def progress_stream_ep() -> StreamingResponse:
        conn = open_readonly(db_path, check_same_thread=False)
        return StreamingResponse(
            progress_stream(conn, cfg),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/progress", include_in_schema=False, response_model=None)
    def progress_snapshot_ep() -> Response:
        conn = open_readonly(db_path)
        try:
            return _json_response(200, progress_payload(conn, cfg))
        finally:
            conn.close()
