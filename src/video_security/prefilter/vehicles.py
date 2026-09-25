from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
from ultralytics import YOLO  # type: ignore[attr-defined]

from video_security.config import CameraOverrides, Config
from video_security.ingest.frames import FrameData

COCO_RELEVANT: list[int] = [0, 1, 2, 3, 5, 7, 16, 17]
VEHICLE_CLASSES: list[int] = [2, 3, 5, 7]
ANIMAL_CLASSES: list[int] = [16, 17]


def box_kind(class_id: int) -> str | None:
    if class_id == 0:
        return "person"
    if class_id in ANIMAL_CLASSES:
        return "animal"
    return None


@dataclasses.dataclass
class Detection:
    track_id: int | None
    class_id: int
    bbox: tuple[int, int, int, int]
    conf: float


@dataclasses.dataclass
class FrameDetections:
    frame_number: int
    timestamp_sec: float
    detections: list[Detection]


@dataclasses.dataclass
class VehicleTrack:
    track_id: int
    class_id: int
    first_frame: int
    last_frame: int
    first_ts: float
    last_ts: float
    bboxes: dict[int, tuple[int, int, int, int]]
    direction: str | None = None
    weaving_score: float | None = None


def load_detector(
    config: Config, camera: CameraOverrides | None = None
) -> Callable[[np.ndarray], list[Detection]]:
    if config.prefilter.yolo_coreml_path and Path(config.prefilter.yolo_coreml_path).exists():
        model_path = config.prefilter.yolo_coreml_path
    else:
        model_path = config.prefilter.yolo_model
    model: Any = YOLO(model_path)
    classes = (
        camera.yolo_classes
        if (camera and camera.yolo_classes)
        else COCO_RELEVANT
    )

    def detect(image: np.ndarray) -> list[Detection]:
        result = model.track(
            image,
            persist=True,
            tracker="bytetrack.yaml",
            classes=classes,
            conf=config.prefilter.yolo_conf,
            verbose=False,
        )[0]
        boxes = result.boxes
        ids = boxes.id
        if ids is None:
            return []
        detections: list[Detection] = []
        for tid, xyxy, cls, c in zip(
            ids.cpu().tolist(),
            boxes.xyxy.cpu().tolist(),
            boxes.cls.cpu().tolist(),
            boxes.conf.cpu().tolist(),
            strict=True,
        ):
            detections.append(
                Detection(
                    track_id=int(tid),
                    class_id=int(cls),
                    bbox=(int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])),
                    conf=float(c),
                )
            )
        return detections

    return detect


def track_vehicles(
    frames: Iterable[FrameData],
    config: Config,
    camera: CameraOverrides | None = None,
    detector: Callable[[np.ndarray], list[Detection]] | None = None,
) -> tuple[list[VehicleTrack], list[FrameDetections]]:
    if detector is None:
        detector = load_detector(config, camera)

    frame_dets: list[FrameDetections] = []
    for frame in frames:
        dets = detector(frame.image)
        frame_dets.append(
            FrameDetections(
                frame_number=frame.frame_number,
                timestamp_sec=frame.timestamp_sec,
                detections=dets,
            )
        )
    return accumulate_tracks(frame_dets), frame_dets


def accumulate_tracks(
    frame_dets: list[FrameDetections],
) -> list[VehicleTrack]:
    tracks_raw: dict[int, dict[str, Any]] = {}
    track_class_votes: dict[int, dict[int, int]] = {}

    for fd in frame_dets:
        for det in fd.detections:
            tid = det.track_id
            if tid is None:
                continue
            if tid not in tracks_raw:
                tracks_raw[tid] = {
                    "first_frame": fd.frame_number,
                    "last_frame": fd.frame_number,
                    "first_ts": fd.timestamp_sec,
                    "last_ts": fd.timestamp_sec,
                    "bboxes": {fd.frame_number: det.bbox},
                }
                track_class_votes[tid] = {det.class_id: 1}
            else:
                t = tracks_raw[tid]
                if fd.frame_number < t["first_frame"]:
                    t["first_frame"] = fd.frame_number
                    t["first_ts"] = fd.timestamp_sec
                if fd.frame_number > t["last_frame"]:
                    t["last_frame"] = fd.frame_number
                    t["last_ts"] = fd.timestamp_sec
                t["bboxes"][fd.frame_number] = det.bbox
                track_class_votes[tid][det.class_id] = (
                    track_class_votes[tid].get(det.class_id, 0) + 1
                )

    vehicles: list[VehicleTrack] = []
    for tid in sorted(tracks_raw):
        t = tracks_raw[tid]
        if len(t["bboxes"]) < 2:
            continue
        votes = track_class_votes[tid]
        best_class = max(votes, key=lambda k: votes[k])

        bboxes: dict[int, tuple[int, int, int, int]] = t["bboxes"]
        sorted_fn = sorted(bboxes.keys())
        first_bbox = bboxes[sorted_fn[0]]
        last_bbox = bboxes[sorted_fn[-1]]
        first_cx = (first_bbox[0] + first_bbox[2]) / 2.0
        last_cx = (last_bbox[0] + last_bbox[2]) / 2.0
        diff = last_cx - first_cx
        direction: str | None = None
        if diff > 10:
            direction = "E"
        elif diff < -10:
            direction = "W"

        weaving_score: float | None = None
        if len(bboxes) >= 10:
            frame_indices: list[int] = sorted(bboxes.keys())
            center_xs: list[float] = [
                (bboxes[fi][0] + bboxes[fi][2]) / 2.0 for fi in frame_indices
            ]
            mean_width = float(
                np.mean([bboxes[fi][2] - bboxes[fi][0] for fi in frame_indices])
            )
            if mean_width <= 0:
                weaving_score = None
            else:
                try:
                    (_, _) = np.polyfit(
                        frame_indices, center_xs, 1
                    ).shape
                    slope, intercept = np.polyfit(
                        np.array(frame_indices, dtype=np.float64),
                        np.array(center_xs, dtype=np.float64),
                        1,
                    )
                    residuals = np.array(center_xs) - (
                        slope * np.array(frame_indices) + intercept
                    )
                    weaving_score = float(np.std(residuals) / mean_width)
                except (np.linalg.LinAlgError, ValueError):
                    weaving_score = None

        vehicles.append(
            VehicleTrack(
                track_id=tid,
                class_id=best_class,
                first_frame=t["first_frame"],
                last_frame=t["last_frame"],
                first_ts=t["first_ts"],
                last_ts=t["last_ts"],
                bboxes=bboxes,
                direction=direction,
                weaving_score=weaving_score,
            )
        )

    return vehicles


def crop_vehicle(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    margin: float = 0.15,
) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    mx = int(round(w * margin))
    my = int(round(h * margin))
    h_img, w_img = image.shape[:2]
    nx1 = max(0, x1 - mx)
    ny1 = max(0, y1 - my)
    nx2 = min(w_img, x2 + mx)
    ny2 = min(h_img, y2 + my)
    return image[ny1:ny2, nx1:nx2]