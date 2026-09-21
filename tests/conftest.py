from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_artifact_dir(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if request.node.get_closest_marker("real_defaults") is not None:
        return
    import video_security.config as cfg

    monkeypatch.setattr(
        cfg.StorageConfig,
        "artifact_dir",
        str(tmp_path / "artifacts"),
        raising=False,
    )
