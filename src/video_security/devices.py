from __future__ import annotations

import dataclasses
import json
import re
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
    gps: tuple[float, float] | None = None


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


def _parse_dms(dms: str, ref: str) -> float | None:
    m = re.match(r"([\d.]+)\s*deg\s*(\d+)?'?\s*([\d.]+)?\"?", dms)
    if not m:
        return None
    deg = float(m.group(1))
    mins = float(m.group(2) or 0)
    secs = float(m.group(3) or 0)
    val = deg + mins / 60 + secs / 3600
    if ref in ("S", "W"):
        val = -val
    return val


_DMS_PAIR = re.compile(
    r"([\d.]+)\s*deg\s*(\d+)?'?\s*([\d.]+)?\"?\s*([NS]),\s*"
    r"([\d.]+)\s*deg\s*(\d+)?'?\s*([\d.]+)?\"?\s*([EW])"
)


def _parse_gps(entry: dict[str, str]) -> tuple[float, float] | None:
    gps_pos = entry.get("GPSPosition")
    if gps_pos:
        m = _DMS_PAIR.match(gps_pos)
        if m:
            lat_dms = f"{m.group(1)} deg {m.group(2) or ''}' {m.group(3) or ''}\""
            lon_dms = f"{m.group(5)} deg {m.group(6) or ''}' {m.group(7) or ''}\""
            lat = _parse_dms(lat_dms, m.group(4))
            lon = _parse_dms(lon_dms, m.group(8))
            if lat is not None and lon is not None:
                return (lat, lon)
    lat_str = entry.get("GPSLatitude")
    lon_str = entry.get("GPSLongitude")
    lat_ref = entry.get("GPSLatitudeRef") or entry.get("GPSLatitudeRef") or ""
    lon_ref = entry.get("GPSLongitudeRef") or entry.get("GPSLongitudeRef") or ""
    if lat_str and lon_str:
        lat = _parse_dms(lat_str, lat_ref)
        lon = _parse_dms(lon_str, lon_ref)
        if lat is not None and lon is not None:
            return (lat, lon)
    return None


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
    gps = _parse_gps(entry)
    return DeviceInfo(kind, make, model, gps)


def from_adapter(kind: str, make: str | None = None, model: str | None = None) -> DeviceInfo:
    return DeviceInfo(kind, make, model)