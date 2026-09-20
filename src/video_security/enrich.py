from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

_DASH_MODES = {"NORMAL", "EVENT", "PARKING", "MANUAL", "PICTURE"}


def clip_mode(video_path: str) -> str | None:
    for part in Path(video_path).parts:
        if part.upper() in _DASH_MODES:
            return part.upper()
    return None


def event_category(mode: str | None, gps_row: sqlite3.Row | None) -> str:
    if mode == "PARKING":
        return "parking"
    if gps_row is not None and gps_row["speed_kmh"] is not None:
        return "driving" if float(gps_row["speed_kmh"]) >= 5.0 else "stationary"
    return "unknown"


def scene_description(
    evt: sqlite3.Row,
    gps_row: sqlite3.Row | None,
    category: str,
    face_count: int,
    plate_texts: list[str],
) -> str:
    bits: list[str] = []
    lead = {
        "driving": "Vehicle driving",
        "parking": "Parked vehicle",
        "stationary": "Vehicle stationary",
    }.get(category, "Scene")
    bits.append(lead)
    if gps_row is not None and gps_row["speed_kmh"] is not None:
        bits.append(f"at {float(gps_row['speed_kmh']):.0f} km/h")
    if evt["event_type"] in {"hard_corner", "impact", "hard_brake"} and gps_row is not None:
        ax = gps_row["ax"]
        ay = gps_row["ay"]
        if ax is not None or ay is not None:
            lateral = math.sqrt(
                float(ax or 0.0) ** 2 + float(ay or 0.0) ** 2
            )
            bits.append(f"lateral {lateral:.2f}g")
    if face_count:
        bits.append(f"{face_count} face{'s' if face_count != 1 else ''} visible")
    if plate_texts:
        bits.append("plate " + ", ".join(plate_texts))
    if gps_row is not None and gps_row["lat"] is not None:
        bits.append(format_coord(gps_row))
    return " · ".join(bits)


def nearest_gps(
    track: list[sqlite3.Row], target_sec: float
) -> sqlite3.Row | None:
    if not track:
        return None
    return min(track, key=lambda r: abs(r["time_sec"] - target_sec))


def gps_track(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT time_sec, lat, lon, speed_kmh, ax, ay FROM clip_gps_data "
        "WHERE job_id = ? AND lat IS NOT NULL AND lon IS NOT NULL "
        "ORDER BY time_sec",
        (job_id,),
    ).fetchall()


def format_coord(row: sqlite3.Row) -> str:
    return f"{row['lat']:.5f}, {row['lon']:.5f}"


def event_description(conn: sqlite3.Connection, evt: sqlite3.Row) -> str:
    if evt["llm_result_id"] is None:
        return ""
    ar = conn.execute(
        "SELECT raw_response FROM analysis_results WHERE id = ?",
        (evt["llm_result_id"],),
    ).fetchone()
    if ar is None:
        return ""
    try:
        resp = json.loads(ar["raw_response"])
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(resp.get("description", ""))