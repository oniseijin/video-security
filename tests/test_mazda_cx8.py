from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from video_security.adapters.mazda_cx8 import (
    GpsSample,
    gforce_events,
    parse_filename,
    parse_nmea,
)
from video_security.config import Config, GsensConfig


def _checksum(line: str) -> str:
    body = line[1:]
    v = 0
    for ch in body:
        v ^= ord(ch)
    return f"{v:02X}"


def _nmea_line(body: str) -> str:
    return f"${body}*{_checksum('$' + body)}"


def test_parse_filename() -> None:
    dt = parse_filename("250807121252.MP4")
    assert dt == datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)


def test_parse_filename_invalid() -> None:
    assert parse_filename("250923152213-flipped.MP4") is None
    assert parse_filename("readme.txt") is None
    assert parse_filename("991231235960.MP4") is None


def test_gprmc_parse_and_relative_time(tmp_path: Path) -> None:
    start = datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)
    lines = [
        _nmea_line("GPRMC,031240.00,A,3539.28502,N,14001.73333,E,4.3,41.0,070825,,,A"),
        _nmea_line("GSENS,-0.041,-0.111,-0.913"),
        _nmea_line("GPRMC,031241.00,A,3539.28665,N,14001.73333,E,3.2,41.0,070825,,,A"),
    ]
    p = tmp_path / "x.NMEA"
    p.write_text("\n".join(lines) + "\n")
    samples = parse_nmea(p, start)
    s0 = samples[0]
    assert s0.time_sec == -12.0
    assert s0.lat is not None and s0.lon is not None and s0.speed_kmh is not None
    assert abs(s0.lat - (35 + 39.28502 / 60)) < 1e-6
    assert abs(s0.lon - (140 + 1.73333 / 60)) < 1e-6
    assert abs(s0.speed_kmh - 4.3 * 1.852) < 1e-6
    assert s0.bearing == 41.0
    assert -12.0 < samples[1].time_sec < -11.0
    assert samples[1].ax == -0.041
    assert samples[2].time_sec == -11.0


def test_parse_nmea_bad_checksum_skipped(tmp_path: Path) -> None:
    start = datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)
    lines = [
        _nmea_line("GPRMC,031240.00,A,3539.28502,N,14001.73333,E,4.3,41.0,070825,,,A"),
        "$GSENS,9.0,9.0,9.0*00",
    ]
    p = tmp_path / "x.NMEA"
    p.write_text("\n".join(lines) + "\n")
    samples = parse_nmea(p, start)
    assert all(s.ax != 9.0 for s in samples)


def test_parse_nmea_invalid_status_skipped(tmp_path: Path) -> None:
    start = datetime(2025, 8, 7, 3, 12, 52, tzinfo=UTC)
    lines = [
        _nmea_line("GPRMC,031240.00,V,3539.28502,N,14001.73333,E,4.3,41.0,070825,,,A"),
    ]
    p = tmp_path / "x.NMEA"
    p.write_text("\n".join(lines) + "\n")
    assert parse_nmea(p, start) == []


def test_parse_nmea_no_start_uses_first_fix_as_zero(tmp_path: Path) -> None:
    lines = [
        _nmea_line("GPRMC,031240.00,A,3539.28502,N,14001.73333,E,4.3,41.0,070825,,,A"),
        _nmea_line("GPRMC,031242.00,A,3539.28665,N,14001.73333,E,3.2,41.0,070825,,,A"),
    ]
    p = tmp_path / "x.NMEA"
    p.write_text("\n".join(lines) + "\n")
    samples = parse_nmea(p, None)
    assert samples[0].time_sec == 0.0
    assert samples[1].time_sec == 2.0


def test_gforce_events_hard_brake() -> None:
    samples: list[GpsSample] = []
    for i in range(20):
        ay = -0.5 if 8 <= i <= 10 else -0.05
        samples.append(
            GpsSample(time_sec=float(i), ax=0.02, ay=ay, az=-1.0)
        )
    events = gforce_events(samples, GsensConfig())
    brakes = [e for e in events if e.event_type == "hard_brake"]
    assert len(brakes) == 1
    assert brakes[0].start_sec == 8.0
    assert brakes[0].end_sec == 10.0
    assert abs(brakes[0].peak_g - 0.45) < 1e-9


def test_gforce_events_baseline_median(tmp_path: Path) -> None:
    samples = [
        GpsSample(time_sec=float(i), ax=0.01, ay=-0.02, az=-1.05)
        for i in range(15)
    ]
    samples[7] = GpsSample(time_sec=7.0, ax=0.01, ay=-0.02, az=-1.95)
    events = gforce_events(samples, GsensConfig())
    impacts = [e for e in events if e.event_type == "impact"]
    assert len(impacts) == 1
    assert impacts[0].start_sec == 7.0


def test_gforce_events_too_few_samples() -> None:
    samples = [GpsSample(time_sec=float(i), ax=5.0, ay=-5.0, az=5.0) for i in range(5)]
    assert gforce_events(samples, GsensConfig()) == []


def test_gforce_events_merge_gap() -> None:
    samples = []
    for i in range(30):
        ay = -0.4 if i in (5, 6, 20) else -0.05
        samples.append(GpsSample(time_sec=float(i), ax=0.0, ay=ay, az=-1.0))
    events = gforce_events(samples, GsensConfig())
    brakes = [e for e in events if e.event_type == "hard_brake"]
    assert len(brakes) == 2


def test_discover_clips(tmp_path: Path) -> None:
    config = Config()
    card = tmp_path / "card"
    for mode in ("NORMAL", "EVENT"):
        (card / mode).mkdir(parents=True)
        (card / "REAR" / mode).mkdir(parents=True)
        (card / "System" / "NMEA" / mode).mkdir(parents=True)
    (card / "PICTURE").mkdir()
    (card / "special").mkdir()

    (card / "EVENT" / "250807121252.MP4").write_bytes(b"front-event")
    (card / "REAR" / "EVENT" / "250807121252.MP4").write_bytes(b"rear-event")
    (card / "System" / "NMEA" / "EVENT" / "250807121252.NMEA").write_text("")
    (card / "NORMAL" / "250921170102.MP4").write_bytes(b"front-normal")
    (card / "REAR" / "NORMAL" / "250921170102.MP4").write_bytes(b"rear-normal")
    (card / "PICTURE" / "250807121253.JPG").write_bytes(b"jpg")
    (card / "special" / "250923152213-flipped.MP4").write_bytes(b"flipped")

    from video_security.adapters import get_adapter

    adapter = get_adapter("mazda_cx8", config)
    clips = adapter.discover_clips(card)
    by_key = {(c.mode, c.channel): c for c in clips}
    assert set(by_key) == {
        ("EVENT", "front"),
        ("NORMAL", "front"),
        ("EVENT", "rear"),
        ("NORMAL", "rear"),
    }

    ev = by_key[("EVENT", "front")]
    assert ev.priority == 1.0
    assert ev.recording_start_utc == "2025-08-07T03:12:52+00:00"
    assert ev.pair_path == card / "REAR" / "EVENT" / "250807121252.MP4"
    assert ev.nmea_path == card / "System" / "NMEA" / "EVENT" / "250807121252.NMEA"

    nm = by_key[("NORMAL", "front")]
    assert nm.priority == 0.3
    assert nm.pair_path == card / "REAR" / "NORMAL" / "250921170102.MP4"
    assert nm.nmea_path is None

    rear = by_key[("EVENT", "rear")]
    assert rear.pair_path is None
    assert rear.nmea_path is None


def test_discover_date_wrapped_archive(tmp_path: Path) -> None:
    config = Config()
    card = tmp_path / "archive" / "20250923"
    (card / "EVENT").mkdir(parents=True)
    (card / "REAR" / "EVENT").mkdir(parents=True)
    (card / "System" / "NMEA" / "EVENT").mkdir(parents=True)
    (card / "EVENT" / "250815202645.MP4").write_bytes(b"x")
    (card / "System" / "NMEA" / "EVENT" / "250815202645.NMEA").write_text("")

    from video_security.adapters import get_adapter

    adapter = get_adapter("mazda_cx8", config)
    clips = adapter.discover_clips(tmp_path / "archive")
    assert len(clips) == 1
    assert clips[0].nmea_path is not None


def test_detect_adapter(tmp_path: Path) -> None:
    from video_security.adapters import detect_adapter

    card = tmp_path / "card"
    (card / "EVENT").mkdir(parents=True)
    assert detect_adapter(card) == "mazda_cx8"
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "video.mp4").write_bytes(b"x")
    assert detect_adapter(plain) == "generic"


def test_generic_adapter(tmp_path: Path) -> None:
    from video_security.adapters import get_adapter

    src = tmp_path / "src"
    src.mkdir()
    (src / "GX010001.MP4").write_bytes(b"one")
    (src / "sub").mkdir()
    (src / "sub" / "clip2.mov").write_bytes(b"two")
    adapter = get_adapter("generic", Config())
    clips = adapter.discover_clips(src)
    assert len(clips) == 2
    assert all(c.channel == "front" for c in clips)
    assert all(c.recording_start_utc is not None for c in clips)

    gopro = get_adapter("gopro", Config())
    assert gopro.discover_clips(src) == clips or len(gopro.discover_clips(src)) == 2
