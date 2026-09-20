from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
import urllib.request

_LAST_CALL_MONOTONIC = 0.0
_MIN_INTERVAL_S = 1.1
_USER_AGENT = "video-security/0.1 (local dashcam analyzer)"

_GEO_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gps_reverse_geocode (
    lat_key REAL NOT NULL,
    lon_key REAL NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (lat_key, lon_key)
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_GEO_TABLE_SQL)


def _key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 4), round(lon, 4)


def cached_description(
    conn: sqlite3.Connection, lat: float, lon: float
) -> str | None:
    k = _key(lat, lon)
    row = conn.execute(
        "SELECT description FROM gps_reverse_geocode "
        "WHERE lat_key = ? AND lon_key = ?",
        k,
    ).fetchone()
    if row is None:
        return None
    desc = row["description"]
    return str(desc) if desc else None


def _store(
    conn: sqlite3.Connection, lat: float, lon: float, description: str
) -> None:
    k = _key(lat, lon)
    conn.execute(
        "INSERT OR REPLACE INTO gps_reverse_geocode "
        "(lat_key, lon_key, description) VALUES (?,?,?)",
        (k[0], k[1], description),
    )
    conn.commit()


def _format_address(data: dict[str, object]) -> str | None:
    address = data.get("address")
    if not isinstance(address, dict):
        return None
    parts: list[str] = []
    road = address.get("road") or address.get("pedestrian")
    if isinstance(road, str) and road:
        parts.append(road)
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("suburb")
        or address.get("neighbourhood")
    )
    if isinstance(city, str) and city:
        parts.append(city)
    state = address.get("state")
    if isinstance(state, str) and state:
        parts.append(state)
    country = address.get("country")
    if isinstance(country, str) and country:
        parts.append(country)
    if not parts:
        return None
    return ", ".join(parts[:3])


def reverse_geocode(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    timeout_s: float = 5.0,
) -> str | None:
    global _LAST_CALL_MONOTONIC
    ensure_table(conn)
    hit = cached_description(conn, lat, lon)
    if hit is not None:
        return hit
    wait = _LAST_CALL_MONOTONIC + _MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL_MONOTONIC = time.monotonic()
    query = urllib.parse.urlencode(
        {
            "format": "jsonv2",
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "zoom": 16,
            "accept-language": "ja,en",
        }
    )
    url = "https://nominatim.openstreetmap.org/reverse?" + query
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data: object = json.loads(resp.read().decode())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    description = _format_address(data)
    if description is None:
        return None
    _store(conn, lat, lon, description)
    return description
