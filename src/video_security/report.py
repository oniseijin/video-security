from __future__ import annotations

import html
import json
import math
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from video_security import db, geo
from video_security.enrich import (
    clip_mode,
    event_category,
    event_description,
    format_coord,
    gps_track,
    nearest_gps,
    scene_description,
)
from video_security.fs import spotlight_ignore
from video_security.jp_plates import ken_for_plate
from video_security.report_theme import (
    THEME_CSS,
    THEME_FACES_JS,
    THEME_JS,
    THEME_LIGHTBOX_JS,
    THEME_MAP_JS,
    THEME_TOGGLE_JS,
    event_tone,
)


class ReportError(Exception):
    pass


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _keyframe_src(path: str, reports_dir: Path) -> str:
    src = Path(path)
    if src.is_absolute():
        if not src.exists():
            return path
        return os.path.relpath(src, reports_dir)
    return path


def _event_faces(evt: sqlite3.Row) -> list[list[list[float]]]:
    if not evt["faces_json"]:
        return []
    try:
        parsed = json.loads(evt["faces_json"])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def _gps_svg(
    track: list[sqlite3.Row],
    events: list[sqlite3.Row],
) -> str:
    if len(track) < 2:
        return ""
    lats = [float(r["lat"]) for r in track]
    lons = [float(r["lon"]) for r in track]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    if min_lat == max_lat and min_lon == max_lon:
        return ""
    mean_lat_rad = math.radians((min_lat + max_lat) / 2)
    cos_lat = abs(math.cos(mean_lat_rad))
    w = 640.0
    h = 240.0
    span_x = max((max_lon - min_lon) * cos_lat, 1e-9)
    span_y = max(max_lat - min_lat, 1e-9)
    scale = min((w - 40) / span_x, (h - 40) / span_y)
    ox = (w - span_x * scale) / 2
    oy = (h - span_y * scale) / 2

    def px(lon: float) -> float:
        return ox + (lon - min_lon) * cos_lat * scale

    def py(lat: float) -> float:
        return h - (oy + (lat - min_lat) * scale)

    pts = " ".join(f"{px(r['lon']):.1f},{py(r['lat']):.1f}" for r in track)
    parts = [
        f'<svg class="gps-svg" viewBox="0 0 {w:.0f} {h:.0f}" '
        'role="img" aria-label="GPS track">',
        f'<polyline points="{pts}" fill="none" stroke="currentColor" '
        'stroke-width="1" opacity="0.45"/>',
    ]
    first, last = track[0], track[-1]
    x0, y0 = px(first["lon"]) - 3, py(first["lat"]) - 3
    x1, y1 = px(last["lon"]) - 3, py(last["lat"]) - 3
    parts.append(
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="6" height="6" '
        'fill="none" stroke="currentColor"/>'
    )
    parts.append(
        f'<rect x="{x1:.1f}" y="{y1:.1f}" width="6" height="6" '
        'stroke="none" fill="currentColor"/>'
    )
    for evt in events:
        gps = nearest_gps(track, float(evt["start_sec"]))
        if gps is None:
            continue
        tone = event_tone(evt["event_type"])
        cx, cy = px(gps["lon"]), py(gps["lat"])
        title = (
            f"<title>{html.escape(evt['event_type'])} @ "
            f"{_mmss(evt['start_sec'])} — {html.escape(format_coord(gps))}"
            "</title>"
        )
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" '
            f'class="gps-dot gps-dot--{tone}">{title}</circle>'
        )
    parts.append("</svg>")
    caption = (
        f"bounds {min_lat:.5f}&ndash;{max_lat:.5f} N, "
        f"{min_lon:.5f}&ndash;{max_lon:.5f} E &middot; "
        "start square &middot; end filled &middot; markers = events"
    )
    return "".join(parts) + f'<p class="note">{caption}</p>'


def render_report_html(
    conn: sqlite3.Connection,
    job_id: int,
    artifact_dir: Path,
    *,
    geocode: bool = False,
    geocode_network: bool = True,
    media_base: str | None = None,
    embed: bool = False,
    carto_api_key: str | None = None,
) -> str:
    job_row = conn.execute(
        "SELECT id, video_path, status, created_at, recording_start_utc "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job_row is None:
        raise ReportError(f"job {job_id} not found")

    out_dir = artifact_dir / "reports"

    events = conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY start_sec", (job_id,)
    ).fetchall()

    event_types: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for evt in events:
        event_types[evt["event_type"]] = event_types.get(evt["event_type"], 0) + 1
        status_counts[evt["status"]] = status_counts.get(evt["status"], 0) + 1

    plate_rows = conn.execute(
        "SELECT * FROM plates WHERE job_id = ?", (job_id,)
    ).fetchall()

    transcript_rows = conn.execute(
        "SELECT * FROM transcript_segments WHERE job_id = ? ORDER BY start_time",
        (job_id,),
    ).fetchall()

    vehicle_tracks = conn.execute(
        "SELECT * FROM vehicle_tracks WHERE job_id = ? "
        "AND class_id IN (2,3,5,7) "
        "AND (direction IS NOT NULL OR weaving_score IS NOT NULL) "
        "ORDER BY track_id",
        (job_id,),
    ).fetchall()

    frames_kept = conn.execute(
        "SELECT COUNT(*) as cnt FROM frames WHERE job_id = ?", (job_id,)
    ).fetchone()["cnt"] or 0

    has_faces = any(any(f for f in _event_faces(evt)) for evt in events)
    face_button = ""
    if has_faces and not embed:
        face_button = (
            '<button id="face-toggle" class="face-toggle" type="button" '
            'aria-pressed="true">Faces On</button>'
        )

    recorded_dt = _parse_utc(job_row["recording_start_utc"])
    imported_dt = _parse_utc(str(job_row["created_at"]))
    meta_bits = [html.escape(job_row["status"])]
    if recorded_dt is not None:
        meta_bits.append(f"recorded {recorded_dt:%Y-%m-%d %H:%M} UTC")
    if imported_dt is not None:
        meta_bits.append(f"imported {imported_dt:%Y-%m-%d}")
    meta_line = " &middot; ".join(meta_bits)
    archive_flag = ""
    if (
        recorded_dt is not None
        and imported_dt is not None
        and imported_dt - recorded_dt > timedelta(days=1)
    ):
        days = (imported_dt - recorded_dt).days
        archive_flag = (
            '<p class="flag">Archive import &middot; recorded '
            f"{recorded_dt:%Y-%m-%d} &middot; imported {imported_dt:%Y-%m-%d}"
            f" &middot; {days} days later</p>"
        )

    theme_toggle_button = ""
    if not embed:
        theme_toggle_button = (
            '<button id="theme-toggle" class="theme-toggle" type="button" role="switch" '
            'aria-label="Toggle Machine/Samaritan theme">Machine</button>'
        )

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en" data-theme="machine">',
        "<head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>VS // Job {job_row['id']}</title>",
        f"<script>{THEME_JS}</script>",
        f"<style>{THEME_CSS}</style>",
        '</head><body class="vs-report">',
        '<div class="wrap">',
        '<header class="masthead">',
        '<span class="rec-dot" aria-hidden="true"></span>',
        f'<span class="masthead-title">Video Surveillance // Job {job_row["id"]}</span>',
        f'<span class="masthead-meta">{meta_line}</span>',
        theme_toggle_button,
        face_button,
        "</header>",
        archive_flag,
        f'<p class="filepath">{html.escape(job_row["video_path"])}</p>',
    ]

    parts.append('<section class="panel">')
    parts.append("<h2>Summary</h2>")
    parts.append(
        '<table class="data-table">'
        "<tr><th>Metric</th><th class=\"num\">Count</th></tr>"
    )
    for et, cnt in sorted(event_types.items()):
        parts.append(
            f"<tr><td>Events: {html.escape(et)}</td><td class=\"num\">{cnt}</td></tr>"
        )
    parts.append(f"<tr><td>Plates</td><td class=\"num\">{len(plate_rows)}</td></tr>")
    parts.append(
        "<tr><td>Transcript segments</td>"
        f"<td class=\"num\">{len(transcript_rows)}</td></tr>"
    )
    parts.append(f"<tr><td>Frames kept</td><td class=\"num\">{frames_kept}</td></tr>")
    for st, cnt in sorted(status_counts.items()):
        parts.append(
            f"<tr><td>Status: {html.escape(st)}</td><td class=\"num\">{cnt}</td></tr>"
        )
    parts.append("</table></section>")

    parts.append('<section class="panel">')
    parts.append("<h2>Timeline</h2>")
    track = gps_track(conn, job_id)
    mode = clip_mode(str(job_row["video_path"]))
    plates_by_track: dict[int, list[str]] = {}
    for pr in plate_rows:
        plates_by_track.setdefault(int(pr["track_id"]), []).append(
            str(pr["norm_text"] or "")
        )
    event_meta: dict[int, tuple[str, str]] = {}
    for evt in events:
        gps = nearest_gps(track, float(evt["start_sec"])) if track else None
        category = event_category(mode, gps)
        description = event_description(conn, evt)
        if not description:
            face_count = sum(len(f) for f in _event_faces(evt))
            plate_texts = (
                plates_by_track.get(int(evt["track_id"]), [])
                if evt["track_id"] is not None
                else []
            )
            description = scene_description(evt, gps, category, face_count, plate_texts)
        event_meta[int(evt["id"])] = (description, category)
    captured_ids: set[int] = set()
    for evt in events:
        kf_json = evt["keyframes_json"]
        try:
            kf_paths = json.loads(kf_json) if kf_json else []
        except (json.JSONDecodeError, TypeError):
            kf_paths = []
        if any("/" in p or "\\" in p for p in kf_paths):
            captured_ids.add(int(evt["id"]))
    head_cells = ["<th>Start</th>", "<th>End</th>"]
    if recorded_dt is not None:
        head_cells.append("<th>Recorded</th>")
    head_cells.append("<th>Type</th>")
    head_cells.append("<th>Category</th>")
    head_cells.append("<th>Id</th>")
    head_cells.append('<th class="num">Priority</th>')
    head_cells.append('<th class="num">Score</th>')
    head_cells.append("<th>Status</th>")
    if track:
        head_cells.append("<th>Location</th>")
    head_cells.append("<th>Description</th>")
    parts.append(
        '<table class="data-table"><tr>' + "".join(head_cells) + "</tr>"
    )
    for evt in events:
        tone = (
            "suppressed"
            if evt["status"] == "suppressed"
            else event_tone(evt["event_type"])
        )
        description, category = event_meta.get(
            int(evt["id"]), ("", "unknown")
        )
        recorded_cell = ""
        if recorded_dt is not None:
            at = recorded_dt + timedelta(seconds=float(evt["start_sec"]))
            recorded_cell = f"<td>{at:%Y-%m-%d %H:%M:%S}</td>"
        gps = nearest_gps(track, float(evt["start_sec"])) if track else None
        location_cell = ""
        if track:
            if gps is None:
                location_cell = "<td></td>"
            else:
                coord = format_coord(gps)
                label = coord
                if geocode and gps["lat"] is not None and gps["lon"] is not None:
                    if geocode_network:
                        place = geo.reverse_geocode(
                            conn, float(gps["lat"]), float(gps["lon"])
                        )
                    else:
                        place = geo.cached_description(
                            conn, float(gps["lat"]), float(gps["lon"])
                        )
                    if place:
                        label = place
                location_cell = (
                    f'<td title="{html.escape(coord)}">'
                    f"{html.escape(label)}</td>"
                )
        id_cell = (
            f'<a href="#event-{evt["id"]}" title="view capture">'
            f"{evt['id']}</a>"
            if int(evt["id"]) in captured_ids
            else str(evt["id"])
        )
        parts.append(
            f"<tr class=\"row-{tone}\">"
            f"<td>{_mmss(evt['start_sec'])}</td><td>{_mmss(evt['end_sec'])}</td>"
            f"{recorded_cell}"
            f"<td>{html.escape(evt['event_type'])}</td>"
            f"<td>{html.escape(category)}</td>"
            f"<td>{id_cell}</td>"
            f"<td class=\"num\">{evt['priority']}</td>"
            f"<td class=\"num\">{evt['detector_score']:.3f}</td>"
            f"<td>{html.escape(evt['status'])}</td>"
            f"{location_cell}"
            f"<td>{html.escape(description)}</td>"
            f"</tr>"
        )
    parts.append("</table></section>")

    gps_map = _gps_svg(track, events)
    if track:
        parts.append('<section class="panel">')
        parts.append("<h2>Location Track</h2>")
        map_events = []
        for evt in events:
            gps = nearest_gps(track, float(evt["start_sec"]))
            if gps is None:
                continue
            map_events.append(
                {
                    "lat": float(gps["lat"]),
                    "lon": float(gps["lon"]),
                    "type": str(evt["event_type"]),
                    "time": _mmss(evt["start_sec"]),
                    "tone": event_tone(evt["event_type"]),
                }
            )
        payload = json.dumps(
            {
                "points": [
                    {
                        "lat": float(r["lat"]),
                        "lon": float(r["lon"]),
                    }
                    for r in track
                ],
                "events": map_events,
            }
        )
        key_attr = (
            f' data-carto-key="{html.escape(carto_api_key, quote=True)}"'
            if carto_api_key
            else ""
        )
        parts.append(f'<div id="gps-map" class="gps-map"{key_attr}></div>')
        parts.append(
            f'<script type="application/json" id="gps-data">{payload}</script>'
        )
        parts.append(
            '<link rel="stylesheet" '
            'href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
        )
        parts.append(
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js">'
            "</script>"
        )
        parts.append(gps_map)
        parts.append('<p class="note gps-fallback-note">SVG fallback —'
            " map tiles unavailable (offline)</p>")
        parts.append(f"<script>{THEME_MAP_JS}</script>")
        if geocode and len(track) >= 2:
            first, last = track[0], track[-1]
            if geocode_network:
                start_place = geo.reverse_geocode(
                    conn, float(first["lat"]), float(first["lon"])
                )
                end_place = geo.reverse_geocode(
                    conn, float(last["lat"]), float(last["lon"])
                )
            else:
                start_place = geo.cached_description(
                    conn, float(first["lat"]), float(first["lon"])
                )
                end_place = geo.cached_description(
                    conn, float(last["lat"]), float(last["lon"])
                )
            if start_place or end_place:
                parts.append(
                    '<p class="note">'
                    f"start: {html.escape(start_place or '—')} &middot; "
                    f"end: {html.escape(end_place or '—')}</p>"
                )
        parts.append("</section>")

    parts.append('<section class="panel">')
    parts.append("<h2>Keyframes</h2>")
    fps = db.clip_fps(conn, job_id)
    for evt in events:
        kf_json = evt["keyframes_json"]
        if kf_json == "[]":
            continue
        try:
            paths = json.loads(kf_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not paths:
            continue
        description, _category = event_meta.get(
            int(evt["id"]), (event_description(conn, evt), "unknown")
        )
        tone = event_tone(evt["event_type"])
        faces = _event_faces(evt)
        face_count = sum(len(f) for f in faces)
        designation = f"{evt['event_type']} // {_mmss(evt['start_sec'])}"
        if face_count:
            designation += f" // {face_count} face" + ("s" if face_count != 1 else "")
        caption = html.escape(
            f"Event {evt['id']}: {evt['event_type']}"
            + (f" — {description}" if description else "")
        )
        parts.append(f'<figure class="subject subject--{tone}" id="event-{evt["id"]}">')
        parts.append(
            f'<span class="designation">{html.escape(designation)}</span>'
        )
        for i, p in enumerate(paths):
            if "/" not in p and "\\" not in p:
                continue
            if media_base is not None:
                try:
                    rel = os.path.relpath(p, artifact_dir)
                    src = f"{media_base}/{rel}"
                except ValueError:
                    src = _keyframe_src(p, out_dir)
            else:
                src = _keyframe_src(p, out_dir)
            boxes = faces[i] if i < len(faces) else []
            if boxes:
                parts.append('<div class="kf-wrap">')
                parts.append(
                    f"<img src=\"{html.escape(src, quote=False)}\" alt=\"{caption}\">"
                )
                for box in boxes:
                    x, y, w, h = (
                        float(box[0]),
                        float(box[1]),
                        float(box[2]),
                        float(box[3]),
                    )
                    parts.append(
                        '<span class="face-box" style="'
                        f"left: {x * 100:.1f}%; top: {y * 100:.1f}%; "
                        f"width: {w * 100:.1f}%; height: {h * 100:.1f}%;"
                        '"></span>'
                    )
                parts.append("</div>")
            else:
                parts.append(
                    f"<img src=\"{html.escape(src, quote=False)}\" alt=\"{caption}\">"
                )
        parts.append(f"<figcaption>{caption}</figcaption>")
        parts.append("</figure>")
    parts.append("")

    parts.append('<section class="panel">')
    parts.append("<h2>Plates</h2>")
    if plate_rows:
        for pr in plate_rows:
            ken = ken_for_plate(pr["raw_text"] or "") or ken_for_plate(
                pr["norm_text"] or ""
            )
            ken_str = (
                f" &middot; {html.escape(ken[0])} ({html.escape(ken[1])})"
                if ken
                else ""
            )
            target = db.nearest_event_to_seconds(
                conn, job_id, pr["track_id"], (pr["best_frame"] or 0) / fps
            )
            chip_body = (
                '<span class="chip chip--plate">'
                f"{html.escape(pr['norm_text'] or '')} &middot; raw "
                f"{html.escape(pr['raw_text'] or '')} &middot; conf "
                f"{pr['confidence']:.2f}{ken_str}</span>"
            )
            if target is not None:
                parts.append(
                    f'<a href="#event-{target["id"]}">{chip_body}</a>'
                )
            else:
                parts.append(chip_body)
        parts.append("")
        parts.append(
            '<table class="data-table"><tr><th>Plate</th><th>Raw</th>'
            "<th>Ken</th>"
            '<th class="num">Confidence</th>'
            "<th class=\"num\">Read At</th><th class=\"num\">Vehicle</th></tr>"
        )
        for pr in plate_rows:
            ken = ken_for_plate(pr["raw_text"] or "") or ken_for_plate(
                pr["norm_text"] or ""
            )
            ken_cell = f"{ken[0]} ({ken[1]})" if ken else ""
            target = db.nearest_event_to_seconds(
                conn, job_id, pr["track_id"], (pr["best_frame"] or 0) / fps
            )
            read_at = _mmss((pr["best_frame"] or 0) / fps) if fps else "—"
            norm_cell = (
                f'<a href="#event-{target["id"]}" title="jump to event">'
                f"{html.escape(pr['norm_text'] or '')}</a>"
                if target is not None
                else html.escape(pr["norm_text"] or "")
            )
            parts.append(
                f"<tr>"
                f"<td>{norm_cell}</td>"
                f"<td>{html.escape(pr['raw_text'] or '')}</td>"
                f"<td>{html.escape(ken_cell)}</td>"
                f"<td class=\"num\">{pr['confidence']}</td>"
                f"<td class=\"num\" title=\"frame {pr['best_frame']}\">{read_at}</td>"
                f"<td class=\"num\">#{pr['track_id']}</td>"
                f"</tr>"
            )
        parts.append("</table>")
        parts.append(
            '<p class="note">Read At = timestamp of the clearest OCR read '
            '(hover for frame number) &middot; Vehicle = tracked vehicle ID '
            "within the clip &middot; click a plate to jump to its event</p>"
        )
    else:
        parts.append("<p>No plates detected.</p>")
    parts.append("</section>")

    hits_rows = conn.execute(
        "SELECT h.detail, h.event_id, w.kind, w.pattern, w.note "
        "FROM watchlist_hits h JOIN watchlists w ON w.id = h.watchlist_id "
        "WHERE h.job_id = ? ORDER BY h.id",
        (job_id,),
    ).fetchall()
    if hits_rows:
        parts.append('<section class="panel threat">')
        parts.append("<h2>Watchlist</h2>")
        parts.append('<table class="kv">')
        parts.append("<tr><th>Match</th><th>Kind</th><th>Pattern</th><th>Note</th></tr>")
        for h in hits_rows:
            event_link = (
                f'<a href="#ev-{h["event_id"]}">event {h["event_id"]}</a>'
                if h["event_id"]
                else ""
            )
            parts.append(
                f"<tr><td>{html.escape(str(h['detail']))} {event_link}</td>"
                f"<td>{html.escape(str(h['kind']))}</td>"
                f"<td>{html.escape(str(h['pattern']))}</td>"
                f"<td>{html.escape(str(h['note'] or ''))}</td></tr>"
            )
        parts.append("</table>")
        parts.append("</section>")

    parts.append('<section class="panel">')
    parts.append("<h2>Transcript</h2>")
    if transcript_rows:
        parts.append('<div class="terminal">')
        for seg in transcript_rows:
            start_fmt = _mmss(seg["start_time"])
            end_fmt = _mmss(seg["end_time"])
            parts.append(
                '<p><span class="prompt">></span> '
                f'<span class="ts">[{html.escape(start_fmt)} - '
                f"{html.escape(end_fmt)}]</span> "
                f"{html.escape(seg['text'])}</p>"
            )
        parts.append("</div>")
    else:
        parts.append("<p>No transcript segments.</p>")
    parts.append("</section>")

    parts.append('<section class="panel">')
    parts.append("<h2>Driving Log</h2>")
    if vehicle_tracks:
        parts.append(
            '<table class="data-table"><tr><th class="num">Vehicle</th>'
            "<th>Active</th>"
            "<th>Direction</th><th class=\"num\">Weaving Score</th></tr>"
        )
        for vt in vehicle_tracks:
            linked = db.events_for_plate(conn, job_id, int(vt["track_id"]))
            first_s = int(vt["first_frame"]) / fps
            last_s = int(vt["last_frame"]) / fps
            if not linked:
                linked = [
                    e
                    for e in events
                    if float(e["start_sec"]) <= last_s and float(e["end_sec"]) >= first_s
                ]
            active = f"{_mmss(first_s)}&ndash;{_mmss(last_s)}"
            if linked:
                target = linked[0]
                veh_cell = (
                    f'<a href="#event-{target["id"]}" '
                    f'title="frames {vt["first_frame"]}-{vt["last_frame"]}">'
                    f"#{vt['track_id']}</a>"
                )
            else:
                veh_cell = (
                    f'<span title="frames {vt["first_frame"]}-{vt["last_frame"]}">'
                    f"#{vt['track_id']}</span>"
                )
            weaving = f"{vt['weaving_score']:.2f}" if vt["weaving_score"] is not None else "—"
            parts.append(
                f"<tr>"
                f'<td class="num">{veh_cell}</td>'
                f'<td class="num">{active}</td>'
                f"<td>{html.escape(vt['direction'] or '—')}</td>"
                f"<td class=\"num\">{weaving}</td>"
                f"</tr>"
            )
        parts.append("</table>")
        parts.append(
            '<p class="note">Vehicle = tracked vehicle ID (hover for frame span)'
            " &middot; click to jump to its first event &middot; "
            "Active = when the vehicle was in view</p>"
        )
    else:
        parts.append("<p>No driving events.</p>")
    parts.append("</section>")

    parts.append(
        '<footer class="report-footer">generated by vs report &middot; '
        "local analysis &middot; no data leaves this machine</footer>"
    )
    if not embed:
        parts.append(f"<script>{THEME_TOGGLE_JS}</script>")
        if has_faces:
            parts.append(f"<script>{THEME_FACES_JS}</script>")
    parts.append(
        '<div id="lightbox" class="lightbox" aria-hidden="true">'
        '<div class="lightbox-frame">'
        '<div class="lightbox-zoom"><img alt=""></div>'
        '<div class="lightbox-controls">'
        '<button type="button" data-zoom="out" aria-label="Zoom out">-</button>'
        '<span class="lb-level">100%</span>'
        '<button type="button" data-zoom="in" aria-label="Zoom in">+</button>'
        '<button type="button" data-zoom="reset" aria-label="Reset zoom">Reset</button>'
        '</div>'
        '<p class="lightbox-caption"></p>'
        '</div></div>'
    )
    parts.append(f"<script>{THEME_LIGHTBOX_JS}</script>")
    parts.append("</div>")
    parts.append("</body></html>")

    return "\n".join(parts)


def generate_report(
    conn: sqlite3.Connection,
    job_id: int,
    artifact_dir: Path,
    geocode: bool = False,
    carto_api_key: str | None = None,
) -> Path:
    out_dir = artifact_dir / "reports"
    spotlight_ignore(out_dir)
    html_content = render_report_html(
        conn,
        job_id,
        artifact_dir,
        geocode=geocode,
        carto_api_key=carto_api_key,
    )
    report_path = out_dir / f"job_{job_id}.html"
    report_path.write_text(html_content, encoding="utf-8")
    return report_path