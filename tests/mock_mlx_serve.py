from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockMlxServe:
    def __init__(
        self,
        mode: str = "ok",
        response_text: str = (
            '{"relevant": true, "event_type": "none", "description": "ok", "confidence": "low"}'
        ),
        delay_s: float = 2.0,
        junk_count: int = 1,
        kv_fail_count: int = 1,
        gate_model: str | None = None,
        models: list[str] | None = None,
    ) -> None:
        if models is None:
            models = [
                "mlx-community/gemma-4-e4b-it-4bit",
                "mlx-community/gemma-4-12b-it-4bit",
            ]
        self.mode = mode
        self.response_text = response_text
        self.delay_s = delay_s
        self.junk_count = junk_count
        self.kv_fail_count = kv_fail_count
        self.gate_model = gate_model
        self.models = models
        self.requests: list[dict[str, Any]] = []
        self.junk_served: int = 0
        self.kv_served: int = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> MockMlxServe:
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

            def _chat_response(self) -> None:
                self._send_json(
                    200,
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion",
                        "model": "mock",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": mock.response_text,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 18,
                            "completion_tokens": 5,
                            "total_tokens": 23,
                        },
                    },
                )

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

                if parsed.path == "/v1/chat/completions":
                    if (
                        mock.gate_model is not None
                        and body is not None
                        and body.get("model") == mock.gate_model
                    ):
                        self._send_json(
                            400,
                            {
                                "error": {
                                    "message": (
                                        "Prompt (18 tokens) requires ~660MB "
                                        "GPU memory but only ~46MB available. "
                                        "Reduce prompt size or use a smaller model."
                                    ),
                                    "type": "invalid_request_error",
                                }
                            },
                        )
                    elif mock.mode == "fail500":
                        self.send_error(500)
                    elif mock.mode == "slow":
                        time.sleep(mock.delay_s)
                        self._chat_response()
                    elif mock.mode == "kvgate":
                        if mock.kv_served < mock.kv_fail_count:
                            mock.kv_served += 1
                            self._send_json(
                                400,
                                {
                                    "error": {
                                        "message": (
                                            "Prompt (18 tokens) requires ~660MB "
                                            "GPU memory but only ~46MB available. "
                                            "Reduce prompt size or use a smaller model."
                                        ),
                                        "type": "invalid_request_error",
                                    }
                                },
                            )
                        else:
                            self._chat_response()
                    elif mock.mode == "badrequest":
                        self._send_json(
                            400,
                            {
                                "error": {
                                    "message": "unknown model",
                                    "type": "invalid_request_error",
                                }
                            },
                        )
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
                            self._chat_response()
                    else:
                        self._chat_response()
                elif parsed.path == "/v1/unload-model":
                    model = body.get("model", "") if body else ""
                    self._send_json(
                        200,
                        {
                            "model": {
                                "id": model,
                                "object": "model",
                                "loaded": False,
                                "state": "unloaded",
                            }
                        },
                    )
                elif parsed.path == "/v1/embeddings":
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "model": body.get("model", "") if body else "",
                            "data": [
                                {
                                    "object": "embedding",
                                    "index": 0,
                                    "embedding": [0.25, -0.5, 0.75],
                                }
                            ],
                        },
                    )
                else:
                    self.send_error(404)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                record: dict[str, Any] = {
                    "path": self.path,
                    "query": {},
                    "body": None,
                    "timestamp": time.time(),
                }
                mock.requests.append(record)

                if parsed.path == "/v1/models":
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {"id": m, "object": "model"} for m in mock.models
                            ],
                        },
                    )
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

    def __enter__(self) -> MockMlxServe:
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("server not started")
        return f"http://127.0.0.1:{self._server.server_address[1]}"
