from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from video_security.config import (
    CameraOverrides,
    Config,
    ConfigError,
    EngineConfig,
    GsensConfig,
    ImportConfig,
    LLMStageConfig,
    MapConfig,
    MazdaCx8Config,
    PrefilterConfig,
    StorageConfig,
    WebConfig,
    WhisperConfig,
    load_config,
)


@pytest.mark.real_defaults
def test_defaults() -> None:
    cfg = load_config()
    assert isinstance(cfg.storage, StorageConfig)
    assert cfg.storage.db_path == os.path.expanduser("~/.video-security/db")
    assert cfg.storage.artifact_dir == os.path.expanduser("~/.video-security/artifacts")
    assert cfg.storage.staging_dir is None

    assert isinstance(cfg.import_, ImportConfig)
    assert cfg.import_.card_mount == "/Volumes/CX-8"
    assert cfg.import_.archive_dirs == ["/Volumes/lacie8/Ryan/video/CX-8"]
    assert cfg.import_.preflight_gb == 25

    assert isinstance(cfg.adapter_mazda_cx8, MazdaCx8Config)
    assert cfg.adapter_mazda_cx8.timezone == "Asia/Tokyo"
    assert cfg.adapter_mazda_cx8.priority == {
        "EVENT": 1.0,
        "PARKING": 0.8,
        "MANUAL": 0.7,
        "NORMAL": 0.3,
    }
    assert isinstance(cfg.adapter_mazda_cx8.gsens, GsensConfig)
    assert cfg.adapter_mazda_cx8.gsens.hard_brake_g == 0.35
    assert cfg.adapter_mazda_cx8.gsens.hard_corner_g == 0.30
    assert cfg.adapter_mazda_cx8.gsens.impact_g == 0.80

    assert isinstance(cfg.engine, EngineConfig)
    assert cfg.engine.heartbeat_sec == 30
    assert cfg.engine.max_llm_events == 100
    assert cfg.engine.retention_days == 30
    assert cfg.engine.disk_preflight_gb == 10
    assert cfg.engine.disk_watermark_gb == 5

    assert isinstance(cfg.llm_triage, LLMStageConfig)
    assert cfg.llm_triage.model == "gemma3:4b"
    assert cfg.llm_triage.num_ctx == 2048
    assert cfg.llm_triage.timeout_s == 120

    assert isinstance(cfg.llm_detail, LLMStageConfig)
    assert cfg.llm_detail.model == "gemma4:12b"
    assert cfg.llm_detail.num_ctx == 8192
    assert cfg.llm_detail.timeout_s == 300

    assert isinstance(cfg.whisper, WhisperConfig)
    assert cfg.whisper.model == "small"
    assert cfg.whisper.language is None

    assert isinstance(cfg.prefilter, PrefilterConfig)
    assert cfg.prefilter.scene_text_sample_sec == 30

    assert isinstance(cfg.map, MapConfig)
    assert cfg.map.carto_api_key is None

    assert isinstance(cfg.web, WebConfig)
    assert cfg.web.host == "127.0.0.1"
    assert cfg.web.port == 8377


def test_toml_override_deep_merge(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""\
[engine]
heartbeat_sec = 60
retention_days = 14

[llm.triage]
model = "custom:model"

[adapter.mazda_cx8.gsens]
hard_brake_g = 0.50
""")
    cfg = load_config(toml_file)
    assert cfg.engine.heartbeat_sec == 60
    assert cfg.engine.retention_days == 14
    assert cfg.engine.max_llm_events == 100  # not overridden
    assert cfg.llm_triage.model == "custom:model"
    assert cfg.llm_triage.num_ctx == 2048  # not overridden
    assert cfg.adapter_mazda_cx8.gsens.hard_brake_g == 0.50
    assert cfg.adapter_mazda_cx8.gsens.hard_corner_g == 0.30  # not overridden
    assert cfg.adapter_mazda_cx8.timezone == "Asia/Tokyo"  # not overridden


def test_map_and_web_config(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""\
[map]
carto_api_key = "cb1_test_key"

[web]
port = 9000
""")
    cfg = load_config(toml_file)
    assert cfg.map.carto_api_key == "cb1_test_key"
    assert cfg.web.port == 9000
    assert cfg.web.host == "127.0.0.1"  # not overridden

    defaults = load_config()
    assert defaults.map.carto_api_key is None
    assert defaults.web.port == 8377


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""\
[unknown_section]
foo = "bar"

[storage]
db_path = "/custom/path"
unknown_key = 42
""")
    cfg = load_config(toml_file)
    assert cfg.storage.db_path == "/custom/path"


def test_bad_type_raises(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("[engine]\nheartbeat_sec = \"not_a_number\"")
    with pytest.raises(ConfigError, match="heartbeat_sec"):
        load_config(toml_file)


def test_camera_json_parse_valid() -> None:
    co = CameraOverrides.from_json(
        json.dumps({
            "osd_mask": [[10, 20, 30, 40]],
            "after_hours": ["22:00-06:00"],
            "motion_threshold": 0.05,
            "yolo_classes": [0, 1, 2],
            "homography": None,
        })
    )
    assert co.osd_mask == [[10, 20, 30, 40]]
    assert co.after_hours == ["22:00-06:00"]
    assert co.motion_threshold == 0.05
    assert co.yolo_classes == [0, 1, 2]
    assert co.homography is None


def test_camera_json_parse_minimal() -> None:
    co = CameraOverrides.from_json("{}")
    assert co.osd_mask == []
    assert co.after_hours == []
    assert co.motion_threshold is None
    assert co.yolo_classes is None
    assert co.homography is None


def test_camera_json_bad_osd_mask() -> None:
    with pytest.raises(ConfigError, match="osd_mask"):
        CameraOverrides.from_json('{"osd_mask": [[10, 20, 30]]}')


def test_camera_json_bad_json() -> None:
    with pytest.raises(ConfigError, match="JSON"):
        CameraOverrides.from_json("not json")


def test_camera_json_not_object() -> None:
    with pytest.raises(ConfigError, match="object"):
        CameraOverrides.from_json("[]")


def test_nonexistent_config_file(tmp_path: Path) -> None:
    fake = tmp_path / "nonexistent.toml"
    with pytest.raises(ConfigError, match="not found"):
        load_config(fake)


def test_malformed_toml(tmp_path: Path) -> None:
    toml_file = tmp_path / "bad.toml"
    toml_file.write_text("this is not valid toml = [")
    with pytest.raises(ConfigError, match="TOML"):
        load_config(toml_file)


def test_tilde_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('[storage]\ndb_path = "~/custom/db"\n')
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", "/Users/test"))
    cfg = load_config(toml_file)
    assert cfg.storage.db_path == "/Users/test/custom/db"


def test_load_config_none() -> None:
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.engine.heartbeat_sec == 30


def test_adapter_photos_defaults() -> None:
    cfg = Config()
    assert cfg.adapter_photos.priority == 0.7
    assert cfg.adapter_photos.device_priorities == {"meta_glasses": 0.8, "iphone": 0.7}


def test_adapter_photos_toml_override(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""\
[adapter.photos]
priority = 0.55

[adapter.photos.device_priorities]
iphone = 0.6
""")
    cfg = load_config(toml_file)
    assert cfg.adapter_photos.priority == 0.55
    assert cfg.adapter_photos.device_priorities == {"meta_glasses": 0.8, "iphone": 0.6}


def test_adapter_photos_bad_priority_type(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("[adapter.photos]\npriority = \"high\"\n")
    with pytest.raises(ConfigError, match="priority"):
        load_config(toml_file)


def test_archive_defaults() -> None:
    cfg = Config()
    assert cfg.archive.cold_dir is None
    assert cfg.archive.days == 30
    assert cfg.archive.video_height == 720


def test_archive_toml_merge(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""\
[archive]
cold_dir = "/Volumes/cold"
days = 7
""")
    cfg = load_config(toml_file)
    assert cfg.archive.cold_dir == "/Volumes/cold"
    assert cfg.archive.days == 7
    assert cfg.archive.video_height == 720


def test_archive_absent_section(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("[engine]\nheartbeat_sec = 45\n")
    cfg = load_config(toml_file)
    assert cfg.archive.cold_dir is None
    assert cfg.archive.days == 30
    assert cfg.archive.video_height == 720


def test_archive_cold_dir_null(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("[archive]\nvideo_height = 1080\n")
    cfg = load_config(toml_file)
    assert cfg.archive.cold_dir is None
    assert cfg.archive.video_height == 1080