from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from video_security.llm.ollama import OllamaError


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode(errors="replace")
    except Exception:
        return ""


def _is_memory_gate(e: urllib.error.HTTPError) -> bool:
    return "GPU memory" in _error_detail(e)


class MlxServeClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11234",
        timeout_s: int = 120,
        max_attempts: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self._last_model: str | None = None

    def _evict_other(self, model: str) -> None:
        if self._last_model is not None and self._last_model != model:
            previous = self._last_model
            self._last_model = None
            self.unload(previous)

    def _models(self) -> list[dict[str, Any]]:
        url = self.base_url + "/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                data: Any = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise OllamaError(
                f"HTTP {e.code} from {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise OllamaError(
                f"mlx-serve not reachable at {self.base_url} — is it running?"
            ) from e
        entries = data.get("data") if isinstance(data, dict) else None
        return [dict(e) for e in entries] if isinstance(entries, list) else []

    def generate(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
        num_ctx: int = 0,
        keep_alive: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        self._evict_other(model)
        content: Any = prompt
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for image_b64 in images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    }
                )
            content = parts
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if format_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": format_schema,
                    "strict": False,
                },
            }
        url = self.base_url + "/v1/chat/completions"

        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    resp_body: str = resp.read().decode()
                try:
                    result: Any = json.loads(resp_body)
                except json.JSONDecodeError:
                    self._last_model = model
                    return resp_body
                choices = result.get("choices") if isinstance(result, dict) else None
                if not isinstance(choices, list) or not choices:
                    raise OllamaError(
                        f"Missing 'choices' in response from {url}"
                    )
                self._last_model = model
                return str(choices[0]["message"]["content"])
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code >= 500 or _is_memory_gate(e):
                    time.sleep(2**attempt)
                    continue
                detail = _error_detail(e)
                raise OllamaError(
                    f"HTTP {e.code} from {url}: {e.reason} {detail[:200]}"
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                time.sleep(2**attempt)
                continue
        raise OllamaError(
            f"mlx-serve not reachable at {self.base_url} after "
            f"{self.max_attempts} attempts"
        ) from last_error

    def generate_json(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
        num_ctx: int = 0,
        keep_alive: str | None = None,
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
        for entry in self._models():
            if str(entry.get("id", "")) == model:
                return str(entry["id"])
        raise OllamaError(
            f"Model {model!r} not found in mlx-serve registry — models are "
            "discovered at startup; restart mlx-serve after pulling models"
        )

    def unload(self, model: str) -> None:
        body = json.dumps({"model": model}).encode()
        url = self.base_url + "/v1/unload-model"
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=self.timeout_s).read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return

    def embed(self, model: str, text: str) -> list[float]:
        body = json.dumps({"model": model, "input": text}).encode()
        url = self.base_url + "/v1/embeddings"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                result: Any = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise OllamaError(
                f"HTTP {e.code} from {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise OllamaError(
                f"mlx-serve not reachable at {self.base_url}"
            ) from e
        data = result.get("data") if isinstance(result, dict) else None
        if (
            not isinstance(data, list)
            or not data
            or "embedding" not in data[0]
        ):
            raise OllamaError(
                f"Missing 'embedding' in response from {url}"
            )
        return [float(v) for v in data[0]["embedding"]]

    def health_check(self, models: list[str]) -> None:
        available = {str(entry.get("id", "")) for entry in self._models()}
        missing = [m for m in models if m not in available]
        if missing:
            missing_list = ", ".join(missing)
            raise OllamaError(
                f"mlx-serve missing models: {missing_list} — models are "
                "discovered at startup; restart mlx-serve after pulling models"
            )
