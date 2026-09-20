from __future__ import annotations

import numpy as np
import pytest

from video_security.prefilter.faces import _convert_box, detect_faces


def test_convert_box_remaps_origin() -> None:
    result = _convert_box(0.1, 0.2, 0.3, 0.4)
    assert result[0] == 0.1
    assert result[2] == 0.3
    assert result[3] == 0.4
    assert result[1] == pytest.approx(0.4)


def test_convert_box_top_left_zero() -> None:
    result = _convert_box(0.0, 0.0, 0.5, 0.5)
    assert result == (0.0, 0.5, 0.5, 0.5)


def test_detect_faces_returns_empty_for_2d() -> None:
    img = np.zeros((100, 100), dtype=np.uint8)
    assert detect_faces(img) == []