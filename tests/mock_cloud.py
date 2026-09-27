from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockCloud:
    def __init__(
        self,
        response_text: str = (
            '{"relevant": true, "event_type": "intrusion", '
            '"description": "ok", "confidence": "high"}'
        ),
        responses: list[str] | None = None,
        usage: dict[str, int] | None = None,
        auth_token: str = "test-key",
        mode: str = "ok",
    ) -> None:
        self.responses = (
            responses if responses is not None else [response_text]
        )
        self.usage = (
            usage
            if usage is not None
            else {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            }
        )
        self.auth_token = auth_token
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self.call_count = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> MockCloud:
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
                content_len = int(self.headers.get("Content-Length", "0") or "0")
                body_raw = self.rfile.read(content_len) if content_len else b""
                try:
                    body: dict[str, Any] | None = (
                        json.loads(body_raw) if body_raw else None
                    )
                except (json.JSONDecodeError, TypeError):
                    body = None
                record: dict[str, Any] = {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": body,
                    "timestamp": time.time(),
                }
                mock.requests.append(record)

                if parsed.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                if mock.mode == "fail500":
                    self.send_error(500)
                    return
                if (
                    self.headers.get("Authorization", "")
                    != f"Bearer {mock.auth_token}"
                ):
                    self._send_json(
                        401,
                        {
                            "error": {
                                "message": "invalid api key",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                    return
                text = mock.responses[mock.call_count % len(mock.responses)]
                mock.call_count += 1
                self._send_json(
                    200,
                    {
                        "id": "chatcmpl-mock-cloud",
                        "object": "chat.completion",
                        "model": "mock-cloud-vlm",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": text,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": mock.usage,
                    },
                )

            def do_GET(self) -> None:
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

    def __enter__(self) -> MockCloud:
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("server not started")
        return f"http://127.0.0.1:{self._server.server_address[1]}"
