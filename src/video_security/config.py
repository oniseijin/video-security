from __future__ import annotations

import dataclasses
import json
import os
import tomllib
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    pass


@dataclasses.dataclass
class GsensConfig:
    hard_brake_g: float = 0.35
    hard_corner_g: float = 0.30
    impact_g: float = 0.80


@dataclasses.dataclass
class MazdaCx8Config:
    timezone: str = "Asia/Tokyo"
    priority: dict[str, float] = dataclasses.field(
        default_factory=lambda: {"EVENT": 1.0, "PARKING": 0.8, "MANUAL": 0.7, "NORMAL": 0.3}
    )
    gsens: GsensConfig = dataclasses.field(default_factory=GsensConfig)


@dataclasses.dataclass
class StorageConfig:
    db_path: str = "~/.video-security/db"
    artifact_dir: str = "/Volumes/lacie8/Ryan/video/vs"
    staging_dir: str | None = None


@dataclasses.dataclass
class ImportConfig:
    card_mount: str = "/Volumes/CX-8"
    archive_dirs: list[str] = dataclasses.field(
        default_factory=lambda: ["/Volumes/lacie8/Ryan/video/CX-8"]
    )
    preflight_gb: int = 25


@dataclasses.dataclass
class EngineConfig:
    heartbeat_sec: int = 30
    max_llm_events: int = 100
    retention_days: int = 30
    disk_preflight_gb: int = 10
    disk_watermark_gb: int = 5


@dataclasses.dataclass
class LLMStageConfig:
    model: str = ""
    num_ctx: int = 0
    timeout_s: int = 0


@dataclasses.dataclass
class WhisperConfig:
    model: str = "small"
    language: str | None = None


@dataclasses.dataclass
class PrefilterConfig:
    scene_text_sample_sec: int = 30
    motion_threshold: float = 0.05
    scene_change_hash_dist: int = 12
    night_luma: int = 60
    ir_max_sat: int = 20
    decode_width: int = 1280
    yolo_model: str = "yolov8n"
    yolo_conf: float = 0.25
    yolo_coreml_path: str | None = None
    ocr_min_conf: float = 0.3
    plate_min_votes: int = 2


@dataclasses.dataclass
class AudioConfig:
    rms_window_ms: int = 100
    rms_sustain_ms: int = 500
    rms_factor: float = 4.0
    rms_floor: float = 0.02
    vad_pad_ms: int = 300
    distress_keywords: list[str] = dataclasses.field(
        default_factory=lambda: ["help", "police", "get out"]
    )


@dataclasses.dataclass
class ThreatConfig:
    score_threshold: float = 0.5
    person_weight: float = 0.4
    motion_weight: float = 0.3
    time_weight: float = 0.3
    after_hours: list[str] = dataclasses.field(
        default_factory=lambda: ["22:00-06:00"]
    )
    loiter_min_sec: int = 60
    merge_gap_sec: int = 5
    priority: dict[str, float] = dataclasses.field(
        default_factory=lambda: {
            "intrusion": 0.9,
            "loitering": 0.6,
            "suspicious_behavior": 0.5,
        }
    )


@dataclasses.dataclass
class CameraOverrides:
    osd_mask: list[list[int]] = dataclasses.field(default_factory=list)
    after_hours: list[str] = dataclasses.field(default_factory=list)
    motion_threshold: float | None = None
    yolo_classes: list[int] | None = None
    homography: Any | None = None

    @classmethod
    def from_json(cls, json_str: str) -> CameraOverrides:
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Camera config JSON parse error: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError("Camera config JSON must be an object")

        osd_mask = data.get("osd_mask", [])
        if not isinstance(osd_mask, list) or not all(
            isinstance(m, list) and len(m) == 4 and all(isinstance(v, int) for v in m)
            for m in osd_mask
        ):
            raise ConfigError("Camera config: osd_mask must be list of [x1,y1,x2,y2]")

        after_hours = data.get("after_hours", [])
        if not isinstance(after_hours, list) or not all(isinstance(h, str) for h in after_hours):
            raise ConfigError("Camera config: after_hours must be list of strings")

        motion_threshold = data.get("motion_threshold")
        if motion_threshold is not None and not isinstance(motion_threshold, (int, float)):
            raise ConfigError("Camera config: motion_threshold must be float or null")

        yolo_classes = data.get("yolo_classes")
        if yolo_classes is not None and (
            not isinstance(yolo_classes, list)
            or not all(isinstance(c, int) for c in yolo_classes)
        ):
            raise ConfigError("Camera config: yolo_classes must be list of ints or null")

        return cls(
            osd_mask=osd_mask,
            after_hours=after_hours,
            motion_threshold=float(motion_threshold) if motion_threshold is not None else None,
            yolo_classes=yolo_classes,
            homography=data.get("homography"),
        )


@dataclasses.dataclass
class Config:
    storage: StorageConfig = dataclasses.field(default_factory=StorageConfig)
    import_: ImportConfig = dataclasses.field(default_factory=ImportConfig)
    adapter_mazda_cx8: MazdaCx8Config = dataclasses.field(default_factory=MazdaCx8Config)
    engine: EngineConfig = dataclasses.field(default_factory=EngineConfig)
    llm_triage: LLMStageConfig = dataclasses.field(
        default_factory=lambda: LLMStageConfig(model="gemma3:4b", num_ctx=2048, timeout_s=120)
    )
    llm_detail: LLMStageConfig = dataclasses.field(
        default_factory=lambda: LLMStageConfig(model="gemma4:12b", num_ctx=8192, timeout_s=300)
    )
    whisper: WhisperConfig = dataclasses.field(default_factory=WhisperConfig)
    prefilter: PrefilterConfig = dataclasses.field(default_factory=PrefilterConfig)
    audio: AudioConfig = dataclasses.field(default_factory=AudioConfig)
    threat: ThreatConfig = dataclasses.field(default_factory=ThreatConfig)


def _merge_dataclass(default: Any, overrides: dict[str, Any], path: str) -> Any:
    field_map = {f.name: f for f in dataclasses.fields(default)}
    kwargs: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in field_map:
            continue
        kwargs[key] = _merge_value(getattr(default, key), value, f"{path}.{key}")
    return dataclasses.replace(default, **kwargs)


def _merge_value(default: Any, override: Any, path: str) -> Any:
    if isinstance(default, bool):
        if not isinstance(override, bool):
            raise ConfigError(f"Expected bool at {path}, got {type(override).__name__}")
        return override
    if isinstance(default, int):
        if not isinstance(override, int) or isinstance(override, bool):
            raise ConfigError(f"Expected int at {path}, got {type(override).__name__}")
        return override
    if isinstance(default, float):
        if not isinstance(override, (int, float)) or isinstance(override, bool):
            raise ConfigError(f"Expected float at {path}, got {type(override).__name__}")
        return float(override)
    if isinstance(default, str):
        if not isinstance(override, str):
            raise ConfigError(f"Expected str at {path}, got {type(override).__name__}")
        return override
    if isinstance(default, dict):
        if not isinstance(override, dict):
            raise ConfigError(f"Expected dict at {path}, got {type(override).__name__}")
        result: dict[Any, Any] = dict(default)
        for k, v in override.items():
            if k not in default:
                continue
            result[k] = _merge_value(default[k], v, f"{path}.{k}")
        return result
    if default is None:
        return override
    if dataclasses.is_dataclass(default):
        if not isinstance(override, dict):
            raise ConfigError(f"Expected table at {path}")
        return _merge_dataclass(default, override, path)
    return override


def _expand_paths(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            setattr(obj, f.name, _expand_paths(getattr(obj, f.name)))
        return obj
    if isinstance(obj, dict):
        return {k: _expand_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_paths(item) for item in obj]
    if isinstance(obj, str):
        return os.path.expanduser(obj)
    return obj


def load_config(path: Path | None = None) -> Config:
    config = Config()
    if path is not None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError(f"Config file not found: {path}") from None
        try:
            toml_data = tomllib.loads(raw)
        except Exception as e:
            raise ConfigError(f"TOML parse error in {path}: {e}") from e
        config = _apply_toml_overrides(config, toml_data)
    _expand_paths(config.storage)
    _expand_paths(config.import_)
    return config


def _apply_toml_overrides(config: Config, toml_data: dict[str, Any]) -> Config:
    kwargs: dict[str, Any] = {}
    for section, values in toml_data.items():
        if not isinstance(values, dict):
            continue
        if section == "storage":
            kwargs["storage"] = _merge_dataclass(config.storage, values, "storage")
        elif section == "import":
            kwargs["import_"] = _merge_dataclass(config.import_, values, "import")
        elif section == "adapter":
            if "mazda_cx8" in values and isinstance(values["mazda_cx8"], dict):
                mcx8 = values["mazda_cx8"]
                adapter_kwargs: dict[str, Any] = {}
                if "timezone" in mcx8:
                    adapter_kwargs["timezone"] = _merge_value(
                        config.adapter_mazda_cx8.timezone,
                        mcx8["timezone"],
                        "adapter.mazda_cx8.timezone",
                    )
                if "priority" in mcx8:
                    adapter_kwargs["priority"] = _merge_value(
                        config.adapter_mazda_cx8.priority,
                        mcx8["priority"],
                        "adapter.mazda_cx8.priority",
                    )
                if "gsens" in mcx8:
                    adapter_kwargs["gsens"] = _merge_dataclass(
                        config.adapter_mazda_cx8.gsens,
                        mcx8["gsens"],
                        "adapter.mazda_cx8.gsens",
                    )
                if adapter_kwargs:
                    kwargs["adapter_mazda_cx8"] = dataclasses.replace(
                        config.adapter_mazda_cx8, **adapter_kwargs
                    )
        elif section == "engine":
            kwargs["engine"] = _merge_dataclass(config.engine, values, "engine")
        elif section == "llm":
            if "triage" in values and isinstance(values["triage"], dict):
                kwargs["llm_triage"] = _merge_dataclass(
                    config.llm_triage, values["triage"], "llm.triage"
                )
            if "detail" in values and isinstance(values["detail"], dict):
                kwargs["llm_detail"] = _merge_dataclass(
                    config.llm_detail, values["detail"], "llm.detail"
                )
        elif section == "whisper":
            kwargs["whisper"] = _merge_dataclass(config.whisper, values, "whisper")
        elif section == "audio":
            kwargs["audio"] = _merge_dataclass(config.audio, values, "audio")
        elif section == "threat":
            if "priority" in values and isinstance(values["priority"], dict):
                kwargs["threat"] = _merge_dataclass(
                    config.threat,
                    {k: v for k, v in values.items() if k != "priority"},
                    "threat",
                )
                kwargs["threat"].priority = _merge_value(
                    config.threat.priority,
                    values["priority"],
                    "threat.priority",
                )
            else:
                kwargs["threat"] = _merge_dataclass(config.threat, values, "threat")
        elif section == "prefilter":
            kwargs["prefilter"] = _merge_dataclass(config.prefilter, values, "prefilter")
    return dataclasses.replace(config, **kwargs)