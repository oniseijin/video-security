from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import ImageFont

from tests.golden import (
    GOLDEN_CAR_RECTS,
    GOLDEN_FRAMES,
    GOLDEN_HEIGHT,
    GOLDEN_PERSON_RECT,
    GOLDEN_PLATE,
    GOLDEN_WIDTH,
    golden_clip,
    make_golden_clip,
)


def test_properties() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.mp4"
        golden_clip(path)

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        info = json.loads(result.stdout)
        streams = info["streams"]
        assert len(streams) == 1
        s = streams[0]
        assert s["codec_type"] == "video"
        assert s["width"] == GOLDEN_WIDTH
        assert s["height"] == GOLDEN_HEIGHT

        nb = s.get("nb_frames")
        if nb is None:
            result2 = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-count_frames",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            info2 = json.loads(result2.stdout)
            nb = info2["streams"][0]["nb_read_frames"]
        assert nb == str(GOLDEN_FRAMES)


def test_size() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.mp4"
        clip = golden_clip(path)
        assert clip.stat().st_size < 5_000_000


def test_determinism() -> None:
    method = "sha256"
    with tempfile.TemporaryDirectory() as tmpdir:
        p1 = Path(tmpdir) / "a.mp4"
        p2 = Path(tmpdir) / "b.mp4"
        make_golden_clip(p1)
        make_golden_clip(p2)

        h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
        equal = h1 == h2

        if not equal:
            method = "framemd5"
            framemd5: list[str] = []
            for p in (p1, p2):
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-i",
                        str(p),
                        "-map",
                        "0:v",
                        "-f",
                        "framemd5",
                        "-",
                    ],
                    capture_output=True,
                    text=True,
                )
                hashes: list[str] = []
                for line in result.stdout.splitlines():
                    if line.startswith("0,"):
                        parts = line.split(",")
                        if len(parts) >= 3:
                            hashes.append(parts[2].split()[1])
                framemd5.append("\n".join(hashes[:20]))
            equal = framemd5[0] == framemd5[1]
        assert equal
        assert method in ("sha256", "framemd5")
        print(f"\n    determinism method: {method}")


def test_constants() -> None:
    assert len(GOLDEN_CAR_RECTS) == GOLDEN_FRAMES
    assert GOLDEN_CAR_RECTS[-1][0] > 500
    x1, y1, x2, y2 = GOLDEN_PERSON_RECT
    assert 0 <= x1 < GOLDEN_WIDTH
    assert 0 <= y1 < GOLDEN_HEIGHT
    assert 0 <= x2 <= GOLDEN_WIDTH
    assert 0 <= y2 <= GOLDEN_HEIGHT
    font = ImageFont.load_default()
    bbox = font.getbbox(GOLDEN_PLATE)
    text_w = bbox[2] - bbox[0]
    assert text_w < 50