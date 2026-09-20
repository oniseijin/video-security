from __future__ import annotations

from pathlib import Path

MARKER_NAME = ".metadata_never_index"


def spotlight_ignore(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER_NAME).touch(exist_ok=True)
    except OSError:
        pass
