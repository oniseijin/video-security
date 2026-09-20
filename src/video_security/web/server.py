from __future__ import annotations

import dataclasses
import json
import mimetypes
import re
import sqlite3
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit

from video_security.config import Config
from video_security.db import init_db

Endpoint = Callable[[sqlite3.Connection, Config, dict[str, Any]], Any]
Route = tuple[str, str, Endpoint]
Routes = list[Route]

STATIC_DIR = Path(__file__).resolve().parent / "static"


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

_PARAM_RE = re.compile(r"\{(\w+)(?::(int|str))?\}")


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


def _compile_route(template: str) -> tuple[re.Pattern[str], dict[str, str]]:
    kinds: dict[str, str] = {}
    chunks = ["^"]
    pos = 0
    for match in _PARAM_RE.finditer(template):
        chunks.append(re.escape(template[pos : match.start()]))
        name = match.group(1)
        kind = match.group(2) or "str"
        kinds[name] = kind
        sub = r"\d+" if kind == "int" else r"[^/]+"
        chunks.append("(?P<" + name + ">" + sub + ")")
        pos = match.end()
    chunks.append(re.escape(template[pos:]))
    chunks.append("$")
    return re.compile("".join(chunks)), kinds


def _handler_class(
    routes: Routes, connections: Connections, config: Config
) -> type[BaseHTTPRequestHandler]:
    compiled: list[tuple[str, re.Pattern[str], dict[str, str], Endpoint]] = []
    for method, template, fn in routes:
        pattern, kinds = _compile_route(template)
        compiled.append((method, pattern, kinds, fn))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_HEAD(self) -> None:
            self._suppress_body = True
            self._dispatch("GET")

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _dispatch(self, method: str) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if self._api(method, parsed, path):
                return
            if path.startswith("/api/") or path.startswith("/media/"):
                self._json(404, {"error": "not found"})
                return
            self._static(path)

        def _api(self, method: str, parsed: SplitResult, path: str) -> bool:
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            for route_method, pattern, kinds, fn in compiled:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if match is None:
                    continue
                params: dict[str, Any] = dict(query)
                for name, raw in match.groupdict().items():
                    if raw is None:
                        continue
                    params[name] = int(raw) if kinds.get(name) == "int" else raw
                params["_headers"] = {
                    key.lower(): str(value) for key, value in self.headers.items()
                }
                try:
                    result = fn(connections.get(), config, params)
                except ApiError as exc:
                    self._json(exc.status, {"error": str(exc)})
                    return True
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return True
                if isinstance(result, FileRange):
                    self._stream(result)
                elif isinstance(result, tuple):
                    status, ctype, body = result
                    self._send(status, ctype, body, "no-cache")
                else:
                    self._json(200, result)
                return True
            return False

        def _static(self, path: str) -> None:
            rel = path.lstrip("/")
            if rel:
                candidate = (STATIC_DIR / rel).resolve()
                if candidate.is_relative_to(STATIC_DIR.resolve()) and candidate.is_file():
                    self._file(candidate)
                    return
            index = STATIC_DIR / "index.html"
            if index.is_file():
                self._file(index)
            else:
                self._send(200, "text/html; charset=utf-8", _PLACEHOLDER.encode(), "no-cache")

        def _file(self, path: Path) -> None:
            cache = "no-cache"
            if "assets" in path.parts:
                cache = "public, max-age=31536000, immutable"
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(200, ctype, path.read_bytes(), cache)

        def _stream(self, fr: FileRange) -> None:
            self.send_response(fr.status)
            self.send_header("Content-Type", fr.ctype)
            self.send_header("Content-Length", str(fr.length))
            for key, value in fr.extra_headers:
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if getattr(self, "_suppress_body", False):
                return
            with fr.path.open("rb") as handle:
                handle.seek(fr.offset)
                remaining = fr.length
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self._send(status, "application/json; charset=utf-8", body, "no-cache")

        def _send(self, status: int, ctype: str, body: bytes, cache: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if not getattr(self, "_suppress_body", False):
                self.wfile.write(body)

    return Handler


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        config: Config,
        routes: Routes | None = None,
    ) -> None:
        if routes is None:
            from video_security.web.api import api_routes
            from video_security.web.media import media_routes

            routes = api_routes() + media_routes()
        self.config = config
        self.connections = Connections(config.storage.db_path)
        super().__init__(address, _handler_class(routes, self.connections, config))


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
    server = WebServer((bind_host, bind_port), config)
    url = f"http://{bind_host}:{int(server.server_address[1])}"
    print(f"{url} — local only, no auth")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
