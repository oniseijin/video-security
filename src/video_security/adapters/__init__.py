from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Protocol

from video_security.config import Config


@dataclasses.dataclass
class ClipInfo:
    path: Path
    mode: str
    channel: str
    priority: float
    recording_start_utc: str | None
    pair_path: Path | None = None
    nmea_path: Path | None = None


class SourceAdapter(Protocol):
    name: str

    def discover_clips(self, source: Path) -> list[ClipInfo]: ...


def detect_adapter(source: Path) -> str:
    mode_names = {"NORMAL", "EVENT", "MANUAL", "PARKING", "PICTURE"}
    for d in _dirs_within(source, depth=2):
        if d.name.upper() in mode_names:
            return "mazda_cx8"
    return "generic"


def _dirs_within(source: Path, depth: int) -> list[Path]:
    out: list[Path] = []
    level: list[Path] = [source]
    for _ in range(depth):
        level = [
            d for p in level if p.is_dir() for d in p.iterdir() if d.is_dir()
        ]
        out.extend(level)
    return out


def get_adapter(name: str, config: Config) -> SourceAdapter:
    if name == "auto":
        raise ValueError("resolve 'auto' via detect_adapter before get_adapter")
    if name == "mazda_cx8":
        from video_security.adapters.mazda_cx8 import MazdaCx8Adapter

        return MazdaCx8Adapter(config)
    if name == "gopro":
        from video_security.adapters.gopro import GoProAdapter

        return GoProAdapter()
    if name == "generic":
        from video_security.adapters.generic import GenericAdapter

        return GenericAdapter()
    raise ValueError(f"unknown adapter: {name}")
