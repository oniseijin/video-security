from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from video_security.config import AudioConfig, Config
from video_security.ingest.audio import (
    AudioError,
    TranscriptSegment,
    analyze_audio,
    distress_keyword_hits,
    extract_pcm,
    rms_loud_regions,
    speech_regions,
    transcribe_regions,
)


def _make_av(path: Path, audio_src: str, dur: int = 4) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s=160x120:r=5:d={dur}",
            "-f", "lavfi", "-i", audio_src,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        check=True,
    )


def _make_video_only(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=160x120:r=5:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        check=True,
    )


class TestExtractPcm:
    def test_extract_pcm(self, tmp_path: Path) -> None:
        av = tmp_path / "test.mp4"
        _make_av(av, "sine=frequency=440:duration=4")
        pcm = extract_pcm(av)
        assert abs(len(pcm) - 64000) <= 500
        assert pcm.dtype == np.float32
        assert 0.1 < np.abs(pcm).max() < 1.0

    def test_extract_pcm_video_only(self, tmp_path: Path) -> None:
        vo = tmp_path / "video.mp4"
        _make_video_only(vo)
        with pytest.raises(AudioError):
            extract_pcm(vo)


class TestRmsLoudRegions:
    def test_rms_loud_regions(self) -> None:
        sr = 16000
        config = AudioConfig()
        silence = np.zeros(2 * sr, dtype=np.float32)
        tone = 0.5 * np.sin(np.linspace(0, np.pi * 440, sr, dtype=np.float32))
        pcm = np.concatenate([silence, tone, silence])
        regions = rms_loud_regions(pcm, sr, config)
        assert len(regions) == 1
        assert abs(regions[0][0] - 2.0) <= 0.15
        assert abs(regions[0][1] - 3.0) <= 0.15

    def test_rms_loud_regions_pure_silence(self) -> None:
        sr = 16000
        config = AudioConfig()
        pcm = np.zeros(5 * sr, dtype=np.float32)
        assert rms_loud_regions(pcm, sr, config) == []

    def test_rms_loud_regions_sustain(self) -> None:
        sr = 16000
        config = AudioConfig()
        silence1 = np.zeros(2 * sr, dtype=np.float32)
        burst = 0.5 * np.sin(
            np.linspace(0, np.pi * 440, int(0.2 * sr), dtype=np.float32)
        )
        silence2 = np.zeros(2 * sr, dtype=np.float32)
        pcm = np.concatenate([silence1, burst, silence2])
        assert rms_loud_regions(pcm, sr, config) == []

    def test_rms_constant_not_flagged(self) -> None:
        sr = 16000
        config = AudioConfig()
        tone = 0.1 * np.sin(
            np.linspace(0, np.pi * 440 * 4, 4 * sr, dtype=np.float32)
        )
        assert rms_loud_regions(tone, sr, config) == []


class TestSpeechRegions:
    def test_real_vad_silence(self) -> None:
        sr = 16000
        config = AudioConfig()
        pcm = np.zeros(4 * sr, dtype=np.float32)
        assert speech_regions(pcm, sr, config) == []

    def test_speech_regions(self, monkeypatch: Any) -> None:
        fake_vad = MagicMock()
        fake_vad.get_speech_timestamps = MagicMock()
        fake_vad.load_silero_vad = MagicMock()
        monkeypatch.setitem(sys.modules, "silero_vad", fake_vad)
        fake_vad.get_speech_timestamps.return_value = [{"start": 16000, "end": 32000}]
        fake_vad.load_silero_vad.return_value = MagicMock()

        sr = 16000
        config = AudioConfig()
        pcm = np.zeros(4 * sr, dtype=np.float32)
        regions = speech_regions(pcm, sr, config)
        assert len(regions) == 1
        assert abs(regions[0][0] - 0.7) <= 0.01
        assert abs(regions[0][1] - 2.3) <= 0.01

    def test_speech_regions_merge_overlapping(self, monkeypatch: Any) -> None:
        fake_vad = MagicMock()
        fake_vad.get_speech_timestamps = MagicMock()
        fake_vad.load_silero_vad = MagicMock()
        monkeypatch.setitem(sys.modules, "silero_vad", fake_vad)
        fake_vad.get_speech_timestamps.return_value = [
            {"start": 0, "end": 32000},
            {"start": 30000, "end": 48000},
        ]
        fake_vad.load_silero_vad.return_value = MagicMock()

        sr = 16000
        config = AudioConfig()
        pcm = np.zeros(4 * sr, dtype=np.float32)
        regions = speech_regions(pcm, sr, config)
        assert len(regions) == 1
        assert abs(regions[0][0] - 0.0) <= 0.01
        assert abs(regions[0][1] - 3.3) <= 0.01


class TestTranscribeRegionsMock:
    def test_transcribe_regions_mock(self) -> None:
        sr = 16000
        config = Config()

        def mock_transcriber(audio: np.ndarray) -> tuple[list[dict[str, Any]], str]:
            return [{"start": 0.0, "end": 0.5, "text": " hello there "}], "en"

        pcm = np.zeros(6 * sr, dtype=np.float32)
        regions = [(1.0, 2.0), (3.0, 3.5), (5.0, 5.1)]
        segments = transcribe_regions(pcm, sr, regions, config, transcriber=mock_transcriber)
        assert len(segments) == 2
        assert segments[0] == TranscriptSegment(1.0, 1.5, "hello there", "en")
        assert segments[1] == TranscriptSegment(3.0, 3.5, "hello there", "en")


class TestDistressKeywordHits:
    def test_keyword_hits(self) -> None:
        config = AudioConfig()
        segments = [
            TranscriptSegment(0.0, 1.0, "Please HELP me", "en"),
            TranscriptSegment(2.0, 3.0, "call the police", "en"),
            TranscriptSegment(4.0, 5.0, "nice weather", "en"),
        ]
        hits = distress_keyword_hits(segments, config)
        assert len(hits) == 2
        assert (0.0, 1.0, "help") in hits
        assert (2.0, 3.0, "police") in hits

    def test_keyword_hits_get_out_substring(self) -> None:
        config = AudioConfig()
        segments = [TranscriptSegment(0.0, 1.0, "please get out now", "en")]
        hits = distress_keyword_hits(segments, config)
        assert (0.0, 1.0, "get out") in hits

    def test_keyword_hits_no_match(self) -> None:
        config = AudioConfig()
        segments = [TranscriptSegment(0.0, 1.0, "nice weather", "en")]
        assert distress_keyword_hits(segments, config) == []


class TestAnalyzeAudio:
    def test_analyze_audio_orchestration(self, tmp_path: Path, monkeypatch: Any) -> None:
        av = tmp_path / "test_sine.mp4"
        _make_av(av, "sine=frequency=440:duration=4")
        config = Config()
        config.audio.distress_keywords = ["help"]
        fake_speech = [(0.5, 1.5)]
        fake_segments = [TranscriptSegment(0.5, 1.5, "help me", "en")]

        monkeypatch.setattr(
            "video_security.ingest.audio.speech_regions",
            lambda pcm, sr, cfg, vad_model=None: fake_speech,
        )
        monkeypatch.setattr(
            "video_security.ingest.audio.transcribe_regions",
            lambda pcm, sr, regions, cfg, transcriber=None: fake_segments,
        )

        result = analyze_audio(av, config)
        assert result.speech_regions == fake_speech
        assert result.segments == fake_segments
        assert len(result.loud_regions) >= 0
        assert result.keyword_hits == [(0.5, 1.5, "help")]