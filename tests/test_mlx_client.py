from __future__ import annotations

import pytest

from tests.mock_mlx_serve import MockMlxServe
from video_security.config import Config
from video_security.llm import make_llm_client
from video_security.llm.mlx_serve import MlxServeClient
from video_security.llm.ollama import OllamaClient, OllamaError

E4B = "mlx-community/gemma-4-e4b-it-4bit"
M12B = "mlx-community/gemma-4-12b-it-4bit"


def test_generate_ok() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        r = c.generate(E4B, "hi")
        assert r == m.response_text
        last_body = m.requests[-1]["body"]
        assert last_body["model"] == E4B
        assert last_body["messages"][0]["role"] == "user"
        assert last_body["messages"][0]["content"] == "hi"
        assert last_body["temperature"] == 0


def test_generate_images_content_parts() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        c.generate(E4B, "hi", images=["aGk="])
        content = m.requests[-1]["body"]["messages"][0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "hi"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,aGk="


def test_generate_schema_response_format() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        schema = {"type": "object", "properties": {"relevant": {"type": "boolean"}}}
        c.generate(E4B, "hi", format_schema=schema)
        rf = m.requests[-1]["body"]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["schema"] == schema
        assert rf["json_schema"]["strict"] is False


def test_generate_fail500_retries() -> None:
    with MockMlxServe(mode="fail500") as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5, max_attempts=3)
        with pytest.raises(OllamaError):
            c.generate(E4B, "hi")
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 3


def test_generate_kvgate_retries_then_ok() -> None:
    with MockMlxServe(mode="kvgate", kv_fail_count=1) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        r = c.generate(E4B, "hi")
        assert r == m.response_text
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 2


def test_generate_kvgate_always_fails() -> None:
    with MockMlxServe(mode="kvgate", kv_fail_count=99) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5, max_attempts=3)
        with pytest.raises(OllamaError, match="attempts"):
            c.generate(E4B, "hi")
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 3


def test_generate_hard_400_no_retry() -> None:
    with MockMlxServe(mode="badrequest") as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5, max_attempts=3)
        with pytest.raises(OllamaError):
            c.generate(E4B, "hi")
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 1


def test_generate_unreachable() -> None:
    c = MlxServeClient(
        base_url="http://127.0.0.1:65530", timeout_s=1, max_attempts=2
    )
    with pytest.raises(OllamaError):
        c.generate(E4B, "hi")


def test_generate_junk_body_returns_raw_text() -> None:
    with MockMlxServe(mode="junkonce", junk_count=99) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        r = c.generate(E4B, "hi")
        assert r == "this is { not json"


def test_unload_posts_unload_model() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        c.unload(E4B)
        last = m.requests[-1]
        assert last["path"] == "/v1/unload-model"
        assert last["body"] == {"model": E4B}


def test_unload_server_down_is_silent() -> None:
    c = MlxServeClient(base_url="http://127.0.0.1:65530", timeout_s=1)
    c.unload(E4B)


def test_model_swap_evicts_previous_model() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        c.generate(E4B, "hi")
        c.generate(M12B, "hi")
        chats = [
            r["body"] for r in m.requests if r["path"] == "/v1/chat/completions"
        ]
        assert [b["model"] for b in chats] == [E4B, M12B]
        unloads = [
            r["body"] for r in m.requests if r["path"] == "/v1/unload-model"
        ]
        assert unloads == [{"model": E4B}]


def test_generate_json_ok() -> None:
    with MockMlxServe(response_text='{"relevant": true}') as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        result = c.generate_json(E4B, "hi")
        assert result == {"relevant": True}


def test_generate_json_repair_retry() -> None:
    with MockMlxServe(mode="junkonce", junk_count=1) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        result = c.generate_json(E4B, "hi")
        assert isinstance(result, dict)
        chats = [r for r in m.requests if r["path"] == "/v1/chat/completions"]
        assert len(chats) == 2
        assert chats[1]["body"]["messages"][0]["content"].endswith(
            "\nRespond with valid JSON only, no prose."
        )


def test_generate_json_still_bad() -> None:
    with MockMlxServe(mode="junkonce", junk_count=99) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=5)
        with pytest.raises(OllamaError, match="malformed JSON"):
            c.generate_json(E4B, "hi")


def test_model_digest() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url)
        assert c.model_digest(E4B) == E4B
        with pytest.raises(OllamaError, match="mlx-serve registry"):
            c.model_digest("mlx-community/nope")


def test_health_check_ok() -> None:
    with MockMlxServe(models=[E4B, M12B]) as m:
        c = MlxServeClient(base_url=m.base_url)
        c.health_check([E4B, M12B])
        with pytest.raises(OllamaError, match="mlx-serve missing models"):
            c.health_check([E4B, "mlx-community/nope"])


def test_embed_returns_floats() -> None:
    with MockMlxServe() as m:
        c = MlxServeClient(base_url=m.base_url)
        emb = c.embed("mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ", "hi")
        assert emb == [0.25, -0.5, 0.75]


def test_slow_timeout() -> None:
    with MockMlxServe(mode="slow", delay_s=2.0) as m:
        c = MlxServeClient(base_url=m.base_url, timeout_s=1, max_attempts=2)
        with pytest.raises(OllamaError):
            c.generate(E4B, "hi")


def test_factory_defaults_to_ollama() -> None:
    cfg = Config()
    c = make_llm_client(cfg)
    assert isinstance(c, OllamaClient)
    assert c.base_url == "http://localhost:11434"
    assert c.timeout_s == cfg.llm_triage.timeout_s


def test_factory_mlx_serve() -> None:
    cfg = Config()
    cfg.llm.provider = "mlx-serve"
    cfg.llm.mlx_url = "http://127.0.0.1:19999"
    c = make_llm_client(cfg, timeout_s=17)
    assert isinstance(c, MlxServeClient)
    assert c.base_url == "http://127.0.0.1:19999"
    assert c.timeout_s == 17
