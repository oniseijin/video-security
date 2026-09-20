from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_BIN = REPO_ROOT / ".venv" / "bin"


def _script(name: str) -> Path | None:
    p = VENV_BIN / name
    return p if p.exists() else None


def test_subcommand_entry_point_hoists_global_flags(
    tmp_path: Path,
) -> None:
    script = _script("vs-list")
    if script is None:
        pytest.skip("vs-list console script not installed in dev venv")
    db = tmp_path / "t.db"
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(f'[storage]\ndb_path = "{db}"\n')
    result = subprocess.run(
        [str(script), "--config", str(cfg), "--db", str(db)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_subcommand_entry_point_plain(tmp_path: Path) -> None:
    script = _script("vs-list")
    if script is None:
        pytest.skip("vs-list console script not installed in dev venv")
    result = subprocess.run(
        [str(script), "--db", str(tmp_path / "t.db")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_hoist_flags_ordering() -> None:
    from video_security.entries import GLOBAL_FLAGS

    orig_argv = sys.argv
    try:
        sys.argv = ["vs-analyze", "vid.mp4", "--config", "c.toml", "--no-llm"]
        argv = sys.argv[1:]
        pre: list[str] = []
        rest: list[str] = []
        i = 0
        while i < len(argv):
            arg = argv[i]
            if arg in GLOBAL_FLAGS and i + 1 < len(argv):
                pre.extend([arg, argv[i + 1]])
                i += 2
            else:
                rest.append(arg)
                i += 1
        assert pre == ["--config", "c.toml"]
        assert rest == ["vid.mp4", "--no-llm"]
    finally:
        sys.argv = orig_argv
