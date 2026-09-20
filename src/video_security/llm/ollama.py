from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import cv2
import numpy as np


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout_s: int = 120,
        max_attempts: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts

    def generate(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
        num_ctx: int = 2048,
        keep_alive: str = "30m",
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0, "num_ctx": num_ctx},
        }
        if images:
            body["images"] = images
        if format_schema:
            body["format"] = format_schema
        data = json.dumps(body).encode()
        url = self.base_url + "/api/generate"

        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    resp_body = resp.read().decode()
                try:
                    result: Any = json.loads(resp_body)
                except json.JSONDecodeError:
                    return resp_body  # type: ignore[no-any-return]
                if "response" not in result:
                    raise OllamaError(
                        f"Missing 'response' key in response from {url}"
                    )
                return str(result["response"])
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code >= 500:
                    time.sleep(2**attempt)
                    continue
                raise OllamaError(
                    f"HTTP {e.code} from {url}: {e.reason}"
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                time.sleep(2**attempt)
                continue
        raise OllamaError(
            f"Ollama not reachable at {self.base_url} after {self.max_attempts} attempts"
        ) from last_error

    def generate_json(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
        num_ctx: int = 2048,
        keep_alive: str = "30m",
    ) -> dict[str, Any]:
        try:
            result_text = self.generate(
                model=model,
                prompt=prompt,
                images=images,
                format_schema=format_schema,
                num_ctx=num_ctx,
                keep_alive=keep_alive,
            )
            return json.loads(result_text)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass
        repair_prompt = prompt + "\nRespond with valid JSON only, no prose."
        repaired = self.generate(
            model=model,
            prompt=repair_prompt,
            images=images,
            format_schema=format_schema,
            num_ctx=num_ctx,
            keep_alive=keep_alive,
        )
        try:
            return json.loads(repaired)  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            raise OllamaError(
                f"malformed JSON after repair retry: {repaired[:200]}"
            ) from e

    def model_digest(self, model: str) -> str:
        url = self.base_url + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                data: Any = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise OllamaError(
                f"HTTP {e.code} from {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise OllamaError(
                f"Ollama not reachable at {self.base_url}"
            ) from e
        for entry in data.get("models", []):
            name = str(entry.get("name", ""))
            if name == model or name.split(":")[0] == model:
                digest = entry.get("digest")
                if digest:
                    return str(digest)
        raise OllamaError(
            f"Model {model!r} not found in Ollama tags — run: ollama pull {model}"
        )

    def health_check(self, models: list[str]) -> None:
        url = self.base_url + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                data: Any = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise OllamaError(
                f"HTTP {e.code} from {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise OllamaError(
                f"Ollama not reachable at {self.base_url} — is it running?"
            ) from e
        available = {m["name"] for m in data.get("models", [])}
        missing = [m for m in models if m not in available]
        if missing:
            missing_list = ", ".join(missing)
            cmds = "; ".join(f"ollama pull {m}" for m in missing)
            raise OllamaError(
                f"Ollama missing models: {missing_list}. Run: {cmds}"
            )


def encode_image_jpeg(
    image: np.ndarray, max_width: int = 1280, quality: int = 80
) -> str:
    h, w = image.shape[:2]
    if w > max_width:
        scale = max_width / w
        new_h = int(h * scale)
        image = cv2.resize(image, (max_width, new_h))
    _, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode()