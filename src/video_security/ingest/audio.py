from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from video_security.config import AudioConfig, Config
from video_security.ingest.sound import SoundEvent, classify_sounds


class AudioError(Exception):
    pass


@dataclass
class TranscriptSegment:
    start_time: float
    end_time: float
    text: str
    language: str | None


@dataclass
class AudioResult:
    speech_regions: list[tuple[float, float]]
    segments: list[TranscriptSegment]
    loud_regions: list[tuple[float, float]]
    keyword_hits: list[tuple[float, float, str]]
    sound_events: list[SoundEvent] = field(default_factory=list)


def extract_pcm(path: Path, sample_rate: int = 16000) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        tail = stderr[-500:] if stderr else ""
        raise AudioError(f"ffmpeg failed (exit {result.returncode}): {tail}")
    raw = result.stdout
    if len(raw) == 0:
        raise AudioError("no audio stream")
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


def rms_loud_regions(
    pcm: np.ndarray,
    sample_rate: int,
    config: AudioConfig,
) -> list[tuple[float, float]]:
    window = int(sample_rate * config.rms_window_ms / 1000)
    n_windows = len(pcm) // window
    if n_windows == 0:
        return []
    truncated = pcm[: n_windows * window]
    frames = truncated.reshape(n_windows, window)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    baseline = float(np.percentile(rms, 20))
    threshold = max(baseline * config.rms_factor, config.rms_floor)
    above = rms >= threshold
    regions: list[tuple[float, float]] = []
    i = 0
    while i < len(above):
        if not above[i]:
            i += 1
            continue
        j = i
        while j < len(above) and above[j]:
            j += 1
        start_sec = i * window / sample_rate
        end_sec = j * window / sample_rate
        if end_sec - start_sec >= config.rms_sustain_ms / 1000:
            regions.append((start_sec, end_sec))
        i = j
    return regions


_vad_model: Any | None = None


def speech_regions(
    pcm: np.ndarray,
    sample_rate: int,
    config: AudioConfig,
    vad_model: Any | None = None,
) -> list[tuple[float, float]]:
    from silero_vad import get_speech_timestamps, load_silero_vad
    global _vad_model
    if vad_model is not None:
        model = vad_model
    else:
        if _vad_model is None:
            _vad_model = load_silero_vad()
        model = _vad_model
    timestamps = get_speech_timestamps(pcm, model, sampling_rate=sample_rate)
    total_dur = len(pcm) / sample_rate
    raw: list[tuple[float, float]] = []
    for ts in timestamps:
        start = max(0.0, ts["start"] / sample_rate - config.vad_pad_ms / 1000.0)
        end = min(total_dur, ts["end"] / sample_rate + config.vad_pad_ms / 1000.0)
        raw.append((start, end))
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    merged: list[tuple[float, float]] = [raw[0]]
    for cur_start, cur_end in raw[1:]:
        last_start, last_end = merged[-1]
        if cur_start <= last_end:
            merged[-1] = (last_start, max(last_end, cur_end))
        else:
            merged.append((cur_start, cur_end))
    return merged


def transcribe_regions(
    pcm: np.ndarray,
    sample_rate: int,
    regions: list[tuple[float, float]],
    config: Config,
    transcriber: Callable[[np.ndarray], tuple[list[dict[str, Any]], str]] | None = None,
) -> list[TranscriptSegment]:
    if transcriber is not None:
        fn = transcriber
    else:
        import mlx_whisper
        repo = {
            "small": "mlx-community/whisper-small",
            "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        }.get(config.whisper.model, config.whisper.model)

        def default_transcriber(audio: np.ndarray) -> tuple[list[dict[str, Any]], str]:
            result = mlx_whisper.transcribe(
                audio=audio,
                path_or_hf_repo=repo,
                language=config.whisper.language,
                condition_on_previous_text=False,
            )
            return result["segments"], result["language"]

        fn = default_transcriber

    segments: list[TranscriptSegment] = []
    for region_start, region_end in regions:
        if region_end - region_start < 0.2:
            continue
        start_idx = int(region_start * sample_rate)
        end_idx = int(region_end * sample_rate)
        region_pcm = pcm[start_idx:end_idx]
        seg_dicts, language = fn(region_pcm)
        for seg in seg_dicts:
            segments.append(
                TranscriptSegment(
                    start_time=region_start + seg["start"],
                    end_time=region_start + seg["end"],
                    text=seg["text"].strip(),
                    language=language,
                )
            )
    return segments


def distress_keyword_hits(
    segments: list[TranscriptSegment],
    config: AudioConfig,
) -> list[tuple[float, float, str]]:
    seen: set[tuple[float, float, str]] = set()
    hits: list[tuple[float, float, str]] = []
    for seg in segments:
        lower_text = seg.text.lower()
        for keyword in config.distress_keywords:
            if keyword.lower() in lower_text:
                entry = (seg.start_time, seg.end_time, keyword)
                if entry not in seen:
                    seen.add(entry)
                    hits.append(entry)
    return hits


def analyze_audio(path: Path, config: Config) -> AudioResult:
    pcm = extract_pcm(path, sample_rate=16000)
    sr = 16000
    sp_regions = speech_regions(pcm, sr, config.audio)
    segs = transcribe_regions(pcm, sr, sp_regions, config)
    l_regions = rms_loud_regions(pcm, sr, config.audio)
    hits = distress_keyword_hits(segs, config.audio)
    sevents = classify_sounds(path, config)
    return AudioResult(
        speech_regions=sp_regions,
        segments=segs,
        loud_regions=l_regions,
        keyword_hits=hits,
        sound_events=sevents,
    )