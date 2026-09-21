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


def test_gps_position_parsing() -> None:
    runner = _runner('[{"Make": "Apple", "Model": "iPhone 15 Pro", '
        '"GPSPosition": "35 deg 39\' 44.52\\\" N, 139 deg 39\' 55.92\\\" E"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_IPHONE
    assert result.gps is not None
    lat, lon = result.gps
    assert lat == pytest.approx(35.0 + 39 / 60 + 44.52 / 3600)
    assert lon == pytest.approx(139.0 + 39 / 60 + 55.92 / 3600)


def test_gps_lat_lon_ref_parsing() -> None:
    runner = _runner('[{"GPSLatitude": "35 deg 39\' 44.52\\\"", '
        '"GPSLatitudeRef": "N", '
        '"GPSLongitude": "139 deg 39\' 55.92\\\"", '
        '"GPSLongitudeRef": "E"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.gps is not None
    lat, lon = result.gps
    assert lat == pytest.approx(35.0 + 39 / 60 + 44.52 / 3600)
    assert lon == pytest.approx(139.0 + 39 / 60 + 55.92 / 3600)


def test_gps_southern_western_negative() -> None:
    runner = _runner('[{"GPSPosition": "33 deg 55\' 22.00\\\" S, 18 deg 25\' 30.00\\\" W"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.gps is not None
    lat, lon = result.gps
    assert lat == pytest.approx(-(33.0 + 55 / 60 + 22.00 / 3600))
    assert lon == pytest.approx(-(18.0 + 25 / 60 + 30.00 / 3600))


def test_gps_unparseable_none() -> None:
    runner = _runner('[{"Make": "Apple", "Model": "iPhone 15 Pro", '
        '"GPSPosition": "invalid gps"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.kind == KIND_IPHONE
    assert result.gps is None


def test_gps_no_data_none() -> None:
    runner = _runner('[{"Make": "Apple", "Model": "iPhone 15 Pro"}]')
    result = probe(Path("/fake/test.mov"), runner=runner)
    assert result.gps is None