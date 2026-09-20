from __future__ import annotations

import html
import json
import shutil
import sqlite3
from pathlib import Path

from video_security.fs import spotlight_ignore
from video_security.report_theme import (
    THEME_CSS,
    THEME_JS,
    THEME_TOGGLE_JS,
    event_tone,
)


class ReportError(Exception):
    pass


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _event_description(conn: sqlite3.Connection, evt: sqlite3.Row) -> str:
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


def generate_report(conn: sqlite3.Connection, job_id: int, artifact_dir: Path) -> Path:
    job_row = conn.execute(
        "SELECT id, video_path, status, created_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job_row is None:
        raise ReportError(f"job {job_id} not found")

    out_dir = artifact_dir / "reports"
    spotlight_ignore(out_dir)
    asset_dir = out_dir / f"job_{job_id}_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)

    events = conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY start_sec", (job_id,)
    ).fetchall()

    copied: dict[str, str] = {}
    for evt in events:
        kf_json = evt["keyframes_json"]
        if kf_json == "[]":
            continue
        try:
            paths: list[str] = json.loads(kf_json)
        except (json.JSONDecodeError, TypeError):
            paths = []
        new_paths: list[str] = []
        for p in paths:
            if p in copied:
                new_paths.append(copied[p])
                continue
            src = Path(p)
            if not src.exists():
                new_paths.append(p)
                continue
            dst_name = f"evt{evt['id']}_{src.name}"
            dst = asset_dir / dst_name
            try:
                shutil.copy2(src, dst)
            except OSError:
                new_paths.append(p)
                continue
            rel = f"job_{job_id}_assets/{dst_name}"
            copied[p] = rel
            new_paths.append(rel)
        if new_paths != paths:
            conn.execute(
                "UPDATE events SET keyframes_json = ? WHERE id = ?",
                (json.dumps(new_paths), evt["id"]),
            )
    conn.commit()

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
        "AND (direction IS NOT NULL OR weaving_score IS NOT NULL) "
        "ORDER BY track_id",
        (job_id,),
    ).fetchall()

    frames_kept = conn.execute(
        "SELECT COUNT(*) as cnt FROM frames WHERE job_id = ?", (job_id,)
    ).fetchone()["cnt"] or 0

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
        '<span class="masthead-meta">'
        f"{html.escape(job_row['status'])} &middot; {html.escape(job_row['created_at'])}"
        "</span>",
        '<button id="theme-toggle" class="theme-toggle" type="button" role="switch" '
        'aria-label="Toggle Machine/Samaritan theme">Machine</button>',
        "</header>",
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
    parts.append(
        '<table class="data-table"><tr><th>Start</th><th>End</th><th>Type</th>'
        "<th>Id</th><th class=\"num\">Priority</th><th class=\"num\">Score</th>"
        "<th>Status</th><th>Description</th></tr>"
    )
    for evt in events:
        tone = (
            "suppressed"
            if evt["status"] == "suppressed"
            else event_tone(evt["event_type"])
        )
        description = _event_description(conn, evt)
        parts.append(
            f"<tr class=\"row-{tone}\">"
            f"<td>{_mmss(evt['start_sec'])}</td><td>{_mmss(evt['end_sec'])}</td>"
            f"<td>{html.escape(evt['event_type'])}</td>"
            f"<td>{evt['id']}</td>"
            f"<td class=\"num\">{evt['priority']}</td>"
            f"<td class=\"num\">{evt['detector_score']:.3f}</td>"
            f"<td>{html.escape(evt['status'])}</td>"
            f"<td>{html.escape(description)}</td>"
            f"</tr>"
        )
    parts.append("</table></section>")

    parts.append('<section class="panel">')
    parts.append("<h2>Keyframes</h2>")
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
        description = _event_description(conn, evt)
        tone = event_tone(evt["event_type"])
        designation = f"{evt['event_type']} // {_mmss(evt['start_sec'])}"
        caption = html.escape(
            f"Event {evt['id']}: {evt['event_type']}"
            + (f" — {description}" if description else "")
        )
        parts.append(f'<figure class="subject subject--{tone}">')
        parts.append(
            f'<span class="designation">{html.escape(designation)}</span>'
        )
        for p in paths:
            if "/" in p or "\\" in p:
                parts.append(f"<img src=\"{html.escape(p, quote=False)}\" alt=\"{caption}\">")
        parts.append(f"<figcaption>{caption}</figcaption>")
        parts.append("</figure>")
    parts.append("")

    parts.append('<section class="panel">')
    parts.append("<h2>Plates</h2>")
    if plate_rows:
        for pr in plate_rows:
            parts.append(
                '<span class="chip chip--plate">'
                f"{html.escape(pr['norm_text'] or '')} &middot; raw "
                f"{html.escape(pr['raw_text'] or '')} &middot; conf "
                f"{pr['confidence']:.2f}</span>"
            )
        parts.append("")
        parts.append(
            '<table class="data-table"><tr><th>Norm</th><th>Raw</th>'
            '<th class="num">Confidence</th>'
            "<th class=\"num\">Best Frame</th><th class=\"num\">Track</th></tr>"
        )
        for pr in plate_rows:
            parts.append(
                f"<tr>"
                f"<td>{html.escape(pr['norm_text'] or '')}</td>"
                f"<td>{html.escape(pr['raw_text'] or '')}</td>"
                f"<td class=\"num\">{pr['confidence']}</td>"
                f"<td class=\"num\">{pr['best_frame']}</td>"
                f"<td class=\"num\">{pr['track_id']}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No plates detected.</p>")
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
            '<table class="data-table"><tr><th class="num">Track</th>'
            "<th>First-Last Frame</th>"
            "<th>Direction</th><th class=\"num\">Weaving Score</th></tr>"
        )
        for vt in vehicle_tracks:
            parts.append(
                f"<tr>"
                f"<td class=\"num\">{vt['track_id']}</td>"
                f"<td>{vt['first_frame']}-{vt['last_frame']}</td>"
                f"<td>{html.escape(vt['direction'] or '')}</td>"
                f"<td class=\"num\">{vt['weaving_score']}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No driving events.</p>")
    parts.append("</section>")

    parts.append(
        '<footer class="report-footer">generated by vs report &middot; '
        "local analysis &middot; no data leaves this machine</footer>"
    )
    parts.append(f"<script>{THEME_TOGGLE_JS}</script>")
    parts.append("</div>")
    parts.append("</body></html>")

    html_content = "\n".join(parts)
    report_path = out_dir / f"job_{job_id}.html"
    report_path.write_text(html_content, encoding="utf-8")
    return report_path