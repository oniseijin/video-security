from __future__ import annotations

import base64

import numpy as np
import pytest

from tests.mock_ollama import MockOllama
from video_security.llm.ollama import OllamaClient, OllamaError, encode_image_jpeg


def test_generate_ok() -> None:
    with MockOllama() as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=5)
        r = c.generate("gemma3:4b", "hi", images=["aGk="], num_ctx=2048)
        assert r == m.response_text
        last_body = m.requests[-1]["body"]
        assert last_body["options"]["num_ctx"] == 2048
        assert last_body["keep_alive"] == 0
        assert last_body["stream"] is False


def test_generate_fail500_retries() -> None:
    with MockOllama(mode="fail500") as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=5, max_attempts=3)
        with pytest.raises(OllamaError):
            c.generate("gemma3:4b", "hi")
        generate_reqs = [r for r in m.requests if r["path"] == "/api/generate"]
        assert len(generate_reqs) == 3


def test_generate_4xx_no_retry() -> None:
    c = OllamaClient(base_url="http://127.0.0.1:65530", timeout_s=1, max_attempts=2)
    with pytest.raises(OllamaError):
        c.generate("gemma3:4b", "hi")


def test_generate_json_ok() -> None:
    with MockOllama(response_text='{"relevant": true}') as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=5)
        result = c.generate_json("gemma3:4b", "hi")
        assert result == {"relevant": True}


def test_generate_json_repair_retry() -> None:
    with MockOllama(mode="junkonce", junk_count=1) as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=5)
        result = c.generate_json("gemma3:4b", "hi")
        assert isinstance(result, dict)
        generate_reqs = [
            r for r in m.requests if r["path"] == "/api/generate"
        ]
        assert len(generate_reqs) == 2
        assert generate_reqs[1]["body"]["prompt"].endswith(
            "\nRespond with valid JSON only, no prose."
        )


def test_generate_json_still_bad() -> None:
    with MockOllama(mode="junkonce", junk_count=99) as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=5)
        with pytest.raises(OllamaError, match="malformed JSON"):
            c.generate_json("gemma3:4b", "hi")


def test_model_digest() -> None:
    with MockOllama() as m:
        c = OllamaClient(base_url=m.base_url)
        d = c.model_digest("gemma3:4b")
        assert d.startswith("sha256:")
        d2 = c.model_digest("gemma3:4b")
        assert d == d2


def test_health_check_ok() -> None:
    with MockOllama(models=["a", "b"]) as m:
        c = OllamaClient(base_url=m.base_url)
        c.health_check(["a", "b"])
        with pytest.raises(OllamaError, match="c"):
            c.health_check(["a", "c"])


def test_encode_image_jpeg() -> None:
    img = np.zeros((100, 400, 3), np.uint8)
    result = encode_image_jpeg(img)
    decoded = base64.b64decode(result)
    assert decoded[:2] == b"\xff\xd8"


def test_sky_slow_timeout() -> None:
    with MockOllama(mode="slow", delay_s=2.0) as m:
        c = OllamaClient(base_url=m.base_url, timeout_s=1, max_attempts=2)
        with pytest.raises(OllamaError):
            c.generate("gemma3:4b", "hi")