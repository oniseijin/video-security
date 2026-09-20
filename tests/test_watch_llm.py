from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.golden import golden_clip
from video_security.config import Config
from video_security.db import (
    connect,
    init_db,
    update_event_keyframes,
)
from video_security.engine import EngineError, check_disk_watermark_once, set_watermark_config
from video_security.pipeline import analyze_video, enqueue_video
from video_security.watch import is_stable, scan_stable, watch_loop


def test_is_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 100)
    old = time.time() - 60
    import os

    os.utime(f, (old, old))
    assert is_stable(f, checks=2, interval_s=0.0)
    fresh = tmp_path / "b.mp4"
    fresh.write_bytes(b"y" * 100)
    assert not is_stable(fresh, checks=2, interval_s=5.0)


def test_is_stable_missing(tmp_path: Path) -> None:
    assert not is_stable(tmp_path / "nope.mp4", checks=1, interval_s=0.0)


def test_scan_stable(tmp_path: Path) -> None:
    import os

    f = tmp_path / "c.mp4"
    f.write_bytes(b"z" * 10)
    old = time.time() - 60
    os.utime(f, (old, old))
    (tmp_path / "notes.txt").write_text("ignore")
    seen: set[Path] = set()
    found = scan_stable(tmp_path, seen)
    assert found == [f]
    assert scan_stable(tmp_path, seen) == []


def test_watch_loop_duration(tmp_path: Path) -> None:
    calls: list[list[Path]] = []
    watch_loop(
        tmp_path,
        calls.append,
        poll_interval_s=0.0,
        max_duration_s=0.05,
    )
    assert isinstance(calls, list)


def test_disk_watermark_breaker(tmp_path: Path) -> None:
    set_watermark_config(999999.0)
    with pytest.raises(EngineError, match="watermark"):
        check_disk_watermark_once()
    set_watermark_config(None)
    check_disk_watermark_once()


def test_only_llm_rerun(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from video_security.cli import app

    db_file = tmp_path / "t.db"
    conn = connect(str(db_file))
    init_db(conn)
    clip = golden_clip(tmp_path / "g.mp4")
    job, _ = enqueue_video(conn, clip)
    cfg = Config()
    cfg.storage.artifact_dir = str(tmp_path / "art")
    analyze_video(job, cfg, conn, no_llm=True)

    kf = tmp_path / "kf.jpg"
    cv2.imwrite(str(kf), np.zeros((60, 80, 3), dtype=np.uint8))
    rows = conn.execute(
        "SELECT id FROM events WHERE job_id = ?", (job.id,)
    ).fetchall()
    assert rows
    for r in rows:
        update_event_keyframes(conn, r["id"], json.dumps([str(kf)]))

    class FakeOllama:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def unload(self, *a: object, **k: object) -> None:
            return None

        def generate_json(self, *a: object, **k: object) -> dict[str, object]:
            return {
                "relevant": True,
                "event_type": "suspicious_behavior",
                "description": "d",
                "confidence": "high",
                "evidence_rationale": "e",
                "recommended_action": "log_only",
            }

        def model_digest(self, model: str) -> str:
            return "sha256:fake2"

    from video_security.llm import ollama as ollama_mod

    original = ollama_mod.OllamaClient
    ollama_mod.OllamaClient = FakeOllama  # type: ignore[assignment, misc]
    try:
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(db_file), "analyze", "--only-llm"]
        )
    finally:
        ollama_mod.OllamaClient = original  # type: ignore[misc]
    assert result.exit_code == 0, result.output
    conn.close()
    conn = connect(str(db_file))
    triaged = conn.execute(
        "SELECT COUNT(*) FROM analysis_results WHERE model_digest = 'sha256:fake2'"
    ).fetchone()[0]
    assert triaged >= 1
    conn.close()
