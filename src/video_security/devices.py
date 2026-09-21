from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

KIND_IPHONE = "iphone"
KIND_META_GLASSES = "meta_glasses"
KIND_DASHCAM = "dashcam"
KIND_UNKNOWN = "unknown"

Runner = Callable[[list[str]], str]


@dataclasses.dataclass(frozen=True)
class DeviceInfo:
    kind: str
    make: str | None
    model: str | None


def _kind(make: str | None, model: str | None) -> str:
    combined = f"{make or ''} {model or ''}".lower()
    if "iphone" in combined:
        return KIND_IPHONE
    if any(t in combined for t in ("ray-ban", "rayban", "meta")):
        return KIND_META_GLASSES
    return KIND_UNKNOWN


def _default_runner(args: list[str]) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=15, check=True
    ).stdout


def probe(path: Path, runner: Runner | None = None) -> DeviceInfo:
    if runner is None:
        if shutil.which("exiftool") is None:
            return DeviceInfo(KIND_UNKNOWN, None, None)
        runner = _default_runner
    try:
        result = runner(["exiftool", "-s2", "-json", "--", str(path)])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return DeviceInfo(KIND_UNKNOWN, None, None)
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return DeviceInfo(KIND_UNKNOWN, None, None)
    entry: dict[str, str] = data[0] if data else {}
    make_raw = entry.get("Make") or ""
    model_raw = entry.get("Model") or entry.get("DeviceName") or ""
    make: str | None = make_raw.strip() or None
    model: str | None = model_raw.strip() or None
    kind = _kind(make, model)
    return DeviceInfo(kind, make, model)


def from_adapter(kind: str, make: str | None = None, model: str | None = None) -> DeviceInfo:
    return DeviceInfo(kind, make, model)