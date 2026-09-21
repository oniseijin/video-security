from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

KEEP_FIELDS = (
    "Make",
    "Model",
    "DeviceName",
    "Software",
    "FirmwareVersion",
    "DateTimeOriginal",
    "CreateDate",
    "Duration",
    "VideoFrameRate",
    "ImageSize",
    "GPSLatitude",
    "GPSLongitude",
    "GPSTimeStamp",
    "TimeZone",
)


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def extract_metadata(video: Path) -> dict[str, Any] | None:
    tool = shutil.which("exiftool")
    if tool is None:
        return None
    try:
        result = subprocess.run(
            [tool, "-json", "-n", str(video)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    source = data[0]
    kept = {k: source[k] for k in KEEP_FIELDS if k in source}
    return kept or None
