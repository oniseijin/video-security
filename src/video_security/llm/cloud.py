from __future__ import annotations

import dataclasses
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


class CloudError(Exception):
    pass


class CloudDisabledError(CloudError):
    pass


class BudgetExceededError(CloudError):
    pass


@dataclasses.dataclass
class CloudResult:
    data: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    model: str


class CloudClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: int = 120,
        max_attempts: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts

    def _error_detail(self, e: urllib.error.HTTPError) -> str:
        try:
            return e.read().decode(errors="replace")
        except Exception:
            return ""

    def generate(
        self,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> CloudResult:
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
            "model": self.model,
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
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    resp_body = resp.read().decode()
                result: Any = json.loads(resp_body)
                choices = (
                    result.get("choices") if isinstance(result, dict) else None
                )
                if not isinstance(choices, list) or not choices:
                    raise CloudError(f"Missing 'choices' in response from {url}")
                message = choices[0].get("message", {})
                text = str(message.get("content", ""))
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    raise CloudError(
                        f"malformed JSON from cloud endpoint: {text[:200]}"
                    ) from e
                usage = result.get("usage", {}) if isinstance(result, dict) else {}
                usage = usage if isinstance(usage, dict) else {}
                return CloudResult(
                    data=data,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    model=str(result.get("model", self.model)),
                )
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code >= 500:
                    time.sleep(2**attempt)
                    continue
                detail = self._error_detail(e)
                raise CloudError(
                    f"HTTP {e.code} from {url}: {e.reason} {detail[:200]}"
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                time.sleep(2**attempt)
                continue
        raise CloudError(
            f"cloud endpoint not reachable at {self.base_url} after "
            f"{self.max_attempts} attempts"
        ) from last_error

    def generate_json(
        self,
        prompt: str,
        images: list[str] | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> CloudResult:
        return self.generate(prompt, images, format_schema)


def make_cloud_client(config: Any) -> CloudClient:
    cloud = getattr(config, "cloud", None)
    if cloud is None or not cloud.enabled:
        raise CloudDisabledError(
            "cloud backend is disabled — set [cloud] enabled = true in config"
        )
    if not cloud.base_url:
        raise CloudDisabledError(
            "[cloud] base_url is not set — point it at an OpenAI-compatible endpoint"
        )
    if not cloud.model:
        raise CloudDisabledError("[cloud] model is not set")
    key = os.environ.get(cloud.api_key_env, "")
    if not key:
        raise CloudDisabledError(
            f"environment variable {cloud.api_key_env} is not set — the API key "
            "lives in the environment only, never in a toml file"
        )
    detail_cfg = getattr(config, "llm_detail", None)
    timeout_s = detail_cfg.timeout_s if detail_cfg and detail_cfg.timeout_s else 120
    return CloudClient(cloud.base_url, cloud.model, key, timeout_s=timeout_s)
