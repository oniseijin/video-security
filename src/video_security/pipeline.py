from __future__ import annotations

import json
import sqlite3
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from video_security import db
from video_security.config import CameraOverrides, Config
from video_security.db import JobRow
from video_security.fs import spotlight_ignore
from video_security.identity import register_face
from video_security.ingest.audio import AudioResult, analyze_audio
from video_security.ingest.frames import FrameData, iter_frames, probe_video
from video_security.llm.detail import detail_events
from video_security.llm.ollama import OllamaClient
from video_security.llm.triage import LLMEvent, TriageResult, triage_events
from video_security.prefilter.faces import detect_faces
from video_security.prefilter.ocr import upscale_crop
from video_security.prefilter.plates import PlateRead, extract_plate_reads
from video_security.prefilter.scenetext import SceneText, sample_scene_text
from video_security.prefilter.threats import detect_threat_events
from video_security.prefilter.vehicles import (
    VEHICLE_CLASSES,
    Detection,
    FrameDetections,
    VehicleTrack,
    accumulate_tracks,
    crop_vehicle,
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
    if path.parent.name.lower() == "rear":
        front_dir = path.parent.parent / "front"
        for suffix in (".NMEA", ".nmea"):
            cand = front_dir / (path.stem + suffix)
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
) -> tuple[
    list[FrameDetections],
    dict[int, FrameData],
    OrderedDict[int, bytes],
    OrderedDict[int, bytes],
]:
    from video_security.engine import check_disk_watermark_once

    frame_dets: list[FrameDetections] = []
    frames_meta: dict[int, FrameData] = {}
    jpeg_cache: OrderedDict[int, bytes] = OrderedDict()
    raw_jpeg_cache: OrderedDict[int, bytes] = OrderedDict()
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
            combined = len(jpeg_cache) + len(raw_jpeg_cache)
            while combined > JPEG_CACHE_LIMIT:
                key, _value = next(iter(jpeg_cache.items()))
                jpeg_cache.pop(key, None)
                raw_jpeg_cache.pop(key, None)
                combined = len(jpeg_cache) + len(raw_jpeg_cache)
            if frame.raw_image is not None:
                raw_jpeg_cache[frame.frame_number] = _encode_jpeg(frame.raw_image)
        frame.image = tiny
        frame.raw_image = None
        frames_meta[frame.frame_number] = frame
        if progress is not None and frame.frame_number % 100 == 0:
            progress(frame.frame_number)
        if frame.frame_number % 1000 == 0:
            check_disk_watermark_once()
    return frame_dets, frames_meta, jpeg_cache, raw_jpeg_cache


def _write_face_crop(
    img: np.ndarray,
    box: list[float],
    faces_dir: Path,
    event_id: int,
    keyframe_index: int,
    face_index: int,
) -> np.ndarray | None:
    h, w = img.shape[:2]
    bx, by, bw, bh = box[0], box[1], box[2], box[3]
    x1 = max(0.0, (bx - 0.15 * bw) * w)
    y1 = max(0.0, (by - 0.15 * bh) * h)
    x2 = min(float(w), (bx + bw * 1.15) * w)
    y2 = min(float(h), (by + bh * 1.15) * h)
    crop = upscale_crop(img[int(y1) : int(y2), int(x1) : int(x2)])
    if crop.size == 0:
        return None
    out = faces_dir / f"face_{event_id}_{keyframe_index}_{face_index}.jpg"
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        out.write_bytes(buf.tobytes())
        return crop
    return None


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


def _choose_keyframe_numbers(
    frame_numbers: list[int],
    jpeg_cache: OrderedDict[int, bytes],
    max_keyframes: int = 3,
    conf_by_frame: dict[int, float] | None = None,
    quality_cache: dict[int, float] | None = None,
) -> list[int]:
    available = [fn for fn in frame_numbers if fn in jpeg_cache]
    if not available:
        return []
    if len(available) <= max_keyframes:
        return available
    if quality_cache is None:
        quality_cache = {}
    conf_by_frame = conf_by_frame or {}

    def score(fn: int) -> float:
        if fn in quality_cache:
            return quality_cache[fn]
        img = cv2.imdecode(
            np.frombuffer(jpeg_cache[fn], dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if img is None:
            quality_cache[fn] = -1.0
            return -1.0
        from video_security.prefilter.ocr import sharpness_luma

        sharp, luma = sharpness_luma(img)
        luma_factor = 1.0 if 40.0 <= luma <= 220.0 else 0.5
        val = sharp * luma_factor * (1.0 + conf_by_frame.get(fn, 0.0))
        quality_cache[fn] = val
        return val

    top = sorted(available, key=score, reverse=True)[:max_keyframes]
    return sorted(top)


def _select_keyframes(
    frame_numbers: list[int],
    jpeg_cache: OrderedDict[int, bytes],
    artifact_dir: Path,
    job_id: int,
    event_id: int,
    max_keyframes: int = 3,
    raw_jpeg_cache: OrderedDict[int, bytes] | None = None,
    conf_by_frame: dict[int, float] | None = None,
    quality_cache: dict[int, float] | None = None,
) -> list[str]:
    chosen = _choose_keyframe_numbers(
        frame_numbers,
        jpeg_cache,
        max_keyframes,
        conf_by_frame=conf_by_frame,
        quality_cache=quality_cache,
    )
    if not chosen:
        return []
    out_dir = artifact_dir / "frames" / str(job_id)
    spotlight_ignore(out_dir)
    rel_paths: list[str] = []
    for i, fn in enumerate(chosen):
        p = out_dir / f"event_{event_id}_{i}.jpg"
        p.write_bytes(jpeg_cache[fn])
        rel_paths.append(str(p))
        if raw_jpeg_cache is not None and fn in raw_jpeg_cache:
            raw_p = out_dir / f"event_{event_id}_{i}_raw.jpg"
            raw_p.write_bytes(raw_jpeg_cache[fn])
    return rel_paths


def analyze_video(
    job: JobRow,
    config: Config,
    conn: sqlite3.Connection,
    camera: CameraOverrides | None = None,
    client: OllamaClient | None = None,
    no_llm: bool = False,
    max_llm_events: int | None = None,
) -> AnalyzeReport:
    report = harvest_job(job, config, conn, camera)
    if no_llm or report.events == 0:
        return report
    if client is None:
        client = OllamaClient()
    if max_llm_events is not None:
        config.engine.max_llm_events = max_llm_events
    report.triaged = triage_job(job, config, conn, client)
    report.detailed = detail_job(job, config, conn, client)
    return report


def _build_evidence(
    *,
    frames_kept: int,
    tracks: list[VehicleTrack],
    plate_reads: dict[int, PlateRead],
    faces_total: int,
    scene_texts: list[SceneText],
    audio_result: AudioResult | None,
    gps_samples: int,
    event_specs: list[EventSpec],
) -> dict[str, object]:
    return {
        "frames_kept": frames_kept,
        "vehicle_tracks": [
            {
                "track_id": t.track_id,
                "direction": t.direction,
                "weaving_score": t.weaving_score,
            }
            for t in tracks
        ][:50],
        "plates": [
            {
                "track_id": track_id,
                "norm": read.norm_text,
                "confidence": read.confidence,
            }
            for track_id, read in plate_reads.items()
        ],
        "faces": faces_total,
        "scene_texts": [
            {"text": s.text, "kind": s.text_kind} for s in scene_texts
        ][:20],
        "audio": {
            "segments": len(audio_result.segments) if audio_result else 0,
            "loud_regions": len(audio_result.loud_regions) if audio_result else 0,
            "keyword_hits": len(audio_result.keyword_hits) if audio_result else 0,
        },
        "gps_samples": gps_samples,
        "event_counts": {
            k: v for k, v in Counter(s.event_type for s in event_specs).items()
        },
    }


def evidence_summary_text(evidence: dict[str, Any] | None) -> str:
    if not evidence:
        return ""
    parts: list[str] = []
    plates = evidence.get("plates") or []
    if plates:
        parts.append(
            "plates: "
            + ", ".join(
                f"{p['norm']} (conf {p['confidence']:.2f})" for p in plates
            )
        )
    tracks = evidence.get("vehicle_tracks") or []
    if tracks:
        dirs = Counter(str(t.get("direction") or "?") for t in tracks)
        parts.append(
            f"vehicle tracks: {len(tracks)} ("
            + ", ".join(f"{n} {d}" for d, n in dirs.items())
            + ")"
        )
    faces = evidence.get("faces") or 0
    if faces:
        parts.append(f"faces detected: {faces}")
    texts = evidence.get("scene_texts") or []
    if texts:
        parts.append(
            "scene text: " + ", ".join(str(t["text"]) for t in texts[:5])
        )
    audio = evidence.get("audio") or {}
    if audio.get("segments"):
        parts.append(
            f"audio: {audio['segments']} transcript segments, "
            f"{audio.get('loud_regions', 0)} loud regions"
        )
    ec = evidence.get("event_counts") or {}
    if ec:
        parts.append(
            "detector events: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(ec.items()))
        )
    device_ctx = evidence.get("device_context")
    if device_ctx:
        parts.append(str(device_ctx))
    summary = "; ".join(parts)
    return summary[:1200]


def harvest_job(
    job: JobRow,
    config: Config,
    conn: sqlite3.Connection,
    camera: CameraOverrides | None = None,
) -> AnalyzeReport:
    path = Path(job.video_path)
    if not path.exists():
        raise PipelineError(f"video missing: {path}")
    camera = camera or CameraOverrides()
    artifact_dir = Path(config.storage.artifact_dir).expanduser()
    report = AnalyzeReport(job_id=job.id)

    db.delete_job_rows(conn, job.id, config.storage.artifact_dir)
    conn.execute(
        "UPDATE jobs SET current_frame = 0, current_stage = 'pending', "
        "evidence_json = NULL WHERE id = ?",
        (job.id,),
    )
    conn.commit()
    db.update_job_status(conn, job.id, "extracting")

    detector = load_detector(config, camera)
    frame_dets, frames_meta, jpeg_cache, raw_jpeg_cache = _decode_pass(
        job, path, config, camera, detector
    )
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
            conn,
            job.id,
            t.track_id,
            0,
            t.first_frame,
            t.last_frame,
            t.weaving_score,
            t.direction,
            t.class_id,
        )

    def images_lookup(fn: int) -> np.ndarray | None:
        raw = jpeg_cache.get(fn)
        if raw is None:
            return None
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img

    plate_reads = _collect_plate_reads(frame_dets, jpeg_cache, images_lookup, config)
    report.plates = len(plate_reads)

    conf_by_frame: dict[int, float] = {}
    for fd in frame_dets:
        if fd.detections:
            conf_by_frame[fd.frame_number] = max(d.conf for d in fd.detections)
    quality_cache: dict[int, float] = {}

    plates_dir = artifact_dir / "plates" / str(job.id)
    spotlight_ignore(plates_dir)
    for track_id, read in plate_reads.items():
        crop_path: str | None = None
        if read.best_bbox is not None:
            try:
                raw_jpeg = jpeg_cache.get(read.best_frame)
                if raw_jpeg is not None:
                    img = cv2.imdecode(
                        np.frombuffer(raw_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if img is not None:
                        track_bbox = next(
                            (
                                d.bbox
                                for fd in frame_dets
                                if fd.frame_number == read.best_frame
                                for d in fd.detections
                                if d.track_id == track_id
                            ),
                            None,
                        )
                        if track_bbox is not None:
                            vehicle_crop = upscale_crop(crop_vehicle(img, track_bbox))
                            vh, vw = vehicle_crop.shape[:2]
                            bx, by, bw, bh = read.best_bbox
                            top = 1.0 - by - bh
                            px1 = max(0.0, (bx - 0.15 * bw) * vw)
                            py1 = max(0.0, (top - 0.15 * bh) * vh)
                            px2 = min(float(vw), (bx + bw * 1.15) * vw)
                            py2 = min(float(vh), (top + bh * 1.15) * vh)
                            plate_crop = vehicle_crop[
                                int(py1) : int(py2), int(px1) : int(px2)
                            ]
                            if plate_crop.size > 0:
                                crop_out = plates_dir / f"track_{track_id}.jpg"
                                ok, _buf = cv2.imencode(
                                    ".jpg", plate_crop, [cv2.IMWRITE_JPEG_QUALITY, 85]
                                )
                                if ok:
                                    crop_out.write_bytes(_buf.tobytes())
                                    crop_path = str(crop_out)
            except Exception:
                crop_path = None

        votes_payload: dict[str, object] = {"votes": read.votes}
        if read.best_bbox is not None:
            votes_payload["best"] = {
                "frame": read.best_frame,
                "bbox": list(read.best_bbox),
            }
        db.insert_plate(
            conn,
            job.id,
            track_id,
            0,
            read.raw_text,
            read.norm_text,
            read.confidence,
            read.best_frame,
            json.dumps(votes_payload),
            crop_path=crop_path,
        )

    frames_dir = artifact_dir / "frames" / str(job.id)
    for t in tracks:
        strip_paths: list[str] = []
        num_frames = t.last_frame - t.first_frame + 1
        if num_frames > 0:
            sample_count = min(5, num_frames)
            for i in range(sample_count):
                fn = t.first_frame + int(
                    i * (num_frames - 1) / max(1, sample_count - 1)
                )
                jpeg_bytes = jpeg_cache.get(fn)
                if jpeg_bytes is not None:
                    spotlight_ignore(frames_dir)
                    strip_out = frames_dir / f"track_{t.track_id}_{i}.jpg"
                    strip_out.write_bytes(jpeg_bytes)
                    strip_paths.append(str(strip_out))
        strip_json = json.dumps(strip_paths) if strip_paths else None
        db.update_vehicle_track_strip(conn, job.id, t.track_id, strip_json)

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
        for se in audio_result.sound_events:
            event_specs.append(
                EventSpec(
                    f"audio_{se.label}",
                    se.start_time,
                    se.end_time,
                    None,
                    se.confidence,
                    0.6 if se.confidence >= 0.7 else 0.4,
                    [],
                )
            )
    for track_id, read in plate_reads.items():
        best_ts = read.best_frame / max(1.0, info.fps)
        event_specs.append(
            EventSpec(
                "plate_capture", best_ts, best_ts + 1.0, track_id,
                read.confidence, 0.6, [read.best_frame],
            )
        )

    gps_samples = 0
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
        gps_samples = len(samples)
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

    faces_total = 0
    faces_dir = artifact_dir / "faces" / str(job.id)
    for spec, event_id in zip(event_specs, event_ids, strict=True):
        kf_paths = _select_keyframes(
            spec.frame_numbers,
            jpeg_cache,
            artifact_dir,
            job.id,
            event_id,
            raw_jpeg_cache=raw_jpeg_cache,
            conf_by_frame=conf_by_frame,
            quality_cache=quality_cache,
        )
        if kf_paths:
            db.update_event_keyframes(conn, event_id, json.dumps(kf_paths))
        chosen = _choose_keyframe_numbers(
            spec.frame_numbers,
            jpeg_cache,
            conf_by_frame=conf_by_frame,
            quality_cache=quality_cache,
        )
        faces_per_kf: list[list[list[float]]] = []
        for i, fn in enumerate(chosen):
            raw = jpeg_cache[fn]
            img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                boxes = [list(fb) for fb in detect_faces(img)]
                if boxes:
                    faces_dir.mkdir(parents=True, exist_ok=True)
                    spotlight_ignore(faces_dir)
                    for j, box in enumerate(boxes):
                        crop = _write_face_crop(img, box, faces_dir, event_id, i, j)
                        if crop is not None:
                            register_face(
                                conn,
                                config,
                                job.id,
                                event_id,
                                i,
                                j,
                                str(
                                    faces_dir
                                    / f"face_{event_id}_{i}_{j}.jpg"
                                ),
                                crop,
                            )
                    faces_total += len(boxes)
            else:
                boxes = []
            faces_per_kf.append(boxes)
        db.update_event_faces(conn, event_id, json.dumps(faces_per_kf))

    if not event_specs:
        db.update_job_status(conn, job.id, "done")
        return report

    evidence = _build_evidence(
        frames_kept=report.kept_frames,
        tracks=tracks,
        plate_reads=plate_reads,
        faces_total=faces_total,
        scene_texts=scene_texts,
        audio_result=audio_result,
        gps_samples=gps_samples,
        event_specs=event_specs,
    )
    meta_row = conn.execute(
        "SELECT metadata_json FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    if meta_row and meta_row["metadata_json"]:
        try:
            meta = json.loads(meta_row["metadata_json"])
            device_kind = meta.get("device_kind")
            if device_kind:
                hints = {
                    "dashcam": "vehicle-mounted dashcam footage (front/rear channel)",
                    "meta_glasses": "first-person smart-glasses bodycam footage",
                    "iphone": "handheld phone footage",
                }
                hint = hints.get(device_kind)
                if hint:
                    evidence["device_context"] = hint
        except (json.JSONDecodeError, TypeError):
            pass
    db.update_job_evidence(conn, job.id, json.dumps(evidence))
    from video_security.watchlist import evaluate_job

    evaluate_job(conn, config, job.id)
    db.update_job_status(conn, job.id, "harvested")
    return report


def load_llm_events(
    conn: sqlite3.Connection, job_id: int, status: str | None = None
) -> list[LLMEvent]:
    summary = evidence_summary_text(db.get_job_evidence(conn, job_id))
    events = db.get_events_for_job(conn, job_id, status)
    out: list[LLMEvent] = []
    for evt in events:
        kf_paths = json.loads(evt["keyframes_json"] or "[]")
        keyframes = []
        for p in kf_paths:
            img = cv2.imread(p)
            if img is not None:
                keyframes.append(img)
        segs = conn.execute(
            "SELECT start_time, end_time, text FROM transcript_segments "
            "WHERE job_id = ? AND end_time >= ? AND start_time <= ?",
            (
                job_id,
                evt["start_sec"] - TRANSCRIPT_WINDOW_SEC,
                evt["end_sec"] + TRANSCRIPT_WINDOW_SEC,
            ),
        ).fetchall()
        window = "\n".join(f"[{s['start_time']:.0f}s] {s['text']}" for s in segs)
        out.append(
            LLMEvent(
                id=evt["id"],
                event_type=evt["event_type"],
                start_sec=evt["start_sec"],
                end_sec=evt["end_sec"],
                detector_score=evt["detector_score"],
                priority=evt["priority"],
                keyframes=keyframes,
                transcript_window=window,
                evidence_summary=summary,
            )
        )
    return out


def triage_job(
    job: JobRow,
    config: Config,
    conn: sqlite3.Connection,
    client: OllamaClient,
) -> int:
    llm_events = load_llm_events(conn, job.id, status="pending")
    if not llm_events:
        db.update_job_status(conn, job.id, "triaged")
        return 0
    db.update_job_status(conn, job.id, "triage")
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
        status = "triaged" if tr.relevant else "suppressed"
        db.update_event_status(conn, tr.event_id, status, result_id)
    db.update_job_status(conn, job.id, "triaged")
    return sum(1 for t in triaged if t.relevant)


def detail_job(
    job: JobRow,
    config: Config,
    conn: sqlite3.Connection,
    client: OllamaClient,
) -> int:
    llm_events = load_llm_events(conn, job.id, status="triaged")
    if not llm_events:
        db.update_job_status(conn, job.id, "done")
        return 0
    db.update_job_status(conn, job.id, "detail")
    triaged = [
        TriageResult(
            event_id=e.id,
            relevant=True,
            event_type=e.event_type,
            description="",
            confidence="",
            raw_response="",
            model_digest="",
            prompt_version="",
        )
        for e in llm_events
    ]
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
    db.update_job_status(conn, job.id, "done")
    return len(detailed)


def enqueue_video(conn: sqlite3.Connection, video_path: Path) -> tuple[JobRow, bool]:
    from video_security.engine import video_hash

    h = video_hash(video_path)
    existing = db.get_job_by_hash(conn, h)
    if existing is not None:
        return existing, False
    job = db.create_job(conn, str(video_path), h)
    return job, True
