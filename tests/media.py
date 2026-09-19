from __future__ import annotations

import subprocess
from pathlib import Path

OSD_FILTER: str = (
    r"geq=r=if(lt(Y\,40)\,mod(T*255\,255)\,p(X\,Y))"
    r":g=if(lt(Y\,40)\,mod(T*255\,255)\,p(X\,Y))"
    r":b=if(lt(Y\,40)\,mod(T*255\,255)\,p(X\,Y))"
)


def make_clip(
    path: Path,
    src: str,
    duration: int = 10,
    fps: int = 10,
    extra_out: list[str] | None = None,
    vf: str | None = None,
) -> Path:
    out_path = path.with_suffix(".mp4")
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        src,
        "-t",
        str(duration),
        "-r",
        str(fps),
        "-s",
        "320x240",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if vf is not None:
        cmd.extend(["-vf", vf])
    if extra_out:
        cmd.extend(extra_out)
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def make_concat_clip(path: Path, src1: str, src2: str) -> Path:
    out_path = path.with_suffix(".mp4")
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-f", "lavfi", "-i", src1,
        "-f", "lavfi", "-i", src2,
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path