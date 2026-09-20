from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from video_security import db
from video_security.adapters import ClipInfo, detect_adapter, get_adapter
from video_security.config import Config
from video_security.engine import EngineError, disk_free_gb, video_hash
from video_security.fs import spotlight_ignore


@dataclasses.dataclass
class ImportReport:
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    jobs: list[int] = dataclasses.field(default_factory=list)


def _copy_atomic(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".vs-partial")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tmp)
        tmp.replace(dst)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _verify_copy(src: Path, dst: Path, expected_hash: str) -> bool:
    if not dst.exists():
        return False
    if dst.stat().st_size != src.stat().st_size:
        return False
    return video_hash(dst) == expected_hash


def _register(
    conn: sqlite3.Connection,
    clip: ClipInfo,
    dest: Path,
    h: str,
    import_id: str,
    session_id: str | None,
    session_clips: list[dict[str, str]],
) -> int | None:
    existing = db.get_job_by_hash(conn, h)
    if existing is not None:
        return None
    job = db.create_job(conn, str(dest), h)
    db.set_job_import_meta(conn, job.id, import_id, clip.recording_start_utc)
    db.insert_clip(
        conn,
        job.id,
        0,
        clip.path.name,
        clip.channel,
        clip.priority,
        clip.recording_start_utc,
    )
    if session_id is not None:
        db.insert_session(conn, job.id, session_id, json.dumps(session_clips))
    return job.id


def _import_one(
    conn: sqlite3.Connection,
    clip: ClipInfo,
    dest: Path,
    h: str,
    import_id: str,
    session_id: str | None,
    session_clips: list[dict[str, str]],
    report: ImportReport,
    seen_hashes: set[str],
    sidecar: Path | None = None,
) -> None:
    try:
        if sidecar is not None and sidecar.exists():
            side_dst = dest.with_suffix(".NMEA")
            _copy_atomic(sidecar, side_dst)
        _copy_atomic(clip.path, dest)
        if not _verify_copy(clip.path, dest, h):
            dest.unlink(missing_ok=True)
            report.failed += 1
            return
        job_id = _register(
            conn, clip, dest, h, import_id, session_id, session_clips
        )
        if job_id is None:
            report.skipped += 1
            return
        seen_hashes.add(h)
        report.imported += 1
        report.jobs.append(job_id)
    except (OSError, sqlite3.Error) as e:
        report.failed += 1
        print(f"import failed for {clip.path}: {e}")


def run_import(
    source: Path,
    config: Config,
    conn: sqlite3.Connection,
    adapter_name: str = "auto",
) -> ImportReport:
    if not source.exists():
        raise EngineError(f"source not found: {source}")
    artifact_dir = Path(config.storage.artifact_dir).expanduser()
    if not artifact_dir.exists():
        raise EngineError(f"artifact dir not mounted: {artifact_dir}")
    free_gb = disk_free_gb(artifact_dir)
    if free_gb < config.import_.preflight_gb:
        raise EngineError(
            f"artifact disk low: {free_gb:.1f} GB free < "
            f"{config.import_.preflight_gb} GB preflight"
        )

    name = adapter_name
    if name == "auto":
        name = detect_adapter(source)
    adapter = get_adapter(name, config)
    clips = adapter.discover_clips(source)

    import_date = datetime.now(UTC).strftime("%Y%m%d")
    import_id = f"{import_date}-{source.name}"
    report = ImportReport()
    seen_hashes: set[str] = set()
    clips_root = artifact_dir / "clips" / import_date
    spotlight_ignore(clips_root)
    spotlight_ignore(artifact_dir)

    for clip in clips:
        h = video_hash(clip.path)
        if h in seen_hashes or db.get_job_by_hash(conn, h) is not None:
            report.skipped += 1
            continue

        channel_dir = "rear" if clip.channel == "rear" else "front"
        dest = clips_root / clip.mode / channel_dir / clip.path.name
        session_id = clip.path.stem if clip.pair_path is not None else None

        session_clips = [{"channel": clip.channel, "path": str(dest)}]
        if clip.pair_path is not None:
            pair_dest = clips_root / clip.mode / "rear" / clip.pair_path.name
            session_clips.append({"channel": "rear", "path": str(pair_dest)})

        _import_one(
            conn,
            clip,
            dest,
            h,
            import_id,
            session_id,
            session_clips,
            report,
            seen_hashes,
            sidecar=clip.nmea_path,
        )

        if clip.pair_path is not None:
            ph = video_hash(clip.pair_path)
            if ph in seen_hashes or db.get_job_by_hash(conn, ph) is not None:
                report.skipped += 1
                continue
            rear_clip = dataclasses.replace(
                clip,
                path=clip.pair_path,
                channel="rear",
                pair_path=None,
                nmea_path=None,
            )
            pair_dest = clips_root / clip.mode / "rear" / clip.pair_path.name
            _import_one(
                conn,
                rear_clip,
                pair_dest,
                ph,
                import_id,
                session_id,
                session_clips,
                report,
                seen_hashes,
            )
    return report
