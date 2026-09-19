from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockOllama:
    def __init__(
        self,
        mode: str = "ok",
        response_text: str = (
            '{"relevant": true, "event_type": "none", "description": "ok", "confidence": "low"}'
        ),
        delay_s: float = 2.0,
        junk_count: int = 1,
        models: list[str] | None = None,
    ) -> None:
        if models is None:
            models = ["gemma3:4b", "gemma4:12b"]
        self.mode = mode
        self.response_text = response_text
        self.delay_s = delay_s
        self.junk_count = junk_count
        self.models = models
        self.requests: list[dict[str, Any]] = []
        self.junk_served: int = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> MockOllama:
        mock = self

        class _Handler(BaseHTTPRequestHandler):
            def _send_json(self, code: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                content_len = int(self.headers.get("Content-Length", "0") or "0")
                body_raw = self.rfile.read(content_len) if content_len else b""
                try:
                    body: dict[str, Any] | None = json.loads(body_raw) if body_raw else None
                except (json.JSONDecodeError, TypeError):
                    body = None
                    self.send_error(400)
                    return
                record: dict[str, Any] = {
                    "path": self.path,
                    "query": query,
                    "body": body,
                    "timestamp": time.time(),
                }
                mock.requests.append(record)

                if parsed.path == "/api/generate":
                    if mock.mode == "fail500":
                        self.send_error(500)
                    elif mock.mode == "slow":
                        time.sleep(mock.delay_s)
                        self._send_json(
                            200,
                            {
                                "model": body.get("model", "") if body else "",
                                "created_at": datetime.now(UTC).isoformat(),
                                "response": mock.response_text,
                                "done": True,
                            },
                        )
                    elif mock.mode == "trickle":
                        payload = json.dumps(
                            {
                                "model": body.get("model", "") if body else "",
                                "created_at": datetime.now(UTC).isoformat(),
                                "response": mock.response_text,
                                "done": True,
                            }
                        ).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        for i in range(0, len(payload), 16):
                            self.wfile.write(payload[i : i + 16])
                            self.wfile.flush()
                            time.sleep(0.5)
                    elif mock.mode == "junkonce":
                        if mock.junk_served < mock.junk_count:
                            mock.junk_served += 1
                            payload_junk = b"this is { not json"
                            self.send_response(200)
                            self.send_header("Content-Type", "text/plain")
                            self.send_header("Content-Length", str(len(payload_junk)))
                            self.end_headers()
                            self.wfile.write(payload_junk)
                        else:
                            self._send_json(
                                200,
                                {
                                    "model": body.get("model", "") if body else "",
                                    "created_at": datetime.now(UTC).isoformat(),
                                    "response": mock.response_text,
                                    "done": True,
                                },
                            )
                    else:
                        self._send_json(
                            200,
                            {
                                "model": body.get("model", "") if body else "",
                                "created_at": datetime.now(UTC).isoformat(),
                                "response": mock.response_text,
                                "done": True,
                            },
                        )
                elif parsed.path == "/api/show":
                    model = body.get("model", "") if body else ""
                    self._send_json(
                        200,
                        {
                            "modelfile": "FAKE",
                            "digest": "sha256:"
                            + hashlib.sha256(str(model).encode()).hexdigest(),
                            "details": {"family": "gemma", "parameter_size": "4B"},
                        },
                    )
                else:
                    self.send_error(404)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                record: dict[str, Any] = {
                    "path": self.path,
                    "query": query,
                    "body": None,
                    "timestamp": time.time(),
                }
                mock.requests.append(record)

                if parsed.path == "/api/tags":
                    self._send_json(
                        200,
                        {
                            "models": [
                                {
                                    "name": m,
                                    "digest": "sha256:" + hashlib.sha256(m.encode()).hexdigest(),
                                }
                                for m in mock.models
                            ]
                        },
                    )
                elif parsed.path == "/api/show":
                    model = query.get("model", [""])[0]
                    self._send_json(
                        200,
                        {
                            "modelfile": "FAKE",
                            "digest": "sha256:" + hashlib.sha256(model.encode()).hexdigest(),
                            "details": {"family": "gemma", "parameter_size": "4B"},
                        },
                    )
                elif parsed.path == "/api/ps":
                    self._send_json(200, {"models": []})
                else:
                    self.send_error(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        for _ in range(100):
            try:
                addr = ("127.0.0.1", self._server.server_address[1])
                with socket.create_connection(addr, timeout=0.01):
                    break
            except OSError:
                time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(5)

    def __enter__(self) -> MockOllama:
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("server not started")
        return f"http://127.0.0.1:{self._server.server_address[1]}"