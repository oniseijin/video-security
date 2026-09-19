from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

GOLDEN_WIDTH = 640
GOLDEN_HEIGHT = 480
GOLDEN_FPS = 10
GOLDEN_FRAMES = 100
GOLDEN_PLATE = "YOLO42"
GOLDEN_PERSON_RECT = (100, 350, 130, 440)

_GOLDEN_CAR_RECTS: list[tuple[int, int, int, int]] = []


def _make_car_rects() -> list[tuple[int, int, int, int]]:
    rects: list[tuple[int, int, int, int]] = []
    for i in range(GOLDEN_FRAMES):
        x1 = -150 + int(round(i * 8.2))
        rects.append((x1, 300, x1 + 120, 360))
    return rects


GOLDEN_CAR_RECTS = _make_car_rects()


def _render_frame(i: int) -> Image.Image:
    img = Image.new("RGB", (GOLDEN_WIDTH, GOLDEN_HEIGHT))
    draw = ImageDraw.Draw(img)

    for r in range(280):
        val = 200 - int(80 * r / 280)
        draw.line([(0, r), (GOLDEN_WIDTH, r)], fill=(val, val, val))

    for r in range(280, GOLDEN_HEIGHT):
        draw.line([(0, r), (GOLDEN_WIDTH, r)], fill=(60, 60, 60))

    for y_start in (300, 340, 380, 420):
        draw.rectangle([318, y_start, 322, y_start + 20], fill=(255, 255, 255))

    x1, _, _, _ = GOLDEN_CAR_RECTS[i]
    draw.rectangle([x1, 300, x1 + 120, 360], fill=(255, 255, 255))
    draw.rectangle([x1 + 8, 350, x1 + 24, 360], fill=(0, 0, 0))
    draw.rectangle([x1 + 96, 350, x1 + 112, 360], fill=(0, 0, 0))
    draw.rectangle([x1 + 4, 322, x1 + 60, 340], fill=(0, 0, 0))
    draw.text((x1 + 8, 323), GOLDEN_PLATE, fill=(255, 255, 255))

    draw.rectangle(GOLDEN_PERSON_RECT, fill=(30, 40, 120))

    return img


def make_golden_clip(path: Path) -> Path:
    args: list[str] = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "640x480",
        "-r",
        "10",
        "-i",
        "pipe:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data = b"".join(
        _render_frame(i).tobytes() for i in range(GOLDEN_FRAMES)
    )
    assert proc.stdin is not None
    proc.stdin.write(data)
    proc.stdin.close()
    proc.wait()
    return path


_cache: Path | None = None


def golden_clip(path: Path) -> Path:
    global _cache
    if _cache is None:
        tmpdir = Path(tempfile.mkdtemp())
        _cache = tmpdir / "golden.mp4"
        make_golden_clip(_cache)
    return Path(shutil.copy2(str(_cache), str(path)))