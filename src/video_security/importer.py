from __future__ import annotations

import dataclasses
import json
import re
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
    skipped_cloud: int = 0
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


def _probe_ok(dest: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return True
    from video_security.ingest.frames import probe_video

    try:
        probe_video(dest)
        return True
    except Exception:
        return False


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
) -> int | None:
    try:
        if sidecar is not None and sidecar.exists():
            side_dst = dest.with_suffix(".NMEA")
            _copy_atomic(sidecar, side_dst)
        _copy_atomic(clip.path, dest)
        if not _verify_copy(clip.path, dest, h):
            dest.unlink(missing_ok=True)
            report.failed += 1
            return None
        if not _probe_ok(dest):
            dest.unlink(missing_ok=True)
            if sidecar is not None and sidecar.exists():
                dest.with_suffix(".NMEA").unlink(missing_ok=True)
            report.failed += 1
            print(f"import failed for {clip.path}: unreadable video (no moov/corrupt)")
            return None
        job_id = _register(
            conn, clip, dest, h, import_id, session_id, session_clips
        )
        if job_id is None:
            report.skipped += 1
            return None
        from video_security.metadata import extract_metadata

        meta = extract_metadata(dest)
        if meta is not None:
            conn.execute(
                "UPDATE jobs SET metadata_json = ? WHERE id = ?",
                (json.dumps(meta), job_id),
            )
            conn.commit()
        seen_hashes.add(h)
        report.imported += 1
        report.jobs.append(job_id)
        return job_id
    except (OSError, sqlite3.Error) as e:
        report.failed += 1
        print(f"import failed for {clip.path}: {e}")
        return None


def _record_uuid(
    conn: sqlite3.Connection,
    clip: ClipInfo,
    job_id: int,
    h: str,
) -> None:
    if clip.source_uuid is None:
        return
    db.insert_photos_import(conn, clip.source_uuid, job_id, h, clip.path.name)


def _record_hash_hit_uuid(
    conn: sqlite3.Connection,
    clip: ClipInfo,
    h: str,
) -> None:
    if clip.source_uuid is None:
        return
    if db.get_photos_import(conn, clip.source_uuid) is not None:
        return
    existing_job = db.get_job_by_hash(conn, h)
    if existing_job is not None:
        db.insert_photos_import(
            conn, clip.source_uuid, existing_job.id, h, clip.path.name
        )


def _merge_device_meta(
    conn: sqlite3.Connection,
    adapter_name: str,
    adapter: object,
    dest: Path,
    job_id: int,
) -> None:
    from video_security.devices import from_adapter as device_from_adapter
    from video_security.devices import probe as device_probe

    info = device_probe(dest)
    if info.kind == "unknown":
        adapter_kind = getattr(adapter, "device_kind", None)
        if adapter_kind is not None:
            info = device_from_adapter(
                adapter_kind,
                getattr(adapter, "device_make", None),
                getattr(adapter, "device_model", None),
            )
    row = conn.execute(
        "SELECT metadata_json FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    existing: dict[str, str] = {}
    if row and row["metadata_json"]:
        try:
            existing = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            pass
    existing["device_kind"] = info.kind
    if info.make:
        existing["device_make"] = info.make
    if info.model:
        existing["device_model"] = info.model
    conn.execute(
        "UPDATE jobs SET metadata_json = ? WHERE id = ?",
        (json.dumps(existing), job_id),
    )
    conn.commit()


def run_import(
    source: Path,
    config: Config,
    conn: sqlite3.Connection,
    adapter_name: str = "auto",
    since: datetime | None = None,
    album: str | None = None,
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
    if name == "photos":
        from video_security.adapters.photos import PhotosAdapter

        adapter = PhotosAdapter(config, since=since, album=album)
    clips = adapter.discover_clips(source)

    import_date = datetime.now(UTC).strftime("%Y%m%d")
    if source.suffix.lower() == ".photoslibrary":
        label = re.sub(r"[^A-Za-z0-9._-]", "-", source.stem)
    else:
        label = source.name
    import_id = f"{import_date}-{label}"
    report = ImportReport()
    report.skipped_cloud = getattr(adapter, "skipped_cloud_only", 0)
    seen_hashes: set[str] = set()
    clips_root = artifact_dir / "clips" / import_date
    spotlight_ignore(clips_root)
    spotlight_ignore(artifact_dir)

    for clip in clips:
        if clip.source_uuid is not None:
            if db.get_photos_import(conn, clip.source_uuid) is not None:
                report.skipped += 1
                continue

        if name == "photos":
            try:
                from video_security.devices import probe as device_probe

                info = device_probe(clip.path)
                if info.kind != "unknown":
                    device_priorities = config.adapter_photos.device_priorities
                    if info.kind in device_priorities:
                        clip.priority = device_priorities[info.kind]
            except Exception:
                info = None
        else:
            info = None

        h = video_hash(clip.path)
        if h in seen_hashes or db.get_job_by_hash(conn, h) is not None:
            _record_hash_hit_uuid(conn, clip, h)
            report.skipped += 1
            continue

        channel_dir = "rear" if clip.channel == "rear" else "front"
        dest = clips_root / clip.mode / channel_dir / clip.path.name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-{h[:8]}{dest.suffix}")

        session_id = clip.path.stem if clip.pair_path is not None else None

        session_clips = [{"channel": clip.channel, "path": str(dest)}]
        if clip.pair_path is not None:
            pair_dest = clips_root / clip.mode / "rear" / clip.pair_path.name
            session_clips.append({"channel": "rear", "path": str(pair_dest)})

        job_id = _import_one(
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
        if job_id is not None:
            _record_uuid(conn, clip, job_id, h)
            try:
                _merge_device_meta(conn, name, adapter, dest, job_id)
            except Exception:
                pass
            if info is not None and info.gps is not None:
                try:
                    db.insert_gps_row(
                        conn, job_id, 0, 0.0,
                        info.gps[0], info.gps[1],
                        None, None, None, None, None,
                    )
                    conn.commit()
                except Exception:
                    pass

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
                source_uuid=None,
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
