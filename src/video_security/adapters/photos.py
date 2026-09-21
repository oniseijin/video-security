from __future__ import annotations

import dataclasses
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_security.adapters import ClipInfo
from video_security.config import Config
from video_security.engine import EngineError

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}


def _ensure_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(None).replace(tzinfo=None)
    return dt


def _to_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(UTC).isoformat()


@dataclasses.dataclass
class PhotosAdapter:
    config: Config
    since: datetime | None = None
    album: str | None = None
    name: str = "photos"
    skipped_cloud_only: int = 0

    def discover_clips(self, source: Path) -> list[ClipInfo]:
        test_env = os.environ.get("VIDEO_SECURITY_TEST_PHOTOS_ASSETS")
        if test_env is not None:
            return self._discover_from_seam(json.loads(test_env))
        return self._discover_from_library(source)

    def _discover_from_seam(self, rows: list[list[Any]]) -> list[ClipInfo]:
        clips: list[ClipInfo] = []
        self.skipped_cloud_only = 0
        for row in rows:
            uuid: str = row[0]
            path_str: str | None = row[1]
            date_str: str | None = row[2]
            hidden: bool = row[3] if len(row) > 3 else False
            albums: list[str] = row[4] if len(row) > 4 else []

            if hidden:
                continue

            if self.album is not None and self.album not in albums:
                continue

            date: datetime | None = None
            if date_str is not None:
                date = datetime.fromisoformat(date_str)

            if self.since is not None and date is not None:
                if _ensure_naive(date) < self.since:
                    continue

            local_path: Path | None = None
            if path_str is not None:
                local_path = Path(path_str)
                if not local_path.exists():
                    local_path = None

            if local_path is None:
                self.skipped_cloud_only += 1
                continue

            if local_path.suffix.lower() not in VIDEO_SUFFIXES:
                continue

            clips.append(
                ClipInfo(
                    path=local_path,
                    mode="PHOTOS",
                    channel="front",
                    priority=self.config.adapter_photos.priority,
                    recording_start_utc=_to_utc_iso(date),
                    source_uuid=uuid,
                )
            )
        return clips

    def _discover_from_library(self, source: Path) -> list[ClipInfo]:
        try:
            import osxphotos
        except ImportError as e:
            raise EngineError(
                "osxphotos required for Photos import: uv pip install osxphotos"
            ) from e

        db = osxphotos.PhotosDB(str(source))
        photos = db.photos(movies=True, images=False)
        self.skipped_cloud_only = 0
        clips: list[ClipInfo] = []
        for photo in photos:
            uuid = self._get(photo, "uuid")
            hidden = self._get(photo, "hidden", False)
            if hidden:
                continue
            if self.album is not None:
                photo_albums = self._get(photo, "albums", [])
                album_titles = []
                for a in photo_albums:
                    t = self._get(a, "title")
                    if t:
                        album_titles.append(t)
                if self.album not in album_titles:
                    continue
            original_path = self._get(photo, "original_path") or self._get(
                photo, "path"
            )
            ismissing = self._get(photo, "ismissing", False)
            date = self._get(photo, "date")

            if self.since is not None and date is not None:
                if _ensure_naive(date) < self.since:
                    continue

            if original_path is None or ismissing:
                self.skipped_cloud_only += 1
                continue

            local = Path(original_path)
            if not local.exists():
                self.skipped_cloud_only += 1
                continue

            if local.suffix.lower() not in VIDEO_SUFFIXES:
                continue

            clips.append(
                ClipInfo(
                    path=local,
                    mode="PHOTOS",
                    channel="front",
                    priority=self.config.adapter_photos.priority,
                    recording_start_utc=_to_utc_iso(date),
                    source_uuid=uuid,
                )
            )
        return clips

    @staticmethod
    def _get(photo: object, attr: str, default: Any = None) -> Any:
        try:
            return getattr(photo, attr, default)
        except Exception:
            return default