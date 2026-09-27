from __future__ import annotations

import dataclasses
import json
import mimetypes
import sqlite3
import threading
import webbrowser
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse

from video_security.config import Config
from video_security.db import init_db

Endpoint = Callable[[sqlite3.Connection, Config, dict[str, Any]], Any]
Route = tuple[str, str, Endpoint]
Routes = list[Route]

STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_ROOT = STATIC_DIR.resolve()


@dataclasses.dataclass
class FileRange:
    status: int
    ctype: str
    length: int
    extra_headers: list[tuple[str, str]]
    path: Path
    offset: int


_PLACEHOLDER = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>VS // CONSOLE</title>"
    "<style>body{background:#000;color:#fff;margin:0;height:100vh;display:grid;"
    "place-items:center;font-family:ui-monospace,Menlo,monospace}"
    "main{text-align:center}h1{letter-spacing:.3em;font-weight:400}"
    "p{color:#888;font-size:.8rem}</style></head>"
    "<body><main><h1>VS // CONSOLE</h1>"
    "<p>web bundle not built — phase 5 pending</p></main></body></html>"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def open_readonly(db_path: str) -> sqlite3.Connection:
    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class Connections:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = open_readonly(self._db_path)
            self._local.conn = conn
        return conn


def _json_response(status: int, payload: dict[str, Any]) -> Response:
    body = json.dumps(payload).encode()
    return _body_response(status, "application/json; charset=utf-8", body)


def _body_response(status: int, ctype: str, body: bytes, cache: str = "no-cache") -> Response:
    return Response(
        content=body,
        status_code=status,
        media_type=ctype,
        headers={"Cache-Control": cache},
    )


def _file_response(path: Path) -> Response:
    cache = "no-cache"
    if "assets" in path.parts:
        cache = "public, max-age=31536000, immutable"
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _body_response(200, ctype, path.read_bytes(), cache)


def _file_range_response(fr: FileRange) -> Response:
    headers = {"Cache-Control": "no-cache", "Content-Length": str(fr.length)}
    for key, value in fr.extra_headers:
        headers[key] = value

    def chunks() -> Iterator[bytes]:
        with fr.path.open("rb") as handle:
            handle.seek(fr.offset)
            remaining = fr.length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        chunks(), status_code=fr.status, media_type=fr.ctype, headers=headers
    )


def _static_response(path: str) -> Response:
    rel = path.lstrip("/")
    if rel:
        candidate = (STATIC_DIR / rel).resolve()
        if candidate.is_relative_to(_STATIC_ROOT) and candidate.is_file():
            return _file_response(candidate)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return _file_response(index)
    return _body_response(200, "text/html; charset=utf-8", _PLACEHOLDER.encode())


def _adapt(
    fn: Endpoint, connections: Connections, config: Config
) -> Callable[[Request], Response]:
    def endpoint(request: Request) -> Response:
        params: dict[str, Any] = {}
        for key, value in request.query_params.multi_items():
            if key not in params:
                params[key] = value
        params.update(request.path_params)
        params["_headers"] = {
            key.lower(): str(value) for key, value in request.headers.items()
        }
        try:
            result = fn(connections.get(), config, params)
        except ApiError:
            raise
        except Exception as exc:
            return _json_response(500, {"error": str(exc)})
        if isinstance(result, FileRange):
            return _file_range_response(result)
        if isinstance(result, tuple):
            status, ctype, body = result
            return _body_response(int(status), ctype, body)
        return _json_response(200, result)

    return endpoint


def create_app(
    config: Config,
    routes: Routes | None = None,
    writes: APIRouter | None = None,
) -> FastAPI:
    if routes is None:
        from video_security.web.api import api_routes
        from video_security.web.media import media_routes

        routes = api_routes() + media_routes()
    if writes is None:
        from video_security.web.writes import write_router

        writes = write_router(config.storage.db_path)
    connections = Connections(config.storage.db_path)
    app = FastAPI(redirect_slashes=False, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> Response:
        return _json_response(exc.status, {"error": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
        return _json_response(422, {"error": "invalid request"})

    for _method, template, fn in routes:
        app.add_api_route(
            template,
            _adapt(fn, connections, config),
            methods=["GET", "HEAD"],
            include_in_schema=False,
            response_model=None,
        )

    app.include_router(writes)

    def static_endpoint(request: Request) -> Response:
        path = str(request.scope["path"])
        if path.startswith("/api/") or path.startswith("/media/"):
            return _json_response(404, {"error": "not found"})
        return _static_response(path)

    app.add_api_route(
        "/{rest:path}",
        static_endpoint,
        methods=["GET", "HEAD"],
        include_in_schema=False,
        response_model=None,
    )
    return app


def serve(
    config: Config,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = False,
) -> None:
    rw = sqlite3.connect(str(Path(config.storage.db_path).expanduser()))
    try:
        init_db(rw)
        from video_security.geo import ensure_table

        ensure_table(rw)
    finally:
        rw.close()
    bind_host = host or config.web.host
    bind_port = port if port is not None else config.web.port
    app = create_app(config)
    url = f"http://{bind_host}:{bind_port}"
    print(f"{url} — local only, no auth")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=bind_host, port=bind_port, log_config=None, access_log=False)
