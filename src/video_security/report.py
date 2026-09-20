from __future__ import annotations

import html
import json
import shutil
import sqlite3
from pathlib import Path

from video_security.fs import spotlight_ignore


class ReportError(Exception):
    pass


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
        "<html lang=\"en\">",
        "<head><meta charset=\"utf-8\">",
        "<title>Video Security Report</title>",
        "<style>",
        "body { font-family: -apple-system, sans-serif; margin: 2em; }",
        "h1 { font-size: 1.5em; }",
        "h2 { font-size: 1.2em; margin-top: 1.5em; }",
        "table { border-collapse: collapse; width: 100%; margin-bottom: 1em; }",
        "th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }",
        "th { background: #f5f5f5; }",
        "img { max-width: 400px; height: auto; margin: 4px; border: 1px solid #eee; }",
        "</style></head><body>",
    ]

    parts.append(f"<h1>Job {job_row['id']} Report</h1>")
    parts.append(
        f"<p>Video: {html.escape(job_row['video_path'])}<br>"
        f"Status: {html.escape(job_row['status'])}<br>"
        f"Created: {html.escape(job_row['created_at'])}</p>"
    )

    parts.append("<h2>Summary</h2>")
    parts.append("<table><tr><th>Metric</th><th>Count</th></tr>")
    for et, cnt in sorted(event_types.items()):
        parts.append(
            f"<tr><td>Events: {html.escape(et)}</td><td>{cnt}</td></tr>"
        )
    parts.append(f"<tr><td>Plates</td><td>{len(plate_rows)}</td></tr>")
    parts.append(f"<tr><td>Transcript segments</td><td>{len(transcript_rows)}</td></tr>")
    parts.append(f"<tr><td>Frames kept</td><td>{frames_kept}</td></tr>")
    for st, cnt in sorted(status_counts.items()):
        parts.append(
            f"<tr><td>Status: {html.escape(st)}</td><td>{cnt}</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Timeline</h2>")
    parts.append(
        "<table><tr><th>Start</th><th>End</th><th>Type</th>"
        "<th>Priority</th><th>Score</th><th>Status</th><th>Description</th></tr>"
    )
    for evt in events:
        start_mm = f"{int(evt['start_sec'] // 60)}:{int(evt['start_sec'] % 60):02d}"
        end_mm = f"{int(evt['end_sec'] // 60)}:{int(evt['end_sec'] % 60):02d}"
        description = ""
        if evt["llm_result_id"] is not None:
            ar = conn.execute(
                "SELECT analysis_type, raw_response FROM analysis_results WHERE id = ?",
                (evt["llm_result_id"],),
            ).fetchone()
            if ar is not None:
                try:
                    resp = json.loads(ar["raw_response"])
                    description = resp.get("description", "")
                except (json.JSONDecodeError, TypeError):
                    description = ""
        parts.append(
            f"<tr>"
            f"<td>{start_mm}</td><td>{end_mm}</td>"
            f"<td>{html.escape(evt['event_type'])}</td>"
            f"<td>{evt['priority']}</td>"
            f"<td>{evt['detector_score']:.3f}</td>"
            f"<td>{html.escape(evt['status'])}</td>"
            f"<td>{html.escape(description)}</td>"
            f"</tr>"
        )
    parts.append("</table>")

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
        description = ""
        if evt["llm_result_id"] is not None:
            ar = conn.execute(
                "SELECT raw_response FROM analysis_results WHERE id = ?",
                (evt["llm_result_id"],),
            ).fetchone()
            if ar is not None:
                try:
                    resp = json.loads(ar["raw_response"])
                    description = resp.get("description", "")
                except (json.JSONDecodeError, TypeError):
                    description = ""
        caption = html.escape(
            f"Event {evt['id']}: {evt['event_type']}"
            + (f" — {description}" if description else "")
        )
        parts.append(f"<p>{caption}</p>")
        for p in paths:
            if "/" in p or "\\" in p:
                parts.append(f"<img src=\"{html.escape(p, quote=False)}\" alt=\"{caption}\">")
    parts.append("")

    parts.append("<h2>Plates</h2>")
    if plate_rows:
        parts.append(
            "<table><tr><th>Norm</th><th>Raw</th><th>Confidence</th>"
            "<th>Votes</th><th>Best Frame</th><th>Track</th></tr>"
        )
        for pr in plate_rows:
            parts.append(
                f"<tr>"
                f"<td>{html.escape(pr['norm_text'] or '')}</td>"
                f"<td>{html.escape(pr['raw_text'] or '')}</td>"
                f"<td>{pr['confidence']}</td>"
                f"<td>{html.escape(pr['ocr_votes_json'] or '')}</td>"
                f"<td>{pr['best_frame']}</td>"
                f"<td>{pr['track_id']}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No plates detected.</p>")

    parts.append("<h2>Transcript</h2>")
    if transcript_rows:
        for seg in transcript_rows:
            start = seg["start_time"]
            end = seg["end_time"]
            start_fmt = f"{int(start // 60)}:{int(start % 60):02d}"
            end_fmt = f"{int(end // 60)}:{int(end % 60):02d}"
            parts.append(
                f"<p>[{html.escape(start_fmt)} - {html.escape(end_fmt)}] "
                f"{html.escape(seg['text'])}</p>"
            )
    else:
        parts.append("<p>No transcript segments.</p>")

    parts.append("<h2>Driving Log</h2>")
    if vehicle_tracks:
        parts.append(
            "<table><tr><th>Track</th><th>First-Last Frame</th>"
            "<th>Direction</th><th>Weaving Score</th></tr>"
        )
        for vt in vehicle_tracks:
            parts.append(
                f"<tr>"
                f"<td>{vt['track_id']}</td>"
                f"<td>{vt['first_frame']}-{vt['last_frame']}</td>"
                f"<td>{html.escape(vt['direction'] or '')}</td>"
                f"<td>{vt['weaving_score']}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No driving events.</p>")

    parts.append("</body></html>")

    html_content = "\n".join(parts)
    report_path = out_dir / f"job_{job_id}.html"
    report_path.write_text(html_content, encoding="utf-8")
    return report_path