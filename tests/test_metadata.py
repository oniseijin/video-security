from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_security.metadata import exiftool_available, extract_metadata


def test_exiftool_available_flag() -> None:
    assert isinstance(exiftool_available(), bool)


def test_extract_metadata_filters_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.metadata.shutil.which", lambda _n: "/usr/bin/exiftool"
    )
    payload = [
        {
            "Make": "Mazda",
            "Model": "CX-8",
            "DateTimeOriginal": "2025:09:22 11:57:04",
            "Junk": "ignored",
            "SourceFile": "/x.mp4",
        }
    ]

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(
        "video_security.metadata.subprocess.run", fake_run
    )
    meta = extract_metadata(tmp_path / "v.mp4")
    assert meta == {
        "Make": "Mazda",
        "Model": "CX-8",
        "DateTimeOriginal": "2025:09:22 11:57:04",
    }


def test_extract_metadata_no_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("video_security.metadata.shutil.which", lambda _n: None)
    assert extract_metadata(tmp_path / "v.mp4") is None


def test_extract_metadata_bad_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "video_security.metadata.shutil.which", lambda _n: "/usr/bin/exiftool"
    )

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="err", stderr="err")

    monkeypatch.setattr(
        "video_security.metadata.subprocess.run", fake_run
    )
    assert extract_metadata(tmp_path / "v.mp4") is None
