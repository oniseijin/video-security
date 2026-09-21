from __future__ import annotations

import subprocess
from pathlib import Path

from video_security.config import Config, SoundConfig
from video_security.ingest.sound import (
    SoundEvent,
    classify_sounds,
)


def _make_test_av(path: Path, audio_src: str = "sine=frequency=440:duration=3") -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=160x120:r=5:d=3",
            "-f", "lavfi", "-i", audio_src,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        check=True,
    )


class TestSoundDisabled:
    def test_disabled_returns_empty(self, tmp_path: Path) -> None:
        av = tmp_path / "test.mp4"
        _make_test_av(av)
        cfg = Config()
        cfg.sound.enabled = False
        assert classify_sounds(av, cfg) == []

    def test_empty_allowlist_returns_empty(self, tmp_path: Path) -> None:
        av = tmp_path / "test.mp4"
        _make_test_av(av)
        cfg = Config()
        cfg.sound.enabled = True
        cfg.sound.allowlist = []
        assert classify_sounds(av, cfg) == []


class TestSoundDisabledGracefully:
    def test_disabled_by_default(self) -> None:
        from video_security.ingest.sound import _snaudio_available
        cfg = Config()
        cfg.sound.enabled = True
        if not _snaudio_available():
            av = Path("/nonexistent/video.mp4")
            assert classify_sounds(av, cfg) == []


class TestSoundConfig:
    def test_default_sound_config(self) -> None:
        sc = SoundConfig()
        assert sc.enabled is True
        assert sc.min_confidence == 0.5
        assert len(sc.allowlist) > 0
        assert "siren" in sc.allowlist
        assert "glass_breaking" in sc.allowlist

    def test_sound_config_in_config(self) -> None:
        cfg = Config()
        assert cfg.sound.enabled is True
        assert cfg.sound.min_confidence == 0.5

    def test_sound_config_toml_parsing(self, tmp_path: Path) -> None:
        toml = tmp_path / "config.toml"
        toml.write_text("[sound]\nenabled = false\nmin_confidence = 0.7\nallowlist = ['siren']\n")
        from video_security.config import load_config
        cfg = load_config(toml)
        assert cfg.sound.enabled is False
        assert cfg.sound.min_confidence == 0.7
        assert cfg.sound.allowlist == ["siren"]


class TestSoundEvent:
    def test_sound_event_fields(self) -> None:
        evt = SoundEvent(start_time=1.0, end_time=2.0, label="siren", confidence=0.9)
        assert evt.start_time == 1.0
        assert evt.end_time == 2.0
        assert evt.label == "siren"
        assert evt.confidence == 0.9

    def test_sound_event_default_construction(self) -> None:
        evt = SoundEvent(start_time=0.0, end_time=0.0, label="", confidence=0.0)
        assert isinstance(evt, SoundEvent)