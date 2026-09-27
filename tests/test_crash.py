from __future__ import annotations

import sqlite3
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
from typer.testing import CliRunner

from video_security.cli import app
from video_security.config import Config, CrashConfig
from video_security.crashscan import is_mazda_event_path, run_crash_calibration, run_crash_scan
from video_security.db import connect, init_db, insert_clip
from video_security.ingest.audio import AudioHit, audio_transients
from video_security.prefilter.crash import (
    GpsPoint,
    JoltHit,
    SpeedHit,
    detect_crash_events,
    fuse_crash,
    jolt_hits,
    speed_drop_hits,
)

runner = CliRunner()


def _write_gray_clip(
    path: Path,
    txs: list[int],
    fps: int = 25,
    size: tuple[int, int] = (320, 240),
) -> Path:
    rng = np.random.default_rng(7)
    tex = rng.integers(0, 255, size=size, dtype=np.uint8)
    h, w = size
    frames = [
        cv2.warpAffine(
            tex, np.array([[1, 0, tx], [0, 1, 0]], dtype=np.float32), (w, h)
        )
        for tx in txs
    ]
    data = b"".join(f.tobytes() for f in frames)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        input=data,
        capture_output=True,
    )
    assert proc.returncode == 0
    return path


def _jolt_clip(path: Path) -> Path:
    txs = [2 * i + (90 if i >= 30 else 0) for i in range(60)]
    return _write_gray_clip(path, txs)


def _static_clip(path: Path) -> Path:
    return _write_gray_clip(path, [0] * 60)


def test_crash_config_defaults() -> None:
    cfg = CrashConfig()
    assert cfg.enabled is False
    assert cfg.jolt_sigma == 6.0
    assert cfg.audio_sigma == 5.0
    assert cfg.audio_confirm_sigma == 7.5
    assert cfg.speed_drop_kmh == 25.0
    assert cfg.window_sec == 3.0
    assert cfg.min_signals == 2


def test_crash_config_toml_overrides(tmp_path: Path) -> None:
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        "[crash]\nenabled = true\njolt_sigma = 4.5\nspeed_drop_kmh = 30\n"
        "audio_confirm_sigma = 9.0\n"
    )
    from video_security.config import load_config

    cfg = load_config(cfg_file)
    assert cfg.crash.enabled is True
    assert cfg.crash.jolt_sigma == 4.5
    assert cfg.crash.speed_drop_kmh == 30.0
    assert cfg.crash.audio_sigma == 5.0
    assert cfg.crash.audio_confirm_sigma == 9.0


def test_jolt_hits_planted_spike(tmp_path: Path) -> None:
    clip = _jolt_clip(tmp_path / "jolt.mp4")
    hits = jolt_hits(clip, 6.0)
    assert len(hits) == 1
    assert hits[0].time_sec == 1.2
    assert hits[0].spike_px > 5 * hits[0].baseline_px


def test_jolt_hits_static_clip_no_hit(tmp_path: Path) -> None:
    clip = _static_clip(tmp_path / "static.mp4")
    assert jolt_hits(clip, 6.0) == []


def test_audio_transients_burst() -> None:
    sr = 16000
    t = np.arange(sr * 5, dtype=np.float32) / sr
    rng = np.random.default_rng(3)
    pcm = rng.normal(0, 0.01, sr * 5).astype(np.float32)
    burst = (0.8 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    pcm[2 * sr : 2 * sr + sr // 10] += burst[: sr // 10]
    hits = audio_transients(pcm, sr, 5.0, 0.02)
    assert len(hits) == 1
    assert hits[0].start_sec == 2.0
    assert hits[0].end_sec == 2.1
    assert hits[0].sigma_multiple > 5.0


def test_audio_transients_sustained_noise_no_hit() -> None:
    sr = 16000
    t = np.arange(sr * 5, dtype=np.float32) / sr
    rng = np.random.default_rng(3)
    sustained = (
        0.3 * np.sin(2 * np.pi * 100 * t)
        + rng.normal(0, 0.005, sr * 5)
    ).astype(np.float32)
    assert audio_transients(sustained, sr, 5.0, 0.02) == []


def test_speed_drop_cliff() -> None:
    pts = [
        GpsPoint(0.0, 60.0, None),
        GpsPoint(0.5, 60.0, None),
        GpsPoint(1.0, 58.0, None),
        GpsPoint(1.5, 20.0, None),
        GpsPoint(2.0, 18.0, None),
    ]
    hits = speed_drop_hits(pts, 25.0)
    assert len(hits) == 1
    assert hits[0].rule == "speed_drop"
    assert hits[0].drop_kmh >= 38.0


def test_speed_gradual_braking_no_hit() -> None:
    pts = [GpsPoint(i * 0.7, 60.0 - 4.0 * i, None) for i in range(8)]
    assert speed_drop_hits(pts, 25.0) == []


def test_negative_time_gps_ignored() -> None:
    pre_roll = [
        GpsPoint(-2.0, 60.0, None),
        GpsPoint(-1.5, 20.0, None),
        GpsPoint(-1.0, 60.0, 10.0, 35.0, 135.0),
        GpsPoint(-0.6, 60.0, 190.0, 35.001, 135.0),
    ]
    assert speed_drop_hits(pre_roll, 25.0) == []
    mixed = [
        GpsPoint(-1.0, 60.0, None),
        GpsPoint(-0.5, 20.0, None),
        GpsPoint(0.0, 60.0, None),
        GpsPoint(0.4, 58.0, None),
    ]
    assert speed_drop_hits(mixed, 25.0) == []


def test_bearing_snap_requires_moving() -> None:
    moving = [
        GpsPoint(0.0, 40.0, 10.0, 35.0, 135.0),
        GpsPoint(0.4, 40.0, 190.0, 35.0001, 135.0),
    ]
    hits = speed_drop_hits(moving, 25.0)
    assert len(hits) == 1
    assert hits[0].rule == "bearing_snap"


def test_bearing_snap_needs_both_points_fast() -> None:
    one_slow = [
        GpsPoint(0.0, 19.0, 10.0, 35.0, 135.0),
        GpsPoint(0.4, 40.0, 190.0, 35.0001, 135.0),
    ]
    assert speed_drop_hits(one_slow, 25.0) == []


def test_bearing_snap_needs_travel_distance() -> None:
    jitter = [
        GpsPoint(0.0, 5.0, 10.0, 35.0, 135.0),
        GpsPoint(0.4, 5.0, 190.0, 35.0, 135.0),
    ]
    assert speed_drop_hits(jitter, 25.0) == []
    parked_jitter = [
        GpsPoint(0.0, 40.0, 10.0, 35.0, 135.0),
        GpsPoint(0.4, 40.0, 190.0, 35.00001, 135.0),
    ]
    assert speed_drop_hits(parked_jitter, 25.0) == []


def test_fusion_g_plus_one_signal_fires() -> None:
    cfg = CrashConfig()
    events = fuse_crash(True, [JoltHit(13.1, 34.2, 1.8)], [], [], cfg)
    assert len(events) == 1
    assert events[0].priority == 0.95
    assert events[0].signals == ["G", "J"]
    assert events[0].rule == "G+J"
    assert events[0].start_sec == 12.1
    assert events[0].end_sec == 14.1


def test_fusion_g_plus_base_audio_does_not_fire() -> None:
    cfg = CrashConfig()
    assert fuse_crash(True, [], [AudioHit(13.0, 13.2, 6.0)], [], cfg) == []
    events = fuse_crash(True, [], [AudioHit(13.0, 13.2, 8.0)], [], cfg)
    assert len(events) == 1
    assert events[0].signals == ["A", "G"]
    assert events[0].rule == "A+G"


def test_fusion_g_plus_confirm_boundary_sigma() -> None:
    cfg = CrashConfig()
    at_threshold = fuse_crash(True, [], [AudioHit(1.0, 1.2, 7.5)], [], cfg)
    assert len(at_threshold) == 1
    below = fuse_crash(True, [], [AudioHit(1.0, 1.2, 7.4)], [], cfg)
    assert below == []


def test_fusion_single_signal_never_fires() -> None:
    cfg = CrashConfig()
    assert fuse_crash(False, [JoltHit(1.0, 30.0, 2.0)], [], [], cfg) == []
    assert fuse_crash(False, [], [AudioHit(1.0, 1.2, 6.0)], [], cfg) == []
    assert fuse_crash(False, [], [], [SpeedHit(1.0, 40.0, "speed_drop")], cfg) == []
    assert fuse_crash(True, [], [], [], cfg) == []


def test_fusion_two_of_three_without_g_fires() -> None:
    cfg = CrashConfig()
    events = fuse_crash(
        False,
        [JoltHit(13.1, 34.2, 1.8)],
        [AudioHit(13.0, 13.2, 6.0)],
        [],
        cfg,
    )
    assert len(events) == 1
    assert events[0].signals == ["A", "J"]
    assert events[0].priority == 0.95


def test_fusion_signals_outside_window_do_not_join() -> None:
    cfg = CrashConfig(window_sec=3.0)
    events = fuse_crash(
        True,
        [JoltHit(1.0, 30.0, 2.0)],
        [],
        [SpeedHit(10.0, 40.0, "speed_drop")],
        cfg,
    )
    assert [(e.rule, e.start_sec) for e in events] == [("G+J", 0.0), ("G+S", 9.0)]
    events = fuse_crash(
        False,
        [JoltHit(1.0, 30.0, 2.0)],
        [AudioHit(8.0, 8.2, 6.0)],
        [],
        cfg,
    )
    assert events == []


def test_fusion_min_signals_respected() -> None:
    cfg = CrashConfig(min_signals=3)
    assert fuse_crash(True, [JoltHit(1.0, 30.0, 2.0)], [], [], cfg) == []
    events = fuse_crash(
        True, [JoltHit(1.0, 30.0, 2.0)], [AudioHit(1.1, 1.3, 8.0)], [], cfg
    )
    assert len(events) == 1


def test_fusion_records_signal_values() -> None:
    cfg = CrashConfig()
    events = fuse_crash(
        True,
        [JoltHit(13.1, 34.2, 1.8)],
        [AudioHit(13.0, 13.2, 9.13)],
        [SpeedHit(13.4, 41.2, "speed_drop")],
        cfg,
    )
    hits_raw = events[0].evidence["hits"]
    assert isinstance(hits_raw, dict)
    thresholds = events[0].evidence["thresholds"]
    assert isinstance(thresholds, dict)
    assert hits_raw["J"] == [
        {"time_sec": 13.1, "spike_px": 34.2, "baseline_px": 1.8}
    ]
    assert hits_raw["A"] == [
        {"start_sec": 13.0, "end_sec": 13.2, "sigma_multiple": 9.1}
    ]
    assert hits_raw["S"] == [
        {"time_sec": 13.4, "drop_kmh": 41.2, "rule": "speed_drop"}
    ]
    assert thresholds["jolt_sigma"] == 6.0
    assert thresholds["speed_drop_kmh"] == 25.0
    assert thresholds["audio_confirm_sigma"] == 7.5


def test_crash_events_exempt_from_merge_gap() -> None:
    cfg = CrashConfig()
    jolt = [JoltHit(1.0, 30.0, 2.0), JoltHit(5.0, 30.0, 2.0)]
    audio = [AudioHit(1.2, 1.4, 6.0), AudioHit(5.2, 5.4, 6.0)]
    events = fuse_crash(False, jolt, audio, [], cfg)
    assert len(events) == 2
    assert events[1].start_sec - events[0].end_sec < cfg.window_sec


def test_detect_crash_events_disabled_returns_empty(tmp_path: Path) -> None:
    clip = _jolt_clip(tmp_path / "jolt.mp4")
    conn = sqlite3.connect(":memory:")
    events = detect_crash_events(
        conn,
        1,
        str(clip),
        CrashConfig(),
        [AudioHit(1.0, 1.2, 6.0)],
        [GpsPoint(1.0, 60.0, 90.0), GpsPoint(1.4, 20.0, 90.0)],
    )
    assert events == []


def test_detect_crash_events_rear_channel_excludes_jolt(tmp_path: Path) -> None:
    clip_path = tmp_path / "clips" / "20260920" / "EVENT" / "rear"
    clip_path.mkdir(parents=True)
    clip = _jolt_clip(clip_path / "260920121500.MP4")
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    insert_clip(conn, 1, 0, "260920121500.MP4", "rear", 1.0, None)
    cfg = CrashConfig(enabled=True)
    events = detect_crash_events(conn, 1, str(clip), cfg, [], [])
    assert events == []

    insert_clip(conn, 2, 0, "260920121500.MP4", "front", 1.0, None)
    events = detect_crash_events(conn, 2, str(clip), cfg, [], [])
    assert len(events) == 1
    assert events[0].signals == ["G", "J"]
    conn.close()


def test_is_mazda_event_path_precision() -> None:
    assert is_mazda_event_path("/a/clips/20260920/EVENT/front/260920121500.MP4")
    assert is_mazda_event_path("/a/clips/20260920/EVENT/rear/260920121500.MP4")
    assert not is_mazda_event_path("/a/clips/20260920/NORMAL/front/260920121500.MP4")
    assert not is_mazda_event_path("/home/user/event/cool.mp4")
    assert not is_mazda_event_path("/a/clips/20260920/EVENT/front/clip.mp4")
    assert not is_mazda_event_path("/somewhere/EVENT/front/260920121500.MP4")
    assert not is_mazda_event_path("/a/clips/EVENT/front/260920121500.MP4")


def _seed_scan_db(tmp_path: Path, jolt_clip: Path, static_clip: Path) -> Path:
    event_dir = tmp_path / "clips" / "20260920" / "EVENT" / "front"
    normal_dir = tmp_path / "clips" / "20260920" / "NORMAL" / "front"
    event_dir.mkdir(parents=True, exist_ok=True)
    normal_dir.mkdir(parents=True, exist_ok=True)
    jolt_dst = event_dir / "260920121500.MP4"
    jolt_dst.write_bytes(jolt_clip.read_bytes())
    static_dst = event_dir / "260920130000.MP4"
    static_dst.write_bytes(static_clip.read_bytes())
    missing = event_dir / "260920140000.MP4"
    missing.write_bytes(static_clip.read_bytes())
    normal_dst = normal_dir / "260920121500.MP4"
    normal_dst.write_bytes(jolt_clip.read_bytes())

    db_path = tmp_path / "scan.db"
    conn = connect(str(db_path))
    init_db(conn)
    from video_security.db import create_job, insert_gps_row, update_job_status

    j1 = create_job(conn, str(jolt_dst), "h1")
    insert_clip(conn, j1.id, 0, "260920121500.MP4", "front", 1.0, None)
    for t, speed in [(0.0, 60.0), (1.0, 58.0), (1.5, 20.0), (2.0, 18.0)]:
        insert_gps_row(conn, j1.id, 0, t, None, None, speed, 90.0, None, None, None)
    update_job_status(conn, j1.id, "done")

    j2 = create_job(conn, str(static_dst), "h2")
    insert_clip(conn, j2.id, 0, "260920130000.MP4", "front", 1.0, None)
    update_job_status(conn, j2.id, "done")

    j3 = create_job(conn, str(missing), "h3")
    insert_clip(conn, j3.id, 0, "260920140000.MP4", "front", 1.0, None)
    update_job_status(conn, j3.id, "done")

    j4 = create_job(conn, str(normal_dst), "h4")
    insert_clip(conn, j4.id, 0, "260920121500.MP4", "front", 0.3, None)
    update_job_status(conn, j4.id, "done")
    conn.close()
    return db_path


def test_crash_scan_dry_report(tmp_path: Path) -> None:
    jolt_clip = _jolt_clip(tmp_path / "jolt_src.mp4")
    static_clip = _static_clip(tmp_path / "static_src.mp4")
    db_path = _seed_scan_db(tmp_path, jolt_clip, static_clip)
    result = runner.invoke(app, ["--db", str(db_path), "crash-scan"])
    assert result.exit_code == 0
    assert "3 EVENT-mode done job(s) scanned" in result.stdout
    assert "job 1" in result.stdout
    assert "WOULD-FIRE" in result.stdout
    assert "G+J+S" in result.stdout
    assert "insufficient" in result.stdout
    assert "job 4" not in result.stdout

    conn = connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert count == 0


def test_crash_scan_job_filter_and_limit(tmp_path: Path) -> None:
    jolt_clip = _jolt_clip(tmp_path / "jolt_src.mp4")
    static_clip = _static_clip(tmp_path / "static_src.mp4")
    db_path = _seed_scan_db(tmp_path, jolt_clip, static_clip)
    result = runner.invoke(app, ["--db", str(db_path), "crash-scan", "--job", "1"])
    assert result.exit_code == 0
    assert "1 EVENT-mode done job(s) scanned" in result.stdout
    assert "job 3" not in result.stdout

    result = runner.invoke(app, ["--db", str(db_path), "crash-scan", "--limit", "1"])
    assert result.exit_code == 0
    assert "1 EVENT-mode done job(s) scanned" in result.stdout
    assert "job 1" in result.stdout
    assert "job 2" not in result.stdout


def test_crash_scan_unavailable_video(tmp_path: Path) -> None:
    static_clip = _static_clip(tmp_path / "static_src.mp4")
    db_path = _seed_scan_db(tmp_path, static_clip, static_clip)
    conn = connect(str(db_path))
    row = conn.execute(
        "SELECT video_path FROM jobs WHERE id = 1"
    ).fetchone()
    Path(row["video_path"]).unlink()
    conn.close()
    report = run_crash_scan(connect(str(db_path)), Config(), job_id=1)
    assert "unavailable" in report
    assert "video missing" in report


def _write_wav(path: Path, pcm: np.ndarray, sr: int = 16000) -> Path:
    data = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(data)
    return path


def _burst_wav(path: Path) -> Path:
    sr = 16000
    t = np.arange(sr * 5, dtype=np.float32) / sr
    rng = np.random.default_rng(3)
    pcm = rng.normal(0, 0.01, sr * 5).astype(np.float32)
    burst = (0.8 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    pcm[2 * sr : 2 * sr + sr // 10] += burst[: sr // 10]
    return _write_wav(path, pcm)


def _quiet_wav(path: Path) -> Path:
    sr = 16000
    rng = np.random.default_rng(5)
    pcm = rng.normal(0, 0.01, sr * 5).astype(np.float32)
    return _write_wav(path, pcm)


def _seed_calibrate_db(tmp_path: Path) -> Path:
    from video_security.db import create_job, insert_gps_row, update_job_status

    clips = tmp_path / "clips" / "20260920"
    normal_dir = clips / "NORMAL" / "front"
    event_dir = clips / "EVENT" / "front"
    normal_dir.mkdir(parents=True)
    event_dir.mkdir(parents=True)

    burst_path = normal_dir / "260920121500.MP4"
    _burst_wav(burst_path)
    quiet_path = normal_dir / "260920130000.MP4"
    _quiet_wav(quiet_path)
    third_path = normal_dir / "260920140000.MP4"
    _quiet_wav(third_path)
    event_path = event_dir / "260920121500.MP4"
    _burst_wav(event_path)

    db_path = tmp_path / "cal.db"
    conn = connect(str(db_path))
    init_db(conn)
    j1 = create_job(conn, str(burst_path), "c1")
    insert_clip(conn, j1.id, 0, "260920121500.MP4", "front", 5.0, None)
    for t, speed in [(-1.0, 60.0), (-0.5, 20.0), (1.5, 60.0), (2.0, 20.0)]:
        insert_gps_row(conn, j1.id, 0, t, 35.0, 135.0, speed, 90.0, None, None, None)
    update_job_status(conn, j1.id, "done")

    j2 = create_job(conn, str(quiet_path), "c2")
    insert_clip(conn, j2.id, 0, "260920130000.MP4", "front", 5.0, None)
    update_job_status(conn, j2.id, "done")

    j3 = create_job(conn, str(third_path), "c3")
    insert_clip(conn, j3.id, 0, "260920140000.MP4", "front", 5.0, None)
    update_job_status(conn, j3.id, "done")

    j4 = create_job(conn, str(event_path), "c4")
    insert_clip(conn, j4.id, 0, "260920121500.MP4", "front", 5.0, None)
    update_job_status(conn, j4.id, "done")
    conn.close()
    return db_path


def test_crash_calibrate_report(tmp_path: Path) -> None:
    db_path = _seed_calibrate_db(tmp_path)
    conn = connect(str(db_path))
    report = run_crash_calibration(conn, Config())
    conn.close()

    assert "crash-calibrate: 3 NORMAL-mode done job(s) sampled" in report
    assert "job 1" in report
    assert "A base 1 (max" in report
    assert "A confirm 1" in report
    assert "S 1 (speed_drop=1)" in report
    assert "A+S co-occurrence within window_sec: 1" in report
    assert "job 2" in report
    assert "A base 0" in report
    assert "job 4" not in report
    assert "summary:" in report
    assert "clip-hours" in report
    assert "A-base: 1 (" in report
    assert "A-confirm: 1 (" in report
    assert "S: 1 (" in report
    assert "A+S co-occurrence (no-G would-fire): 1 (" in report
    assert "suggested thresholds: noise sigma p99 =" in report

    conn = connect(str(db_path))
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    evidence = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE evidence_json IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert events == 0
    assert evidence == 0


def test_crash_calibrate_limit_even_sampling(tmp_path: Path) -> None:
    db_path = _seed_calibrate_db(tmp_path)
    conn = connect(str(db_path))
    report = run_crash_calibration(conn, Config(), limit=2)
    assert "2 NORMAL-mode done job(s) sampled" in report
    assert "job 2" not in report
    assert "job 3" in report

    report = run_crash_calibration(conn, Config(), limit=0)
    assert "3 NORMAL-mode done job(s) sampled" in report
    conn.close()


def test_crash_calibrate_cli(tmp_path: Path) -> None:
    db_path = _seed_calibrate_db(tmp_path)
    result = runner.invoke(
        app, ["--db", str(db_path), "crash-scan", "--calibrate"]
    )
    assert result.exit_code == 0
    assert "crash-calibrate: 3 NORMAL-mode done job(s) sampled" in result.stdout
    assert "suggested thresholds" in result.stdout
