from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from video_security import db
from video_security.config import CameraOverrides, Config
from video_security.db import JobRow
from video_security.fs import spotlight_ignore
from video_security.ingest.audio import AudioResult, TranscriptSegment, analyze_audio
from video_security.ingest.frames import FrameData, iter_frames, probe_video
from video_security.llm.detail import detail_events
from video_security.llm.ollama import OllamaClient
from video_security.llm.triage import LLMEvent, triage_events
from video_security.prefilter.plates import PlateRead, extract_plate_reads
from video_security.prefilter.scenetext import sample_scene_text
from video_security.prefilter.threats import detect_threat_events
from video_security.prefilter.vehicles import (
    VEHICLE_CLASSES,
    Detection,
    FrameDetections,
    accumulate_tracks,
    load_detector,
)

TRANSCRIPT_WINDOW_SEC = 30.0
JPEG_CACHE_LIMIT = 1000
GFORCE_KEYFRAME_PAD_SEC = 0.5


def _find_nmea_sidecar(path: Path) -> Path | None:
    for suffix in (".NMEA", ".nmea"):
        cand = path.with_suffix(suffix)
        if cand.exists():
            return cand
    return None


def _frames_in_window(
    frames_meta: dict[int, FrameData], start_sec: float, end_sec: float, max_n: int = 3
) -> list[int]:
    in_window = [
        fn
        for fn, meta in frames_meta.items()
        if start_sec - GFORCE_KEYFRAME_PAD_SEC
        <= meta.timestamp_sec
        <= end_sec + GFORCE_KEYFRAME_PAD_SEC
    ]
    in_window.sort()
    if len(in_window) <= max_n:
        return in_window
    step = len(in_window) / max_n
    return [in_window[int(i * step)] for i in range(max_n)]


@dataclass
class AnalyzeReport:
    job_id: int
    kept_frames: int = 0
    vehicle_tracks: int = 0
    plates: int = 0
    transcript_segments: int = 0
    scene_texts: int = 0
    events: int = 0
    triaged: int = 0
    detailed: int = 0
    keyframes: list[Path] = field(default_factory=list)


class PipelineError(Exception):
    pass


@dataclass
class EventSpec:
    event_type: str
    start_sec: float
    end_sec: float
    track_id: int | None
    detector_score: float
    priority: float
    frame_numbers: list[int]


def _encode_jpeg(image: np.ndarray, max_width: int = 1280, quality: int = 80) -> bytes:
    h, w = image.shape[:2]
    if w > max_width:
        image = cv2.resize(
            image, (max_width, int(round(h * max_width / w))), interpolation=cv2.INTER_AREA
        )
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise PipelineError("JPEG encode failed")
    return buf.tobytes()


def _decode_pass(
    job: JobRow,
    path: Path,
    config: Config,
    camera: CameraOverrides | None,
    detector: Callable[[np.ndarray], list[Detection]],
    progress: Callable[[int], None] | None = None,
) -> tuple[list[FrameDetections], dict[int, FrameData], OrderedDict[int, bytes]]:
    from video_security.engine import check_disk_watermark_once

    frame_dets: list[FrameDetections] = []
    frames_meta: dict[int, FrameData] = {}
    jpeg_cache: OrderedDict[int, bytes] = OrderedDict()
    tiny = np.zeros((1, 1, 3), dtype=np.uint8)
    for frame in iter_frames(path, config, camera):
        dets = detector(frame.image)
        frame_dets.append(
            FrameDetections(
                frame_number=frame.frame_number,
                timestamp_sec=frame.timestamp_sec,
                detections=dets,
            )
        )
        if dets:
            jpeg_cache[frame.frame_number] = _encode_jpeg(frame.image)
            while len(jpeg_cache) > JPEG_CACHE_LIMIT:
                jpeg_cache.popitem(last=False)
        frame.image = tiny
        frames_meta[frame.frame_number] = frame
        if progress is not None and frame.frame_number % 100 == 0:
            progress(frame.frame_number)
        if frame.frame_number % 1000 == 0:
            check_disk_watermark_once()
    return frame_dets, frames_meta, jpeg_cache


def _collect_plate_reads(
    frame_dets: list[FrameDetections],
    jpeg_cache: OrderedDict[int, bytes],
    images_lookup: Callable[[int], np.ndarray | None],
    config: Config,
) -> dict[int, PlateRead]:
    track_bboxes: dict[int, dict[int, tuple[int, int, int, int]]] = {}
    for fd in frame_dets:
        for det in fd.detections:
            if det.class_id in VEHICLE_CLASSES:
                track_bboxes.setdefault(det.track_id, {})[fd.frame_number] = det.bbox
    reads: dict[int, PlateRead] = {}
    for track_id, bboxes in track_bboxes.items():
        if len(bboxes) < 2:
            continue
        images: dict[int, np.ndarray] = {}
        for fn in bboxes:
            img = images_lookup(fn)
            if img is not None:
                images[fn] = img
        if not images:
            continue
        read = extract_plate_reads(bboxes, images, track_id, config)
        if read is not None:
            reads[track_id] = read
    return reads


def _select_keyframes(
    frame_numbers: list[int],
    jpeg_cache: OrderedDict[int, bytes],
    artifact_dir: Path,
    job_id: int,
    event_id: int,
    max_keyframes: int = 3,
) -> list[str]:
    available = [fn for fn in frame_numbers if fn in jpeg_cache]
    if not available:
        return []
    if len(available) <= max_keyframes:
        chosen = available
    else:
        step = len(available) / max_keyframes
        chosen = [available[int(i * step)] for i in range(max_keyframes)]
    out_dir = artifact_dir / "frames" / str(job_id)
    spotlight_ignore(out_dir)
    rel_paths: list[str] = []
    for i, fn in enumerate(chosen):
        p = out_dir / f"event_{event_id}_{i}.jpg"
        p.write_bytes(jpeg_cache[fn])
        rel_paths.append(str(p))
    return rel_paths


def _transcript_window(
    segments: list[TranscriptSegment], start_sec: float, end_sec: float
) -> str:
    lines: list[str] = []
    for seg in segments:
        if seg.end_time < start_sec - TRANSCRIPT_WINDOW_SEC:
            continue
        if seg.start_time > end_sec + TRANSCRIPT_WINDOW_SEC:
            continue
        lines.append(f"[{seg.start_time:.0f}s] {seg.text}")
    return "\n".join(lines)


def analyze_video(
    job: JobRow,
    config: Config,
    conn: sqlite3.Connection,
    camera: CameraOverrides | None = None,
    client: OllamaClient | None = None,
    no_llm: bool = False,
    max_llm_events: int | None = None,
) -> AnalyzeReport:
    path = Path(job.video_path)
    if not path.exists():
        raise PipelineError(f"video missing: {path}")
    camera = camera or CameraOverrides()
    artifact_dir = Path(config.storage.artifact_dir).expanduser()
    report = AnalyzeReport(job_id=job.id)

    db.delete_job_rows(conn, job.id)
    conn.execute(
        "UPDATE jobs SET current_frame = 0, current_stage = 'pending' WHERE id = ?",
        (job.id,),
    )
    conn.commit()
    db.update_job_status(conn, job.id, "extracting")

    detector = load_detector(config, camera)
    frame_dets, frames_meta, jpeg_cache = _decode_pass(job, path, config, camera, detector)
    report.kept_frames = len(frames_meta)

    info = probe_video(path)
    audio_result: AudioResult | None = None
    if info.has_audio:
        try:
            audio_result = analyze_audio(path, config)
        except Exception:
            audio_result = None

    db.update_job_status(conn, job.id, "filtering")

    for fn, meta in frames_meta.items():
        db.insert_frame(
            conn, job.id, 0, fn, meta.timestamp_sec, meta.dhash, meta.lighting
        )
    report.kept_frames = len(frames_meta)

    tracks = accumulate_tracks(frame_dets)
    report.vehicle_tracks = len(tracks)
    for t in tracks:
        db.insert_vehicle_track(
            conn, job.id, t.track_id, 0, t.first_frame, t.last_frame, t.weaving_score, t.direction
        )

    def images_lookup(fn: int) -> np.ndarray | None:
        raw = jpeg_cache.get(fn)
        if raw is None:
            return None
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img

    plate_reads = _collect_plate_reads(frame_dets, jpeg_cache, images_lookup, config)
    report.plates = len(plate_reads)
    for track_id, read in plate_reads.items():
        db.insert_plate(
            conn,
            job.id,
            track_id,
            0,
            read.raw_text,
            read.norm_text,
            read.confidence,
            read.best_frame,
            json.dumps({"votes": read.votes}),
        )

    scene_texts = sample_scene_text(
        [f for f in frames_meta.values()], config
    )
    report.scene_texts = len(scene_texts)
    for s in scene_texts:
        db.insert_frame_text(
            conn, job.id, 0, s.frame_number, s.text, s.text_kind, None, s.confidence
        )

    segments = audio_result.segments if audio_result else []
    report.transcript_segments = len(segments)
    for i, seg in enumerate(segments):
        db.insert_transcript_segment(
            conn, job.id, 0, i, seg.start_time, seg.end_time, seg.text, seg.language
        )

    threat_events = detect_threat_events(frame_dets, frames_meta, config, camera)

    event_specs: list[EventSpec] = []
    for te in threat_events:
        event_specs.append(
            EventSpec(
                event_type=te.event_type,
                start_sec=te.start_sec,
                end_sec=te.end_sec,
                track_id=te.track_id,
                detector_score=te.detector_score,
                priority=te.priority,
                frame_numbers=te.frame_numbers,
            )
        )
    if audio_result is not None:
        for start, end in audio_result.loud_regions:
            event_specs.append(
                EventSpec("audio_loud", start, end, None, 0.5, 0.5, [])
            )
        for start, end, _kw in audio_result.keyword_hits:
            event_specs.append(
                EventSpec("audio_distress", start, end, None, 0.7, 0.8, [])
            )
    for track_id, read in plate_reads.items():
        best_ts = read.best_frame / max(1.0, info.fps)
        event_specs.append(
            EventSpec(
                "plate_capture", best_ts, best_ts + 1.0, track_id,
                read.confidence, 0.6, [read.best_frame],
            )
        )

    nmea_sidecar = _find_nmea_sidecar(path)
    if nmea_sidecar is not None:
        from video_security.adapters.mazda_cx8 import (
            GFORCE_EVENT_PRIORITY,
            gforce_events,
            parse_nmea,
        )

        clip_start = None
        if job.recording_start_utc:
            try:
                clip_start = datetime.fromisoformat(job.recording_start_utc)
            except ValueError:
                clip_start = None
        samples = parse_nmea(nmea_sidecar, clip_start)
        for gs in samples:
            db.insert_gps_row(
                conn, job.id, 0, gs.time_sec, gs.lat, gs.lon,
                gs.speed_kmh, gs.bearing, gs.ax, gs.ay, gs.az,
            )
        conn.commit()
        for ge in gforce_events(samples, config.adapter_mazda_cx8.gsens):
            event_specs.append(
                EventSpec(
                    ge.event_type,
                    ge.start_sec,
                    ge.end_sec,
                    None,
                    ge.peak_g,
                    GFORCE_EVENT_PRIORITY.get(ge.event_type, 0.7),
                    _frames_in_window(frames_meta, ge.start_sec, ge.end_sec),
                )
            )

    event_ids: list[int] = []
    for spec in event_specs:
        event_id = db.insert_event(
            conn,
            job.id,
            spec.event_type,
            spec.start_sec,
            spec.end_sec,
            0,
            spec.track_id,
            "[]",
            spec.detector_score,
            spec.priority,
        )
        event_ids.append(event_id)
    report.events = len(event_ids)

    if no_llm or not event_specs:
        db.update_job_status(conn, job.id, "done")
        return report

    if client is None:
        client = OllamaClient()
    if max_llm_events is not None:
        config.engine.max_llm_events = max_llm_events

    db.update_job_status(conn, job.id, "triage")
    llm_events: list[LLMEvent] = []
    for spec, event_id in zip(event_specs, event_ids, strict=True):
        kf_paths = _select_keyframes(
            spec.frame_numbers, jpeg_cache, artifact_dir, job.id, event_id
        )
        if kf_paths:
            db.update_event_keyframes(conn, event_id, json.dumps(kf_paths))
        keyframes = []
        for p in kf_paths:
            img = cv2.imread(p)
            if img is not None:
                keyframes.append(img)
        window = _transcript_window(segments, spec.start_sec, spec.end_sec)
        llm_events.append(
            LLMEvent(
                id=event_id,
                event_type=spec.event_type,
                start_sec=spec.start_sec,
                end_sec=spec.end_sec,
                detector_score=spec.detector_score,
                priority=spec.priority,
                keyframes=keyframes,
                transcript_window=window,
            )
        )
        report.keyframes.extend(Path(p) for p in kf_paths)

    triaged = triage_events(llm_events, config, client)
    for tr in triaged:
        result_id = db.insert_analysis_result(
            conn,
            job.id,
            tr.event_id,
            tr.model_digest,
            tr.prompt_version,
            "triage",
            tr.raw_response,
            None,
            0,
            None,
        )
        if tr.relevant:
            db.update_event_status(conn, tr.event_id, "triaged", result_id)
        else:
            db.update_event_status(conn, tr.event_id, "suppressed", result_id)
    report.triaged = sum(1 for t in triaged if t.relevant)

    db.update_job_status(conn, job.id, "detail")
    detailed = detail_events(triaged, llm_events, config, client)
    for dr in detailed:
        result_id = db.insert_analysis_result(
            conn,
            job.id,
            dr.event_id,
            dr.model_digest,
            dr.prompt_version,
            "detail",
            dr.raw_response,
            None,
            0,
            json.dumps({"tiled": dr.tiled}) if dr.tiled else None,
        )
        db.update_event_status(conn, dr.event_id, "detailed", result_id)
    report.detailed = len(detailed)

    db.update_job_status(conn, job.id, "done")
    return report


def enqueue_video(conn: sqlite3.Connection, video_path: Path) -> tuple[JobRow, bool]:
    from video_security.engine import video_hash

    h = video_hash(video_path)
    existing = db.get_job_by_hash(conn, h)
    if existing is not None:
        return existing, False
    job = db.create_job(conn, str(video_path), h)
    return job, True
