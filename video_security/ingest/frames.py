from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

from video_security.config import CameraOverrides, Config


class IngestError(Exception):
    pass


@dataclasses.dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    total_frames: int
    duration_sec: float
    has_audio: bool


def probe_video(path: Path) -> VideoInfo:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise IngestError(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    video_stream = None
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        if stream.get("codec_type") == "audio":
            has_audio = True
    if video_stream is None:
        raise IngestError("No video stream found")
    width = video_stream["width"]
    height = video_stream["height"]
    rfr = video_stream.get("r_frame_rate", "0/1")
    num, den = rfr.split("/")
    fps = float(num) / float(den) if float(den) != 0 else 0.0
    duration = float(video_stream.get("duration", 0))
    nb_frames_raw = video_stream.get("nb_frames", 0)
    if nb_frames_raw:
        total_frames = int(nb_frames_raw)
    else:
        total_frames = int(round(duration * fps))
    return VideoInfo(
        width=width,
        height=height,
        fps=fps,
        total_frames=total_frames,
        duration_sec=duration,
        has_audio=has_audio,
    )


def dhash_hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclasses.dataclass
class FrameData:
    frame_number: int
    timestamp_sec: float
    image: np.ndarray
    motion_score: float
    mog2_ratio: float
    dhash: int
    lighting: str
    keep_reason: str


def _compute_dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (8, 9), interpolation=cv2.INTER_AREA)
    diff = small[1:, :] > small[:-1, :]
    bits = diff.flatten()
    h = 0
    for bit in bits:
        h = (h << 1) | int(bit)
    return h


def _lighting(hsv: np.ndarray, night_luma: int, ir_max_sat: int) -> str:
    mean_v = float(cv2.mean(hsv[:, :, 2])[0])
    mean_s = float(cv2.mean(hsv[:, :, 1])[0])
    if mean_v < night_luma and mean_s < ir_max_sat:
        return "ir"
    if mean_v < night_luma:
        return "night"
    if mean_v < night_luma * 1.5:
        return "dusk"
    return "day"


def _osd_mask_image(
    image: np.ndarray, rects: list[list[int]], from_w: int, from_h: int
) -> None:
    for r in rects:
        x1, y1, x2, y2 = r
        x1 = max(0, min(x1, from_w - 1))
        y1 = max(0, min(y1, from_h - 1))
        x2 = max(0, min(x2, from_w))
        y2 = max(0, min(y2, from_h))
        if x2 <= x1 or y2 <= y1:
            continue
        image[y1:y2, x1:x2] = 0


def iter_frames(
    video_path: Path, config: Config, camera: CameraOverrides | None = None
) -> Iterator[FrameData]:
    camera = camera or CameraOverrides()
    info = probe_video(video_path)
    tw = config.prefilter.decode_width
    th = int(round(tw * info.height / info.width / 2.0)) * 2
    thr = (
        camera.motion_threshold
        if camera.motion_threshold is not None
        else config.prefilter.motion_threshold
    )
    scale_w = round(tw)
    scale_h = round(th)

    frame_bytes = scale_w * scale_h * 3
    cmd: list[str] = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"scale={scale_w}:-2",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None

    motion_h = 480
    motion_w = int(round(motion_h * scale_w / scale_h / 2.0)) * 2

    osd_mask_rects = camera.osd_mask
    scale_x = motion_w / scale_w
    scale_y = motion_h / scale_h
    scaled_rects: list[list[int]] = []
    for r in osd_mask_rects:
        scaled_rects.append([
            int(r[0] * scale_x),
            int(r[1] * scale_y),
            int(r[2] * scale_x),
            int(r[3] * scale_y),
        ])

    bg_subtractor = cv2.createBackgroundSubtractorMOG2(
        history=300, varThreshold=25, detectShadows=False
    )
    prev_gray: np.ndarray | None = None
    last_kept_dhash: int | None = None
    last_kept_t: float = -999999.0
    frame_idx = 0
    fps = info.fps

    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((scale_h, scale_w, 3))
            ts = frame_idx / fps
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            motion_gray = cv2.resize(gray_full, (motion_w, motion_h), interpolation=cv2.INTER_AREA)

            _osd_mask_image(motion_gray, scaled_rects, motion_w, motion_h)

            motion_score = 0.0
            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, motion_gray)
                motion_score = float(cv2.mean(diff)[0]) / 255.0

            fg_mask = bg_subtractor.apply(motion_gray)
            mog2_ratio = float(cv2.countNonZero(fg_mask)) / (motion_w * motion_h)

            dh = _compute_dhash(motion_gray)

            motion_hsv = cv2.cvtColor(
                cv2.cvtColor(motion_gray, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2HSV
            )
            lighting = _lighting(
                motion_hsv, config.prefilter.night_luma, config.prefilter.ir_max_sat
            )

            keep_reason = ""
            if frame_idx == 0:
                keep_reason = "first"
            elif (
                last_kept_dhash is not None
                and dhash_hamming(dh, last_kept_dhash)
                > config.prefilter.scene_change_hash_dist
            ):
                keep_reason = "scene_change"
                bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                    history=300, varThreshold=25, detectShadows=False
                )
            elif motion_score > thr or mog2_ratio > thr:
                keep_reason = "motion"
            elif (ts - last_kept_t) >= config.engine.heartbeat_sec:
                keep_reason = "heartbeat"

            prev_gray = motion_gray.copy()

            if keep_reason:
                out_frame: np.ndarray = frame
                if lighting in ("night", "ir"):
                    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                    l_channel: np.ndarray = clahe.apply(lab[:, :, 0])
                    out_frame = cv2.cvtColor(
                        np.dstack([l_channel, lab[:, :, 1], lab[:, :, 2]]).astype(np.uint8),
                        cv2.COLOR_LAB2BGR,
                    )
                last_kept_dhash = dh
                last_kept_t = ts
                yield FrameData(
                    frame_number=frame_idx,
                    timestamp_sec=ts,
                    image=out_frame,
                    motion_score=motion_score,
                    mog2_ratio=mog2_ratio,
                    dhash=dh,
                    lighting=lighting,
                    keep_reason=keep_reason,
                )

            frame_idx += 1
    finally:
        proc.stdout.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def save_keyframe(
    image: np.ndarray, out_path: Path, max_width: int = 1280, quality: int = 80
) -> None:
    h, w = image.shape[:2]
    if w > max_width:
        new_h = int(round(h * max_width / w))
        image = cv2.resize(image, (max_width, new_h), interpolation=cv2.INTER_AREA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image, [cv2.IMWRITE_JPEG_QUALITY, quality])