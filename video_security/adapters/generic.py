from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from video_security.adapters import ClipInfo

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv"}


def find_videos(source: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in ("*.mp4", "*.MP4", "*.mov", "*.MOV", "*.m4v", "*.mkv"):
        for p in source.rglob(pattern):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
    return sorted(out)


@dataclasses.dataclass
class GenericAdapter:
    name: str = "generic"
    priority: float = 0.5

    def discover_clips(self, source: Path) -> list[ClipInfo]:
        clips: list[ClipInfo] = []
        for p in find_videos(source):
            ts = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat()
            clips.append(
                ClipInfo(
                    path=p,
                    mode="NORMAL",
                    channel="front",
                    priority=self.priority,
                    recording_start_utc=ts,
                )
            )
        return clips
