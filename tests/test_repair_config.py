from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from video_security import db, repair
from video_security.config import Config
from video_security.db import connect, init_db


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return conn


def _no_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(repair, "_LOCAL_UNTRUNC", tmp_path / "missing-untrunc")


def test_resolve_config_path_wins_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "untrunc-bin"
    fake.write_text("#!/bin/sh\n")
    cfg = Config()
    cfg.repair.untrunc_path = str(fake)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/untrunc")
    assert repair.resolve_untrunc(cfg) == str(fake)


def test_resolve_config_path_missing_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Config()
    cfg.repair.untrunc_path = str(tmp_path / "nope")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/untrunc")
    assert repair.resolve_untrunc(cfg) is None


def test_resolve_path_then_local_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Config()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/untrunc")
    assert repair.resolve_untrunc(cfg) == "/usr/bin/untrunc"

    monkeypatch.setattr(shutil, "which", lambda name: None)
    local = tmp_path / "local-untrunc"
    local.write_text("#!/bin/sh\n")
    monkeypatch.setattr(repair, "_LOCAL_UNTRUNC", local)
    assert repair.resolve_untrunc(cfg) == str(local)

    _no_local(monkeypatch, tmp_path)
    assert repair.resolve_untrunc(cfg) is None


def test_resolve_expands_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tool = home / "tools" / "untrunc"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    cfg = Config()
    cfg.repair.untrunc_path = "~/tools/untrunc"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert repair.resolve_untrunc(cfg) == str(tool)


def test_auto_repair_loud_bail(
    db_conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    j = db.create_job(db_conn, "/nonexistent/clip.mp4", "h1")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _no_local(monkeypatch, tmp_path)
    cfg = Config()

    assert repair.auto_repair_job(db_conn, j.id, cfg) is False
    err = capsys.readouterr().err
    assert "auto-repair skipped: untrunc not on PATH" in err
    assert "[repair] untrunc_path" in err
