from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv"}


def is_stable(path: Path, checks: int = 2, interval_s: float = 5.0) -> bool:
    try:
        sizes = [path.stat().st_size]
        mtime = path.stat().st_mtime
    except OSError:
        return False
    if mtime > time.time() - interval_s * checks:
        return False
    for _ in range(checks - 1):
        time.sleep(interval_s)
        try:
            sizes.append(path.stat().st_size)
        except OSError:
            return False
    return len(set(sizes)) == 1


def scan_stable(directory: Path, seen: set[Path]) -> list[Path]:
    out: list[Path] = []
    if not directory.exists():
        return out
    for p in sorted(directory.iterdir()):
        if p.suffix.lower() not in VIDEO_SUFFIXES or p in seen:
            continue
        if is_stable(p):
            seen.add(p)
            out.append(p)
    return out


def watch_loop(
    directory: Path,
    on_files: Callable[[list[Path]], None],
    stop_check: Callable[[], bool] | None = None,
    poll_interval_s: float = 30.0,
    max_duration_s: float | None = None,
) -> None:
    seen: set[Path] = set()
    started = time.monotonic()
    while True:
        if stop_check is not None and stop_check():
            return
        if max_duration_s is not None and time.monotonic() - started >= max_duration_s:
            return
        files = scan_stable(directory, seen)
        if files:
            on_files(files)
        time.sleep(poll_interval_s)
