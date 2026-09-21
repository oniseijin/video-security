from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from video_security.config import Config

KINDS = ("plate", "text", "person")


@dataclass
class WatchlistHit:
    watchlist_id: int
    job_id: int
    event_id: int | None
    detail: str


def add_watchlist(
    conn: sqlite3.Connection, kind: str, pattern: str, note: str | None
) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not pattern.strip():
        raise ValueError("pattern must not be empty")
    if kind == "person":
        int(pattern)
    cur = conn.execute(
        "INSERT INTO watchlists (kind, pattern, note) VALUES (?, ?, ?)",
        (kind, pattern.strip(), note),
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("watchlist insert failed")
    return int(cur.lastrowid)


def remove_watchlist(conn: sqlite3.Connection, watchlist_id: int) -> bool:
    cur = conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
    conn.execute(
        "DELETE FROM watchlist_hits WHERE watchlist_id = ?", (watchlist_id,)
    )
    conn.commit()
    return cur.rowcount > 0


def list_watchlists(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT w.id, w.kind, w.pattern, w.note, w.created_at, "
        "(SELECT COUNT(*) FROM watchlist_hits h WHERE h.watchlist_id = w.id) "
        "AS hits FROM watchlists w ORDER BY w.id"
    ).fetchall()
    return [dict(r) for r in rows]


def _plate_hits(
    conn: sqlite3.Connection, job_id: int, wl_id: int, pattern: str
) -> list[WatchlistHit]:
    rows = conn.execute(
        "SELECT p.track_id, p.norm_text FROM plates p "
        "WHERE p.job_id = ? AND p.norm_text LIKE ?",
        (job_id, pattern),
    ).fetchall()
    hits: list[WatchlistHit] = []
    for r in rows:
        ev = conn.execute(
            "SELECT id FROM events WHERE job_id = ? AND track_id = ? "
            "AND event_type = 'plate_capture' ORDER BY id LIMIT 1",
            (job_id, r["track_id"]),
        ).fetchone()
        hits.append(
            WatchlistHit(
                wl_id, job_id, int(ev["id"]) if ev else None, str(r["norm_text"])
            )
        )
    return hits


def _text_hits(
    conn: sqlite3.Connection, job_id: int, wl_id: int, pattern: str
) -> list[WatchlistHit]:
    try:
        rows = conn.execute(
            "SELECT ft.text FROM frame_text_fts ft "
            "JOIN frame_text f ON f.id = ft.rowid "
            "WHERE frame_text_fts MATCH ? AND f.job_id = ? LIMIT 20",
            (pattern, job_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        WatchlistHit(wl_id, job_id, None, str(r["text"])) for r in rows
    ]


def _person_hits(
    conn: sqlite3.Connection, job_id: int, wl_id: int, pattern: str
) -> list[WatchlistHit]:
    try:
        pid = int(pattern)
    except ValueError:
        return []
    rows = conn.execute(
        "SELECT f.event_id FROM faces f WHERE f.job_id = ? AND f.person_id = ?",
        (job_id, pid),
    ).fetchall()
    return [
        WatchlistHit(wl_id, job_id, int(r["event_id"]), f"person {pid}")
        for r in rows
    ]


def evaluate_job(conn: sqlite3.Connection, cfg: Config, job_id: int) -> list[WatchlistHit]:
    watchlists = conn.execute("SELECT id, kind, pattern FROM watchlists").fetchall()
    hits: list[WatchlistHit] = []
    for wl in watchlists:
        kind, pattern = str(wl["kind"]), str(wl["pattern"])
        if kind == "plate":
            hits.extend(_plate_hits(conn, job_id, int(wl["id"]), pattern))
        elif kind == "text":
            hits.extend(_text_hits(conn, job_id, int(wl["id"]), pattern))
        elif kind == "person":
            hits.extend(_person_hits(conn, job_id, int(wl["id"]), pattern))
    for h in hits:
        conn.execute(
            "INSERT INTO watchlist_hits (watchlist_id, job_id, event_id, detail) "
            "VALUES (?, ?, ?, ?)",
            (h.watchlist_id, h.job_id, h.event_id, h.detail),
        )
    conn.commit()
    if hits and cfg.watchlist.notify_command is not None:
        _notify(cfg, hits)
    return hits


def _notify(cfg: Config, hits: list[WatchlistHit]) -> None:
    import subprocess

    template = cfg.watchlist.notify_command
    if template is None:
        return
    message = f"{len(hits)} watchlist hit(s): {hits[0].detail} (job {hits[0].job_id})"
    cmd = template.format(
        message=message.replace('"', "'"),
        detail=hits[0].detail.replace('"', "'"),
        job_id=hits[0].job_id,
    )
    try:
        subprocess.run(
            ["/bin/sh", "-c", cmd],
            timeout=10,
            capture_output=True,
            check=False,
        )
    except Exception:
        return
