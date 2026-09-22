from __future__ import annotations

import dataclasses
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from video_security import db
from video_security.engine import EngineError, video_hash
from video_security.ingest.frames import probe_video


@dataclasses.dataclass
class RepairReport:
    repaired: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[str] = dataclasses.field(default_factory=list)


def _probe_ok(path: Path) -> bool:
    try:
        probe_video(path)
        return True
    except Exception:
        return False


def _find_reference(broken: Path) -> Path | None:
    for cand in sorted(broken.parent.glob("*.MP4")) + sorted(broken.parent.glob("*.mp4")):
        if cand.resolve() == broken.resolve():
            continue
        if cand.name.endswith(".vs-partial") or ".repaired." in cand.name:
            continue
        if _probe_ok(cand):
            return cand
    return None


def _untrunc_binary() -> str:
    tool = shutil.which("untrunc")
    if tool is None:
        raise EngineError(
            "untrunc not found on PATH — build it from "
            "https://github.com/anthwlock/untrunc and put it on PATH"
        )
    return tool


def repair_job(
    conn: sqlite3.Connection,
    job_id: int,
    reference: Path | None,
    report: RepairReport,
) -> None:
    job = db.get_job_by_id(conn, job_id)
    if job is None:
        report.failed += 1
        report.failures.append(f"job {job_id}: not found")
        return
    src = Path(job.video_path)
    if not src.exists():
        report.failed += 1
        report.failures.append(f"job {job_id}: video not found at {src}")
        return
    if _probe_ok(src):
        report.skipped += 1
        report.failures.append(f"job {job_id}: video is playable, nothing to repair")
        return

    tool = _untrunc_binary()
    ref = reference
    if ref is None:
        ref = _find_reference(src)
    if ref is None or not ref.exists():
        report.failed += 1
        report.failures.append(
            f"job {job_id}: no healthy reference clip found — pass --reference"
        )
        return

    repaired = src.with_name(f"{src.stem}.repaired{src.suffix}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        broken_copy = work / src.name
        shutil.copy2(src, broken_copy)
        try:
            result = subprocess.run(
                [tool, str(ref), str(broken_copy)],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            report.failed += 1
            report.failures.append(f"job {job_id}: untrunc failed: {e}")
            return
        fixed = broken_copy.with_name(
            broken_copy.name + "_fixed" + broken_copy.suffix
        )
        if not fixed.exists():
            report.failed += 1
            tail = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
            report.failures.append(f"job {job_id}: untrunc failed: {tail[0]}")
            return
        if not _probe_ok(fixed):
            report.failed += 1
            report.failures.append(
                f"job {job_id}: repaired file is still unreadable"
            )
            return
        shutil.move(str(fixed), str(repaired))

    new_hash = video_hash(repaired)
    conn.execute(
        "UPDATE jobs SET video_path = ?, video_hash = ?, status = 'pending', "
        "current_stage = 'pending', attempts = 0, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (str(repaired), new_hash, job_id),
    )
    conn.commit()
    report.repaired += 1


def auto_repair_job(conn: sqlite3.Connection, job_id: int) -> bool:
    job = db.get_job_by_id(conn, job_id)
    if job is None:
        return False
    src = Path(job.video_path)
    if ".repaired" in src.stem:
        return False
    if shutil.which("untrunc") is None:
        return False
    report = RepairReport()
    repair_job(conn, job_id, None, report)
    return report.repaired == 1


def run_repair(
    conn: sqlite3.Connection,
    job_ids: list[int],
    reference: Path | None = None,
) -> RepairReport:
    if not job_ids:
        raise EngineError("repair requires explicit --job")
    _untrunc_binary()
    report = RepairReport()
    for jid in job_ids:
        repair_job(conn, jid, reference, report)
    return report
