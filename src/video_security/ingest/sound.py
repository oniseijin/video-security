from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_security.config import Config


@dataclass
class SoundEvent:
    start_time: float
    end_time: float
    label: str
    confidence: float


DEFAULT_ALLOWLIST: list[str] = [
    "glass_breaking",
    "alarm_clock",
    "smoke_detector",
    "siren",
    "civil_defense_siren",
    "police_siren",
    "ambulance_siren",
    "fire_engine_siren",
    "car_horn",
    "air_horn",
    "gunshot_gunfire",
    "boom",
    "explosion",
    "fireworks",
    "firecracker",
    "door_slam",
    "screaming",
    "shout",
    "yell",
]


def _snaudio_available() -> bool:
    try:
        from SoundAnalysis import SNClassifySoundRequest  # noqa: F401
        req = SNClassifySoundRequest.alloc().init()
        return req is not None
    except Exception:
        return False


def classify_sounds(path: Path, cfg: Config) -> list[SoundEvent]:
    if not cfg.sound.enabled:
        return []
    allowlist = [a.lower() for a in cfg.sound.allowlist]
    if not allowlist:
        return []

    if not _snaudio_available():
        return []

    try:
        from CoreML import MLModel
        from Foundation import NSURL, NSDate, NSRunLoop
        from SoundAnalysis import SNAudioFileAnalyzer, SNClassifySoundRequest

        model_path = (
            "/System/Library/Frameworks/SoundAnalysis.framework"
            "/Versions/A/Resources/SNSoundClassifierVersion1Model.mlmodelc"
        )
        model_url = NSURL.fileURLWithPath_(model_path)
        model, _ = MLModel.modelWithContentsOfURL_error_(model_url, None)
        if model is None:
            return []

        req, _ = SNClassifySoundRequest.alloc().initWithMLModel_error_(model, None)
        if req is None:
            return []

        audio_url = NSURL.fileURLWithPath_(str(path))
        analyzer, _ = SNAudioFileAnalyzer.alloc().initWithURL_error_(audio_url, None)
        if analyzer is None:
            return []

        results_holder: list[Any] = []

        def cb(result: Any) -> None:
            results_holder.append(result)

        analyzer.analyzeWithCompletionHandler_(cb)
        run_loop = NSRunLoop.currentRunLoop()
        deadline = NSDate.dateWithTimeIntervalSinceNow_(120.0)
        while not results_holder and deadline.timeIntervalSinceNow() > 0:
            run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))

        if not results_holder:
            return []

        snresult = results_holder[0]
        if snresult is None:
            return []

        events: list[SoundEvent] = []
        min_conf = cfg.sound.min_confidence

        for snr in snresult:
            if snr is None:
                continue
            tr = snr.timeRange
            start = tr.start.value
            end = start + tr.duration.value
            for cl in snr.classifications:
                label = str(cl.identifier)
                if label.lower() in allowlist:
                    conf = float(cl.confidence)
                    if conf >= min_conf:
                        events.append(
                            SoundEvent(
                                start_time=float(start),
                                end_time=float(end),
                                label=label,
                                confidence=conf,
                            )
                        )

        return events
    except Exception:
        return []