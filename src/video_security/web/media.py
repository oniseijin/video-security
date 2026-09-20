from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
from pathlib import Path
from typing import Any

from video_security.config import Config
from video_security.web.server import ApiError, FileRange, Routes

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str, total: int) -> tuple[int, int]:
    match = _RANGE_RE.match(header.strip())
    if match is None:
        raise ApiError(416, "invalid range")
    start_raw, end_raw = match.group(1), match.group(2)
    if not start_raw and not end_raw:
        raise ApiError(416, "invalid range")
    if not start_raw:
        length = int(end_raw)
        start = max(0, total - length)
        end = total - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else total - 1
    if start >= total or start > end:
        raise ApiError(416, "range not satisfiable")
    return start, min(end, total - 1)


def _artifact_file(cfg: Config, sub: str, job_id: int, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ApiError(404, "not found")
    artifact = Path(cfg.storage.artifact_dir).expanduser().resolve()
    candidate = (artifact / sub / str(job_id) / name).resolve()
    if not candidate.is_relative_to(artifact) or not candidate.is_file():
        raise ApiError(404, "not found")
    return candidate


def _image_response(path: Path) -> tuple[int, str, bytes]:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return 200, ctype, path.read_bytes()


def frame_image(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> tuple[int, str, bytes]:
    path = _artifact_file(cfg, "frames", int(params["job_id"]), str(params["name"]))
    return _image_response(path)


def plate_image(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> tuple[int, str, bytes]:
    path = _artifact_file(cfg, "plates", int(params["job_id"]), str(params["name"]))
    return _image_response(path)


def _pair_video_path(conn: sqlite3.Connection, job_id: int) -> str | None:
    row = conn.execute(
        "SELECT clips_json FROM sessions WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        paths = json.loads(row["clips_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(paths, list):
        return None
    for path in paths:
        if not isinstance(path, str):
            continue
        other = conn.execute(
            "SELECT id, video_path FROM jobs WHERE video_path = ?", (path,)
        ).fetchone()
        if other is not None and int(other["id"]) != job_id:
            return str(other["video_path"])
    return None


def serve_video(
    conn: sqlite3.Connection, cfg: Config, params: dict[str, Any]
) -> FileRange:
    job_id = int(params["id"])
    job = conn.execute(
        "SELECT video_path FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job is None:
        raise ApiError(404, "job not found")
    video_str = str(job["video_path"])
    channel = str(params.get("channel", "front"))
    if channel == "rear":
        pair = _pair_video_path(conn, job_id)
        if pair is None:
            raise ApiError(404, "no rear pair")
        video_str = pair
    video = Path(video_str).expanduser()
    if not video.is_file():
        raise ApiError(404, "video file missing")
    total = video.stat().st_size
    headers = params.get("_headers")
    range_header = ""
    if isinstance(headers, dict):
        range_header = str(headers.get("range", ""))
    if range_header:
        start, end = _parse_range(range_header, total)
        return FileRange(
            status=206,
            ctype="video/mp4",
            length=end - start + 1,
            extra_headers=[
                ("Content-Range", f"bytes {start}-{end}/{total}"),
                ("Accept-Ranges", "bytes"),
            ],
            path=video,
            offset=start,
        )
    return FileRange(
        status=200,
        ctype="video/mp4",
        length=total,
        extra_headers=[("Accept-Ranges", "bytes")],
        path=video,
        offset=0,
    )


def media_routes() -> Routes:
    return [
        ("GET", "/media/frames/{job_id:int}/{name}", frame_image),
        ("GET", "/media/plates/{job_id:int}/{name}", plate_image),
        ("GET", "/api/jobs/{id:int}/video", serve_video),
    ]
