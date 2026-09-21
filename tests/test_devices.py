from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from video_security.devices import (
    KIND_DASHCAM,
    KIND_IPHONE,
    KIND_META_GLASSES,
    KIND_UNKNOWN,
    from_adapter,
    probe,
)


def _runner(output: str) -> Callable[[list[str]], str]:
    return lambda _args: output


def test_iphone_detection() -> None:
    runner = _runner('[{"Make": "Apple", "Model": "iPhone 15 Pro"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_IPHONE
    assert result.make == "Apple"
    assert result.model == "iPhone 15 Pro"


def test_meta_glasses_detection() -> None:
    runner = _runner('[{"Make": "Ray-Ban", "Model": "Ray-Ban Meta Smart Glasses"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_META_GLASSES
    assert result.make == "Ray-Ban"
    assert result.model == "Ray-Ban Meta Smart Glasses"


def test_unknown_detection() -> None:
    runner = _runner('[{"Make": "Canon", "Model": "EOS R5"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_UNKNOWN
    assert result.make == "Canon"
    assert result.model == "EOS R5"


def test_empty_dict_unknown() -> None:
    runner = _runner('[{}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_UNKNOWN
    assert result.make is None
    assert result.model is None


def test_malformed_json_unknown() -> None:
    runner = _runner("not json")
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_UNKNOWN
    assert result.make is None
    assert result.model is None


def test_runner_raises_oserror_unknown() -> None:
    def failing_runner(_args: list[str]) -> str:
        raise OSError("exiftool not found")
    result = probe(Path("/fake/test.mov"), runner=failing_runner)
    assert result.kind == KIND_UNKNOWN
    assert result.make is None
    assert result.model is None


def test_from_adapter_dashcam() -> None:
    result = from_adapter(KIND_DASHCAM, "Mazda", "CX-8")
    assert result.kind == KIND_DASHCAM
    assert result.make == "Mazda"
    assert result.model == "CX-8"


def test_whitespace_stripping() -> None:
    runner = _runner('[{"Make": "  Apple  ", "Model": "  iPhone 15 Pro  "}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.make == "Apple"
    assert result.model == "iPhone 15 Pro"


def test_empty_string_to_none() -> None:
    runner = _runner('[{"Make": "", "Model": ""}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.make is None
    assert result.model is None


def test_which_guard_no_exiftool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _x: None)
    result = probe(Path("/fake/test.mov"))
    assert result.kind == KIND_UNKNOWN
    assert result.make is None
    assert result.model is None


def test_device_name_fallback() -> None:
    runner = _runner('[{"Make": "Apple", "DeviceName": "iPhone 15 Pro"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.model == "iPhone 15 Pro"
    assert result.kind == KIND_IPHONE


def test_from_adapter_no_make_model() -> None:
    result = from_adapter(KIND_DASHCAM)
    assert result.kind == KIND_DASHCAM
    assert result.make is None
    assert result.model is None