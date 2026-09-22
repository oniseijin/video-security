from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from tests.mock_ollama import MockOllama


def test_ok() -> None:
    with MockOllama() as m:
        body = json.dumps({"model": "gemma3:4b", "prompt": "x", "images": ["aGk="]}).encode()
        req = urllib.request.Request(m.base_url + "/api/generate", data=body)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            parsed = json.loads(resp.read())
        assert parsed["response"] == m.response_text
        assert m.requests[-1]["body"]["model"] == "gemma3:4b"


def test_fail500() -> None:
    with MockOllama(mode="fail500") as m:
        body = json.dumps({"model": "gemma3:4b", "prompt": "x"}).encode()
        req = urllib.request.Request(m.base_url + "/api/generate", data=body)
        req.add_header("Content-Type", "application/json")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 500


def test_junkonce() -> None:
    with MockOllama(mode="junkonce") as m:
        body = json.dumps({"model": "gemma3:4b", "prompt": "x"}).encode()
        req = urllib.request.Request(m.base_url + "/api/generate", data=body)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            with pytest.raises(json.JSONDecodeError):
                json.loads(resp.read())
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            json.loads(resp.read())
        assert m.junk_served == 1


def test_slow() -> None:
    with MockOllama(mode="slow", delay_s=0.5) as m:
        body = json.dumps({"model": "gemma3:4b", "prompt": "x"}).encode()
        req = urllib.request.Request(m.base_url + "/api/generate", data=body)
        req.add_header("Content-Type", "application/json")
        t0 = time.monotonic()
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            json.loads(resp.read())
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.45


def test_tags_show() -> None:
    with MockOllama() as m:
        with urllib.request.urlopen(m.base_url + "/api/tags") as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        names = {md["name"] for md in data["models"]}
        assert {"gemma3:4b", "gemma4:12b-mlx"} <= names
        digests = set[str]()
        for _ in range(2):
            with urllib.request.urlopen(m.base_url + "/api/show?model=gemma3:4b") as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                digests.add(data["digest"])
        assert len(digests) == 1


def test_ps() -> None:
    with MockOllama() as m:
        with urllib.request.urlopen(m.base_url + "/api/ps") as resp:
            assert resp.status == 200
            assert json.loads(resp.read()) == {"models": []}


def test_404() -> None:
    with MockOllama() as m:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(m.base_url + "/api/nope")
        assert e.value.code == 404


def test_ctx_exit() -> None:
    m = MockOllama()
    m.start()
    url = m.base_url + "/api/tags"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
    m.stop()
    with pytest.raises((urllib.error.URLError, urllib.error.HTTPError)):
        urllib.request.urlopen(url, timeout=1)