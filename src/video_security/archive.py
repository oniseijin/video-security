from __future__ import annotations

import dataclasses
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from video_security import db
from video_security.config import Config
from video_security.engine import EngineError

_DISCOVERED_ROOTS: list[Path] = []
_MAX_DISCOVERY_DIRS = 20000


@dataclasses.dataclass
class ArchiveReport:
    archived: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_moved: int = 0
    bytes_saved: int = 0
    restored: int = 0
    deleted: int = 0
    moved: int = 0
    planned: int = 0
    failures: list[str] = dataclasses.field(default_factory=list)


def select_jobs(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id FROM jobs WHERE status = 'done' "
        "AND created_at < datetime('now', ?) "
        "AND id NOT IN (SELECT job_id FROM archived_originals) "
        "ORDER BY id",
        (f'-{days} days',),
    ).fetchall()


def _duration(path: Path) -> float | None:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _proxy(src: Path, dst: Path, video_height: int) -> None:
    cmd: list[str] = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"scale=-2:{video_height}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-movflags", "+faststart",
        "-f", "mp4", "-y", str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:])


def _find_clips_root(src: Path) -> Path | None:
    for p in src.parents:
        if p.name == "clips":
            return p.parent
    return None


def archive_job(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_job_by_id(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not found")
        return

    if row.status != "done":
        report.failed += 1
        report.failures.append(f"job {job_id}: not done (status={row.status})")
        return

    if db.get_archived_original(conn, job_id) is not None:
        report.skipped += 1
        return

    src = Path(row.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: video not found at {src}")
        return

    proxy_tmp = src.with_name(src.name + ".vs-partial")
    roots = config.archive.cold_roots()
    if not roots:
        report.failed += 1
        report.failures.append(f"job {job_id}: no cold location configured")
        return
    cold_root = Path(roots[0]).expanduser()

    try:
        _proxy(src, proxy_tmp, config.archive.video_height)
        orig_dur = _duration(src)
        proxy_dur = _duration(proxy_tmp)
        if orig_dur is None or proxy_dur is None or abs(orig_dur - proxy_dur) > 1.0:
            proxy_tmp.unlink(missing_ok=True)
            report.failed += 1
            report.failures.append(
                f"job {job_id}: duration mismatch (orig={orig_dur}, proxy={proxy_dur})"
            )
            return

        clips_root = _find_clips_root(src)
        if clips_root is not None:
            cold_dest = cold_root / src.relative_to(clips_root)
        else:
            cold_dest = cold_root / src.name
        cold_dest.parent.mkdir(parents=True, exist_ok=True)

        original_bytes = src.stat().st_size
        shutil.move(str(src), str(cold_dest))

        proxy_bytes = proxy_tmp.stat().st_size
        proxy_tmp.rename(src)

        db.insert_archived_original(
            conn, job_id, str(cold_dest), original_bytes, proxy_bytes
        )
        report.archived += 1
        report.bytes_moved += original_bytes
        report.bytes_saved += max(0, original_bytes - proxy_bytes)
    except (OSError, RuntimeError, sqlite3.Error) as e:
        proxy_tmp.unlink(missing_ok=True)
        cold_dest = cold_root / (
            src.relative_to(clips_root) if (clips_root := _find_clips_root(src)) else src.name
        )
        if cold_dest.exists() and not src.exists():
            try:
                shutil.move(str(cold_dest), str(src))
            except OSError:
                pass
        report.failed += 1
        report.failures.append(f"job {job_id}: {e}")


def delete_job(
    conn: sqlite3.Connection,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_job_by_id(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not found")
        return
    if row.status != "done":
        report.failed += 1
        report.failures.append(f"job {job_id}: not done (status={row.status})")
        return
    if db.get_archived_original(conn, job_id) is not None:
        report.skipped += 1
        return
    src = Path(row.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: video not found at {src}")
        return
    try:
        original_bytes = src.stat().st_size
        src.unlink()
        db.insert_archived_original(
            conn, job_id, str(src), original_bytes, 0, location="deleted"
        )
        report.deleted += 1
        report.bytes_saved += original_bytes
    except (OSError, sqlite3.Error) as e:
        report.failed += 1
        report.failures.append(f"job {job_id}: {e}")


def _cold_tail(path: Path) -> tuple[str, ...]:
    parts = path.parts
    if "clips" in parts:
        idx = len(parts) - 1 - parts[::-1].index("clips")
        return parts[idx:]
    return (path.name,)


def _find_relocated_root(
    tail: tuple[str, ...], expected_bytes: int | None, bases: list[Path]
) -> Path | None:
    visited = 0
    for base in bases:
        if not base.is_dir():
            continue
        stack: list[tuple[Path, int]] = [(base, 0)]
        while stack:
            d, depth = stack.pop()
            visited += 1
            if visited > _MAX_DISCOVERY_DIRS:
                return None
            cand = d.joinpath(*tail)
            try:
                if cand.is_file() and (
                    expected_bytes is None or cand.stat().st_size == expected_bytes
                ):
                    return d
            except OSError:
                pass
            if depth >= 4:
                continue
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if e.name == "clips":
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append((Path(e.path), depth + 1))
                except OSError:
                    continue
    return None


def _resolve_cold_path(
    recorded: str,
    roots: list[Path],
    expected_bytes: int | None = None,
) -> Path:
    p = Path(recorded)
    if p.exists():
        return p
    tail = _cold_tail(p)
    for root in [*_DISCOVERED_ROOTS, *roots]:
        cand = root.joinpath(*tail)
        if cand.exists():
            return cand
    bases: list[Path] = []
    for r in roots:
        if r.parent not in bases:
            bases.append(r.parent)
    parts = p.parts
    if "clips" in parts:
        idx = len(parts) - 1 - parts[::-1].index("clips")
        old_root = Path(*parts[:idx])
        for b in (old_root, old_root.parent):
            if b not in bases:
                bases.append(b)
    found = _find_relocated_root(tail, expected_bytes, bases)
    if found is not None:
        _DISCOVERED_ROOTS.append(found)
        return found.joinpath(*tail)
    return p


def deep_archive_job(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_archived_original(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not archived yet (run archive first)")
        return
    if row["location"] == "deleted":
        report.failed += 1
        report.failures.append(f"job {job_id}: original was deleted")
        return
    if row["proxy_cold_path"] is not None:
        report.skipped += 1
        return
    job = db.get_job_by_id(conn, job_id)
    if job is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: job not found")
        return
    src = Path(job.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: proxy not found at {src}")
        return
    roots = [Path(r).expanduser() for r in config.archive.cold_roots()]
    try:
        orig_cold = _resolve_cold_path(
            str(row["original_path"]),
            roots,
            expected_bytes=int(row["original_bytes"]),
        )
        if str(orig_cold) != str(row["original_path"]):
            conn.execute(
                "UPDATE archived_originals SET original_path = ? WHERE job_id = ?",
                (str(orig_cold), job_id),
            )
            conn.commit()
        proxy_dest = orig_cold.with_name(
            f"{orig_cold.stem}.proxy{orig_cold.suffix}"
        )
        proxy_dest.parent.mkdir(parents=True, exist_ok=True)
        proxy_bytes = src.stat().st_size
        shutil.move(str(src), str(proxy_dest))
        conn.execute(
            "UPDATE archived_originals SET proxy_cold_path = ? WHERE job_id = ?",
            (str(proxy_dest), job_id),
        )
        conn.commit()
        report.archived += 1
        report.bytes_moved += proxy_bytes
    except (OSError, sqlite3.Error) as e:
        report.failed += 1
        report.failures.append(f"job {job_id}: {e}")


def restore_job(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    report: ArchiveReport,
) -> None:
    row = db.get_archived_original(conn, job_id)
    if row is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not archived")
        return
    if row["location"] == "deleted":
        report.failed += 1
        report.failures.append(f"job {job_id}: original was deleted, not cold-stored")
        return

    job = db.get_job_by_id(conn, job_id)
    if job is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: job not found")
        return

    src = Path(job.video_path)
    roots = [Path(r).expanduser() for r in config.archive.cold_roots()]
    cold = _resolve_cold_path(
        str(row["original_path"]), roots, expected_bytes=int(row["original_bytes"])
    )
    if not cold.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: archived original not found at {cold}")
        return
    if str(cold) != str(row["original_path"]):
        conn.execute(
            "UPDATE archived_originals SET original_path = ? WHERE job_id = ?",
            (str(cold), job_id),
        )
        conn.commit()

    if src.exists():
        src.unlink()
    proxy_cold = row["proxy_cold_path"]
    if proxy_cold is not None:
        proxy_path = _resolve_cold_path(str(proxy_cold), roots)
        if proxy_path.is_file():
            proxy_path.unlink()
    shutil.move(str(cold), str(src))
    db.delete_archived_original(conn, job_id)
    report.restored += 1


def run_archive(
    conn: sqlite3.Connection,
    config: Config,
    days: int | None = None,
    job_ids: list[int] | None = None,
    dry_run: bool = False,
    restore: bool = False,
    delete: bool = False,
    deep: bool = False,
) -> ArchiveReport:
    report = ArchiveReport()

    if restore:
        if not job_ids:
            raise EngineError("--restore requires explicit --job")
        for jid in job_ids:
            restore_job(conn, config, jid, report)
        return report

    if deep:
        if job_ids:
            deep_ids = job_ids
        else:
            d = days if days is not None else config.archive.days
            deep_ids = [
                int(r["job_id"])
                for r in conn.execute(
                    "SELECT job_id FROM archived_originals "
                    "WHERE location = 'cold' AND proxy_cold_path IS NULL "
                    "AND archived_at < datetime('now', ?) ORDER BY job_id",
                    (f"-{d} days",),
                )
            ]
        if not dry_run and deep_ids:
            if not config.archive.cold_roots():
                raise EngineError(
                    "set [archive] cold_dirs in config "
                    "(priority-ordered cold storage locations)"
                )
        for jid in deep_ids:
            if dry_run:
                jrow = db.get_job_by_id(conn, jid)
                if jrow is None:
                    continue
                jpath = Path(jrow.video_path)
                if not jpath.exists():
                    continue
                arow = db.get_archived_original(conn, jid)
                if arow is None or arow["proxy_cold_path"] is not None:
                    continue
                report.planned += 1
                report.bytes_moved += jpath.stat().st_size
            else:
                deep_archive_job(conn, config, jid, report)
        return report

    if job_ids:
        jobs_to_process = job_ids
    else:
        d = days if days is not None else config.archive.days
        jobs_to_process = [int(r["id"]) for r in select_jobs(conn, d)]

    if not dry_run and jobs_to_process and not delete:
        if not config.archive.cold_roots():
            raise EngineError(
                "set [archive] cold_dirs in config "
                "(priority-ordered cold storage locations)"
            )

    for jid in jobs_to_process:
        if dry_run:
            jrow = db.get_job_by_id(conn, jid)
            if jrow is None or jrow.status != "done":
                continue
            jpath = Path(jrow.video_path)
            if not jpath.exists():
                continue
            if db.get_archived_original(conn, jid) is not None:
                continue
            report.planned += 1
            report.bytes_moved += jpath.stat().st_size
        else:
            if delete:
                delete_job(conn, jid, report)
            else:
                archive_job(conn, config, jid, report)

    return report

def relocate_cold(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    dry_run: bool = False,
) -> ArchiveReport:
    report = ArchiveReport()
    src_p = Path(src).expanduser()
    dst_p = Path(dst).expanduser()
    if not src_p.exists():
        raise EngineError(f"source cold location not found: {src_p}")
    rows = conn.execute(
        "SELECT job_id, original_path, proxy_cold_path FROM archived_originals "
        "ORDER BY job_id"
    ).fetchall()
    for row in rows:
        for col in ("original_path", "proxy_cold_path"):
            val = row[col]
            if val is None:
                continue
            old = Path(val)
            if not old.is_relative_to(src_p):
                continue
            new = dst_p / old.relative_to(src_p)
            if dry_run:
                report.planned += 1
                if old.exists():
                    report.bytes_moved += old.stat().st_size
                continue
            try:
                if not old.exists():
                    report.failed += 1
                    report.failures.append(
                        f"job {row['job_id']}: missing {old}"
                    )
                    continue
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
                conn.execute(
                    f"UPDATE archived_originals SET {col} = ? WHERE job_id = ?",
                    (str(new), row["job_id"]),
                )
                conn.commit()
                report.moved += 1
                report.bytes_moved += new.stat().st_size
            except (OSError, sqlite3.Error) as e:
                report.failed += 1
                report.failures.append(f"job {row['job_id']}: {e}")
    return report
