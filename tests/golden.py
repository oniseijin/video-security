from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GOLDEN_WIDTH = 640
GOLDEN_HEIGHT = 480
GOLDEN_FPS = 10
GOLDEN_FRAMES = 100
GOLDEN_PLATE = "YOLO42"
GOLDEN_PERSON_RECT = (100, 320, 190, 470)
GOLDEN_PLATE_RECT = (6, 396, 76, 422)

BUS_W = 260
BUS_H = 173
PERSON_W = 90
PERSON_H = 150

_FIXTURES = Path(__file__).parent / "fixtures"
_BUS_CUTOUT = _FIXTURES / "bus_cutout.png"
_PERSON_CUTOUT = _FIXTURES / "person_cutout.png"


def _vehicle_rect(i: int) -> tuple[int, int, int, int]:
    x1 = -300 + int(round(i * 9.8))
    return (x1, 300, x1 + BUS_W, 473)


GOLDEN_CAR_RECTS: list[tuple[int, int, int, int]] = [
    _vehicle_rect(i) for i in range(GOLDEN_FRAMES)
]


def scaled_rect(
    rect: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int]:
    sx = width / GOLDEN_WIDTH
    sy = height / GOLDEN_HEIGHT
    return (int(rect[0] * sx), int(rect[1] * sy), int(rect[2] * sx), int(rect[3] * sy))


def _plate_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 20
        )
    except OSError:
        return ImageFont.load_default()


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

    bus = Image.open(_BUS_CUTOUT).resize((BUS_W, BUS_H))
    x1, _, _, _ = GOLDEN_CAR_RECTS[i]
    img.paste(bus, (x1, 300))
    px1, py1, px2, py2 = GOLDEN_PLATE_RECT
    draw.rectangle([x1 + px1, py1, x1 + px2, py2], fill=(0, 0, 0))
    draw.text((x1 + px1 + 4, py1 + 2), GOLDEN_PLATE, fill=(255, 255, 255), font=_plate_font())

    person = Image.open(_PERSON_CUTOUT).resize((PERSON_W, PERSON_H))
    img.paste(person, (GOLDEN_PERSON_RECT[0], GOLDEN_PERSON_RECT[1]))

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
