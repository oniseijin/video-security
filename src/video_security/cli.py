from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from video_security import db
from video_security.config import Config, ConfigError, load_config
from video_security.db import connect, init_db, list_jobs

if TYPE_CHECKING:
    from video_security.engine import BatchEngine, SignalGuard
    from video_security.llm.ollama import OllamaClient

app = typer.Typer()


def parse_duration(duration_str: str) -> float:
    if not duration_str:
        raise ValueError("Empty duration string")
    m = re.fullmatch(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", duration_str)
    if not m:
        raise ValueError(
            f"Invalid duration format: {duration_str!r}. "
            "Use e.g. '2h', '90m', '45s', '1h30m15s'"
        )
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2)) if m.group(2) else 0
    seconds = int(m.group(3)) if m.group(3) else 0
    if hours == 0 and minutes == 0 and seconds == 0:
        raise ValueError(f"Duration must have at least one component: {duration_str!r}")
    return float(hours * 3600 + minutes * 60 + seconds)


def _format_config_json(config: Config) -> str:
    def _to_dict(obj: object) -> object:
        if hasattr(obj, "__dataclass_fields__"):
            return {
                f.name: _to_dict(getattr(obj, f.name))
                for f in obj.__dataclass_fields__.values()
                if not f.name.startswith("_")
            }
        if isinstance(obj, dict):
            return {str(k): _to_dict(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_to_dict(item) for item in obj]
        return obj

    d = _to_dict(config)
    return json.dumps(d, indent=2, default=str)


def _get_db(ctx: typer.Context) -> tuple[sqlite3.Connection, Config]:
    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)
    return conn, cfg


@app.callback()
def main(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config", help="Path to TOML config file"),  # noqa: B008
    db: Path | None = typer.Option(None, "--db", help="Override SQLite DB path"),  # noqa: B008
) -> None:
    try:
        cfg = load_config(config)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1) from e
    if db:
        cfg.storage.db_path = str(db)
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg


@app.command(name="analyze")
def analyze_cmd(
    ctx: typer.Context,
    video: Path | None = typer.Argument(None, help="Video file or directory"),  # noqa: B008
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM analysis"),  # noqa: B008
    only_llm: bool = typer.Option(False, "--only-llm", help="Re-run LLM on existing prefilter output"),  # noqa: B008, E501
    phases: str = typer.Option("1,2,3", "--phases", help="Pipeline phases to run: 1, 1,2 or 1,2,3"),  # noqa: B008
    resume: bool = typer.Option(False, "--resume", help="Resume incomplete jobs"),  # noqa: B008
    stop_after: str | None = typer.Option(None, "--stop-after", help="Stop after duration"),  # noqa: B008
    watch: Path | None = typer.Option(None, "--watch", help="Monitor directory"),  # noqa: B008
    retention_days: int | None = typer.Option(None, "--retention-days", help="Prune jobs older than N days"),  # noqa: B008, E501
    max_llm_events: int | None = typer.Option(None, "--max-llm-events", help="LLM event budget circuit breaker"),  # noqa: B008, E501
) -> None:
    from video_security.config import Config as Cfg
    from video_security.engine import (
        BatchEngine,
        EngineError,
        SignalGuard,
        on_ac_power,
    )
    from video_security.pipeline import enqueue_video

    cfg: Cfg = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)

    if retention_days is not None:
        pruned = BatchEngine(cfg).prune_old_jobs(retention_days)
        print(f"pruned {pruned} old jobs")

    targets: list[Path] = []
    if video is not None:
        if video.is_dir():
            targets = sorted(
                p for p in video.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"}
            )
        else:
            targets = [video]
        for t in targets:
            if not t.exists():
                print(f"Error: {t} not found", file=sys.stderr)
                raise typer.Exit(code=1)
        for t in targets:
            job, created = enqueue_video(conn, t)
            if not created:
                print(f"skip (already analyzed): {t}")
            else:
                print(f"queued job {job.id}: {t}")
    if resume:
        reclaimed = BatchEngine(cfg).reclaim_stale_jobs()
        if reclaimed:
            print(f"reclaimed {reclaimed} stale jobs")

    if only_llm:
        _rerun_llm_only(conn, cfg, max_llm_events)
        conn.close()
        return

    try:
        phase_list = sorted({int(p.strip()) for p in phases.split(",") if p.strip()})
    except ValueError as e:
        print(f"Error: invalid --phases value: {phases!r} (e.g. 1, 1,2, 1,2,3)", file=sys.stderr)
        raise typer.Exit(code=1) from e
    if not phase_list or any(p not in (1, 2, 3) for p in phase_list):
        print(f"Error: --phases must be 1, 2, and/or 3 (got: {phases!r})", file=sys.stderr)
        raise typer.Exit(code=1)
    if no_llm:
        phase_list = [1]
    needs_llm = any(p in (2, 3) for p in phase_list)

    if watch is not None and watch.is_dir():
        _watch_and_run(watch, conn, cfg, no_llm=no_llm, max_llm_events=max_llm_events)
        conn.close()
        return

    budget_s: float | None = parse_duration(stop_after) if stop_after else None
    started = time.monotonic()

    from video_security.llm.ollama import OllamaClient, OllamaError

    client: OllamaClient | None = None
    if needs_llm:
        client = OllamaClient(timeout_s=cfg.llm_triage.timeout_s)
        try:
            health_models = []
            if 2 in phase_list:
                health_models.append(cfg.llm_triage.model)
            if 3 in phase_list:
                health_models.append(cfg.llm_detail.model)
            client.health_check(health_models)
        except OllamaError as e:
            print(f"Warning: {e} — falling back to --no-llm for this run")
            client = None
            phase_list = [p for p in phase_list if p == 1]
            if not phase_list:
                conn.close()
                return

    engine = BatchEngine(cfg)
    try:
        engine.preflight()
    except EngineError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1) from e
    if not on_ac_power():
        print("Warning: not on AC power — running anyway (overnight runs should be AC)")

    from video_security.engine import CaffeinateGuard, set_watermark_config

    set_watermark_config(cfg.engine.disk_watermark_gb)

    def budget_hit() -> bool:
        return budget_s is not None and time.monotonic() - started >= budget_s

    guard = SignalGuard()
    with guard, CaffeinateGuard():
        _phase_sweeps(
            engine, conn, cfg, phase_list, client, budget_hit, guard
        )
    if client is not None:
        if 2 in phase_list:
            client.unload(cfg.llm_triage.model)
        if 3 in phase_list:
            client.unload(cfg.llm_detail.model)
    conn.close()


def _phase_sweeps(
    engine: BatchEngine,
    conn: sqlite3.Connection,
    cfg: Config,
    phase_list: list[int],
    client: OllamaClient | None,
    budget_hit: Callable[[], bool],
    guard: SignalGuard,
) -> None:
    from video_security.engine import EngineError
    from video_security.llm.ollama import OllamaError
    from video_security.pipeline import (
        PipelineError,
        detail_job,
        harvest_job,
        triage_job,
    )

    ollama_client = client
    for phase in phase_list:
        if guard.stop_requested or budget_hit():
            if guard.stop_requested:
                print("stop requested — finishing")
            else:
                print("time budget reached — stopping")
            break
        print(f"— phase {phase} sweep —")
        processed = 0
        while True:
            if guard.stop_requested:
                print("stop requested — finishing")
                break
            if budget_hit():
                print("time budget reached — stopping")
                break
            claimed = engine.claim_next_job(phase)
            if claimed is None:
                break
            job = claimed
            print(f"job {job.id}: {job.video_path}")
            try:
                if phase == 1:
                    report = harvest_job(job, cfg, conn)
                    print(
                        f"job {job.id} harvested: {report.events} events, "
                        f"{report.plates} plates, {report.kept_frames} frames kept"
                    )
                elif phase == 2:
                    n = triage_job(job, cfg, conn, ollama_client) if ollama_client else 0
                    print(f"job {job.id} triaged: {n} relevant events")
                else:
                    n = detail_job(job, cfg, conn, ollama_client) if ollama_client else 0
                    print(f"job {job.id} detailed: {n} events")
                processed += 1
            except (PipelineError, OllamaError, EngineError) as e:
                print(f"job {job.id} failed: {e}", file=sys.stderr)
                engine.mark_failed(job.id)
        print(f"phase {phase} sweep complete: {processed} jobs processed")


@app.command(name="run")
def run_cmd(
    ctx: typer.Context,
    source: Path | None = typer.Option(None, "--source", help="Import source (card mount or archive) before analysis"),  # noqa: B008, E501
    phases: str = typer.Option("1,2,3", "--phases", help="Pipeline phases to run: 1, 1,2 or 1,2,3"),  # noqa: B008
    stop_after: str | None = typer.Option(None, "--stop-after", help="Stop after duration"),  # noqa: B008
    resume: bool = typer.Option(False, "--resume", help="Resume incomplete jobs"),  # noqa: B008
) -> None:
    from video_security.engine import (
        BatchEngine,
        CaffeinateGuard,
        EngineError,
        SignalGuard,
        on_ac_power,
        set_watermark_config,
    )
    from video_security.llm.ollama import OllamaClient, OllamaError

    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)

    if source is not None:
        from video_security.importer import run_import

        if not source.exists():
            print(f"Error: {source} not found", file=sys.stderr)
            conn.close()
            raise typer.Exit(code=1)
        try:
            report = run_import(source, cfg, conn, "auto")
        except (EngineError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            conn.close()
            raise typer.Exit(code=1) from e
        print(
            f"import: {report.imported} new, {report.skipped} skipped, "
            f"{report.failed} failed"
        )

    try:
        phase_list = sorted({int(p.strip()) for p in phases.split(",") if p.strip()})
    except ValueError as e:
        print(f"Error: invalid --phases value: {phases!r}", file=sys.stderr)
        conn.close()
        raise typer.Exit(code=1) from e
    if not phase_list or any(p not in (1, 2, 3) for p in phase_list):
        print(f"Error: --phases must be 1, 2, and/or 3 (got: {phases!r})", file=sys.stderr)
        conn.close()
        raise typer.Exit(code=1)
    needs_llm = any(p in (2, 3) for p in phase_list)

    engine = BatchEngine(cfg)
    if resume:
        reclaimed = engine.reclaim_stale_jobs()
        if reclaimed:
            print(f"reclaimed {reclaimed} stale jobs")

    before = {
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "plates": conn.execute("SELECT COUNT(*) FROM plates").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
        "done": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'done'"
        ).fetchone()[0],
    }

    budget_s: float | None = parse_duration(stop_after) if stop_after else None
    started = time.monotonic()

    client: OllamaClient | None = None
    if needs_llm:
        client = OllamaClient(timeout_s=cfg.llm_triage.timeout_s)
        try:
            health_models = []
            if 2 in phase_list:
                health_models.append(cfg.llm_triage.model)
            if 3 in phase_list:
                health_models.append(cfg.llm_detail.model)
            client.health_check(health_models)
        except OllamaError as e:
            print(f"Warning: {e} — running harvest-only phases")
            client = None
            phase_list = [p for p in phase_list if p == 1]

    try:
        engine.preflight()
    except EngineError as e:
        print(f"Error: {e}", file=sys.stderr)
        conn.close()
        raise typer.Exit(code=1) from e
    if not on_ac_power():
        print("Warning: not on AC power — running anyway (overnight runs should be AC)")

    set_watermark_config(cfg.engine.disk_watermark_gb)

    def budget_hit() -> bool:
        return budget_s is not None and time.monotonic() - started >= budget_s

    guard = SignalGuard()
    with guard, CaffeinateGuard():
        _phase_sweeps(engine, conn, cfg, phase_list, client, budget_hit, guard)
    if client is not None:
        if 2 in phase_list:
            client.unload(cfg.llm_triage.model)
        if 3 in phase_list:
            client.unload(cfg.llm_detail.model)

    after = {
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "plates": conn.execute("SELECT COUNT(*) FROM plates").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
        "done": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'done'"
        ).fetchone()[0],
    }
    print("— run digest —")
    print(f"jobs done: {before['done']} -> {after['done']}")
    print(
        f"events: +{after['events'] - before['events']}, "
        f"plates: +{after['plates'] - before['plates']}, "
        f"faces: +{after['faces'] - before['faces']}"
    )
    conn.close()


@app.command(name="import")
def import_clips_cmd(
    ctx: typer.Context,
    source: Path = typer.Argument(..., help="Source path (card mount or archive)"),  # noqa: B008
    adapter: str = typer.Option("auto", "--adapter", help="Source adapter (auto|mazda_cx8|gopro|photos|generic)"),  # noqa: B008, E501
    since: str | None = typer.Option(None, "--since", help="Only import assets recorded on/after date (YYYY-MM-DD)"),  # noqa: B008, E501
    album: str | None = typer.Option(None, "--album", help="Only import assets in a specific Photos album"),  # noqa: B008, E501
) -> None:
    from video_security.engine import EngineError
    from video_security.importer import run_import

    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)
    since_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            print(f"Error: invalid --since date {since!r} (expected YYYY-MM-DD)", file=sys.stderr)
            conn.close()
            raise typer.Exit(code=1) from None
    try:
        report = run_import(source, cfg, conn, adapter, since=since_dt, album=album)
    except (EngineError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        conn.close()
        raise typer.Exit(code=1) from e
    print(
        f"imported {report.imported} new clips, "
        f"skipped {report.skipped} (already imported), "
        f"failed {report.failed}"
    )
    if report.skipped_cloud:
        print(f"skipped {report.skipped_cloud} cloud-only (iCloud-evicted) assets")
    conn.close()


@app.command(name="search")
def search_cmd(
    ctx: typer.Context,
    query: str | None = typer.Argument(None, help="FTS5 search query string"),  # noqa: B008
    kind: str | None = typer.Option(None, "--kind", help="Event kind filter"),  # noqa: B008
    date_from: str | None = typer.Option(None, "--from", help="Events recorded on/after date (YYYY-MM-DD)"),  # noqa: B008, E501
    date_to: str | None = typer.Option(None, "--to", help="Events recorded on/before date (YYYY-MM-DD)"),  # noqa: B008, E501
) -> None:
    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)

    if query is not None:
        try:
            rows = db.fts_match(
                conn,
                "SELECT ft.text, j.video_path, f.frame_number "
                "FROM frame_text_fts ft "
                "JOIN frame_text f ON f.id = ft.rowid "
                "JOIN jobs j ON j.id = f.job_id "
                "WHERE frame_text_fts MATCH ? ORDER BY rank LIMIT 50",
                query,
            )
        except sqlite3.OperationalError:
            print("Error: invalid search query", file=sys.stderr)
            conn.close()
            raise typer.Exit(code=1) from None
        print("TEXT MATCHES:")
        for row in rows:
            print(f"  {row['video_path']} frame {row['frame_number']}: {row['text']}")

        try:
            plate_rows = conn.execute(
                "SELECT norm_text, raw_text, confidence, job_id, track_id "
                "FROM plates WHERE norm_text LIKE ? ORDER BY confidence DESC",
                (f"%{query.upper()}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            print("Error: invalid search query", file=sys.stderr)
            conn.close()
            raise typer.Exit(code=1) from None
        print("PLATES:")
        for row in plate_rows:
            print(
                f"  {row['norm_text']} ({row['raw_text']})"
                f" conf={row['confidence']} job={row['job_id']}"
            )
            for evt in db.events_for_plate(conn, row["job_id"], row["track_id"]):
                start_mm = f"{int(evt['start_sec'] // 60)}:{int(evt['start_sec'] % 60):02d}"
                print(
                    f"    -> event {evt['id']} {evt['event_type']} @ {start_mm}"
                    f" ({evt['status']})"
                )
    elif kind is not None:
        if kind == "dangerous":
            event_rows = conn.execute(
                "SELECT id, event_type, start_sec, end_sec, status FROM events "
                "WHERE event_type IN ('weaving','near_miss','hard_brake',"
                "'hard_corner','impact','audio_distress') "
                "ORDER BY start_sec"
            ).fetchall()
            print("DANGEROUS DRIVING EVENTS:")
        else:
            event_rows = conn.execute(
                "SELECT id, event_type, start_sec, end_sec, status FROM events "
                "WHERE event_type = ? ORDER BY start_sec", (kind,)
            ).fetchall()
            print(f"EVENTS ({kind}):")
        for row in event_rows:
            print(
                f"  {row['id']}: {row['event_type']}"
                f" {row['start_sec']}s-{row['end_sec']}s {row['status']}"
            )
    elif date_from is not None or date_to is not None:
        clauses = []
        args: list[str] = []
        if date_from is not None:
            clauses.append("date(j.recording_start_utc) >= ?")
            args.append(date_from)
        if date_to is not None:
            clauses.append("date(j.recording_start_utc) <= ?")
            args.append(date_to)
        if kind is not None:
            clauses.append("e.event_type = ?")
            args.append(kind)
        rows = conn.execute(
            "SELECT e.id, e.event_type, e.start_sec, e.status, "
            "j.recording_start_utc, j.id AS job_id "
            "FROM events e JOIN jobs j ON j.id = e.job_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY j.recording_start_utc, e.id LIMIT 500",
            args,
        ).fetchall()
        print(f"EVENTS {date_from or '...'}..{date_to or '...'}:")
        for row in rows:
            print(
                f"  {row['id']}: {row['event_type']}"
                f" @ {row['recording_start_utc']} job={row['job_id']}"
                f" {row['start_sec']}s ({row['status']})"
            )
    else:
        print("Usage: vs-search <query> OR vs-search --kind <event-type>")
    conn.close()


@app.command(name="diff")
def diff_cmd(
    ctx: typer.Context,
    job_id: int = typer.Argument(..., help="Job id"),
    version_a: str = typer.Argument(..., help="Prompt version A"),
    version_b: str = typer.Argument(..., help="Prompt version B"),
) -> None:
    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)
    rows = conn.execute(
        "SELECT event_id, analysis_type, prompt_version, raw_response "
        "FROM analysis_results WHERE job_id = ? "
        "AND prompt_version IN (?, ?) ORDER BY event_id, id",
        (job_id, version_a, version_b),
    ).fetchall()
    by_event: dict[int, dict[str, str]] = {}
    for r in rows:
        by_event.setdefault(int(r["event_id"]), {})[
            f"{r['analysis_type']}:{r['prompt_version']}"
        ] = r["raw_response"]
    compared = 0
    changed = 0
    for event_id, versions in sorted(by_event.items()):
        a = next((v for k, v in versions.items() if k.endswith(f":{version_a}")), None)
        b = next((v for k, v in versions.items() if k.endswith(f":{version_b}")), None)
        if a is None or b is None:
            continue
        compared += 1
        try:
            da = json.loads(a).get("description", "")
            db_ = json.loads(b).get("description", "")
        except json.JSONDecodeError:
            da, db_ = a, b
        if da != db_:
            changed += 1
            print(f"event {event_id} CHANGED:")
            print(f"  [{version_a}] {da}")
            print(f"  [{version_b}] {db_}")
    print(f"compared {compared} events, {changed} changed")
    conn.close()


@app.command(name="report")
def report_cmd(
    ctx: typer.Context,
    job_id: int = typer.Argument(..., help="Job ID to generate report for"),  # noqa: B008
) -> None:
    from video_security.report import ReportError, generate_report

    cfg: Config = ctx.obj["config"]
    conn = connect(cfg.storage.db_path)
    init_db(conn)
    try:
        path = generate_report(
            conn, job_id, Path(cfg.storage.artifact_dir).expanduser(),
            geocode=cfg.report.reverse_geocode,
            carto_api_key=cfg.map.carto_api_key,
        )
        print(path)
    except ReportError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1) from e
    finally:
        conn.close()


@app.command(name="list")
def list_cmd(ctx: typer.Context) -> None:
    conn, _ = _get_db(ctx)
    try:
        jobs = list_jobs(conn)
        if not jobs:
            print("No jobs found.")
        else:
            for job in jobs:
                print(f"{job.id}\t{job.video_path}\t{job.status}\t{job.created_at}")
    finally:
        conn.close()


@app.command(name="index")
def index_cmd(
    ctx: typer.Context,
    refresh: bool = typer.Option(False, "--refresh", help="Re-embed already indexed events"),
) -> None:
    from video_security.llm.embeddings import (
        EMBED_MODEL,
        embed_events,
        embed_transcripts,
    )
    from video_security.llm.ollama import OllamaClient, OllamaError

    conn, cfg = _get_db(ctx)
    client = OllamaClient(timeout_s=120)
    try:
        count = embed_events(client, EMBED_MODEL, conn, refresh=refresh)
        tcount = embed_transcripts(client, EMBED_MODEL, conn, refresh=refresh)
    except OllamaError as e:
        print(f"Error: {e}", file=sys.stderr)
        conn.close()
        raise typer.Exit(code=1) from e
    print(f"embedded {count} events, {tcount} transcript segments")
    try:
        client.unload(EMBED_MODEL)
    except Exception:
        pass
    conn.close()


@app.command(name="serve")
def serve_cmd(
    ctx: typer.Context,
    port: int | None = typer.Option(None, "--port", help="Web console port"),  # noqa: B008
    host: str | None = typer.Option(None, "--host", help="Bind address"),  # noqa: B008
    open_browser: bool = typer.Option(False, "--open", help="Open the browser"),  # noqa: B008
) -> None:
    from video_security.web.server import serve as web_serve

    cfg: Config = ctx.obj["config"]
    web_serve(cfg, host=host, port=port, open_browser=open_browser)


@app.command(name="backfill-media")
def backfill_media_cmd(
    ctx: typer.Context,
    limit: int | None = typer.Option(None, "--limit", help="Max plates to process"),  # noqa: B008
    detect_faces: bool = typer.Option(False, "--detect-faces", help="Run face detection on keyframes of events without face data"),  # noqa: B008, E501
) -> None:
    from video_security.backfill import backfill_face_crops, backfill_plate_crops

    conn, cfg = _get_db(ctx)
    try:
        report = backfill_plate_crops(conn, cfg, limit=limit)
        print(
            f"plate crops: attempted {report.attempted}, written {report.written}, "
            f"skipped {report.skipped}, failed {report.failed}"
        )
        for failure in report.failures:
            print(f"  {failure}")
        face_report = backfill_face_crops(
            conn, cfg, limit=limit, detect=detect_faces
        )
        print(
            f"face crops: events {face_report.attempted}, written {face_report.written}, "
            f"skipped {face_report.skipped}, failed {face_report.failed}"
        )
        for failure in face_report.failures:
            print(f"  {failure}")
    finally:
        conn.close()


@app.command(name="watch")
def watch_cmd(
    ctx: typer.Context,
    add: bool = typer.Option(False, "--add", help="Add a watchlist entry"),  # noqa: B008
    remove: int | None = typer.Option(None, "--remove", help="Remove watchlist by id"),  # noqa: B008
    kind: str = typer.Option("plate", "--kind", help="plate|text|person"),  # noqa: B008
    pattern: str | None = typer.Option(None, "--pattern", help="Pattern (plate LIKE / FTS query / person id)"),  # noqa: B008, E501
    note: str | None = typer.Option(None, "--note", help="Note attached to hits"),  # noqa: B008
) -> None:
    from video_security.watchlist import (
        add_watchlist,
        list_watchlists,
        remove_watchlist,
    )

    conn, _cfg = _get_db(ctx)
    try:
        if remove is not None:
            ok = remove_watchlist(conn, remove)
            print(f"removed watchlist {remove}" if ok else f"no watchlist {remove}")
            return
        if add:
            if pattern is None:
                print("Error: --pattern is required with --add", file=sys.stderr)
                raise typer.Exit(code=1)
            try:
                wl_id = add_watchlist(conn, kind, pattern, note)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                raise typer.Exit(code=1) from e
            print(f"watchlist {wl_id}: {kind} / {pattern}")
            return
        rows = list_watchlists(conn)
        if not rows:
            print("no watchlists — add with: vs watch --add --kind plate --pattern L1208")
            return
        for r in rows:
            print(
                f"{r['id']:3d} {r['kind']:6s} {r['pattern']:20s} "
                f"hits={r['hits']} {r['note'] or ''}"
            )
    finally:
        conn.close()


@app.command(name="index-faces")
def index_faces_cmd(ctx: typer.Context) -> None:
    from video_security.identity import index_existing_faces

    conn, cfg = _get_db(ctx)
    try:
        if not cfg.identity.enabled:
            print("identity disabled in config — nothing to do")
            return
        registered = index_existing_faces(conn, cfg)
        persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        print(f"face embeddings registered: {registered}")
        print(f"persons clustered: {persons}")
    finally:
        conn.close()


def _rerun_llm_only(
    conn: sqlite3.Connection, cfg: Config, max_llm_events: int | None
) -> None:
    from video_security.llm.ollama import OllamaClient
    from video_security.llm.triage import triage_events
    from video_security.pipeline import detail_job, load_llm_events

    client = OllamaClient(timeout_s=cfg.llm_triage.timeout_s)
    if max_llm_events is not None:
        cfg.engine.max_llm_events = max_llm_events
    rows = conn.execute(
        "SELECT j.id AS job_id FROM jobs j "
        "WHERE j.status IN ('done','harvested') ORDER BY j.id"
    ).fetchall()
    for row in rows:
        job_id = row["job_id"]
        conn.execute(
            "UPDATE events SET status = 'pending' WHERE job_id = ?", (job_id,)
        )
        conn.commit()
        llm_events = load_llm_events(conn, job_id)
        if not llm_events:
            continue
        job = db.get_job_by_id(conn, job_id)
        if job is None:
            continue
        print(f"job {job_id}: re-running LLM on {len(llm_events)} events")
        triaged = triage_events(llm_events, cfg, client)
        for tr in triaged:
            result_id = db.insert_analysis_result(
                conn, job_id, tr.event_id, tr.model_digest, tr.prompt_version,
                "triage", tr.raw_response, None, 0, None,
            )
            status = "triaged" if tr.relevant else "suppressed"
            db.update_event_status(conn, tr.event_id, status, result_id)
        detailed = detail_job(job, cfg, conn, client)
        relevant_n = sum(1 for t in triaged if t.relevant)
        print(f"job {job_id}: {relevant_n} triaged, {detailed} detailed")
    client.unload(cfg.llm_triage.model)
    client.unload(cfg.llm_detail.model)


def _watch_and_run(
    directory: Path,
    conn: sqlite3.Connection,
    cfg: Config,
    no_llm: bool,
    max_llm_events: int | None,
) -> None:
    from video_security.engine import BatchEngine, EngineError, SignalGuard, on_ac_power
    from video_security.llm.ollama import OllamaClient, OllamaError
    from video_security.pipeline import PipelineError, analyze_video, enqueue_video
    from video_security.watch import watch_loop

    if not on_ac_power():
        print("Warning: not on AC power — running anyway (overnight runs should be AC)")
    client: OllamaClient | None = None
    if not no_llm:
        client = OllamaClient(timeout_s=cfg.llm_triage.timeout_s)
        try:
            client.health_check([cfg.llm_triage.model, cfg.llm_detail.model])
        except OllamaError as e:
            print(f"Warning: {e} — falling back to --no-llm for this run")
            client = None
            no_llm = True
    engine = BatchEngine(cfg)
    guard = SignalGuard()

    def on_files(files: list[Path]) -> None:
        for f in files:
            job, created = enqueue_video(conn, f)
            if created:
                print(f"queued job {job.id}: {f}")

    with guard:
        watch_loop(
            directory,
            on_files,
            stop_check=lambda: guard.stop_requested,
            poll_interval_s=30.0,
        )
        while True:
            if guard.stop_requested:
                break
            claimed = engine.claim_next_job()
            if claimed is None:
                break
            try:
                report = analyze_video(
                    claimed, cfg, conn, client=client, no_llm=no_llm,
                    max_llm_events=max_llm_events,
                )
                print(
                    f"job {claimed.id} done: {report.events} events, "
                    f"{report.plates} plates"
                )
            except (PipelineError, OllamaError, EngineError) as e:
                print(f"job {claimed.id} failed: {e}", file=sys.stderr)
                engine.mark_failed(claimed.id)
    if client is not None:
        client.unload(cfg.llm_triage.model)
        client.unload(cfg.llm_detail.model)


@app.command(name="archive")
def archive_cmd(
    ctx: typer.Context,
    days: int
    | None = typer.Option(None, "--days", help="Age threshold in days for selecting jobs"),
    job: list[int] = typer.Option(  # noqa: B008
        [], "--job", help="Specific job IDs to archive/restore"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be done without making changes",
    ),
    restore: bool = typer.Option(
        False, "--restore",
        help="Restore archived originals back to their original paths",
    ),
    delete: bool = typer.Option(
        False, "--delete",
        help="Delete done-job videos outright (no proxy, no cold copy)",
    ),
    deep: bool = typer.Option(
        False, "--deep",
        help="Move proxies of already-archived jobs to cold too (evidence only on hot)",
    ),
) -> None:
    from video_security.archive import run_archive
    from video_security.engine import EngineError

    conn, cfg = _get_db(ctx)
    try:
        report = run_archive(
            conn, cfg,
            days=days,
            job_ids=job if job else None,
            dry_run=dry_run,
            restore=restore,
            delete=delete,
            deep=deep,
        )
        if restore:
            print(f"restored {report.restored} jobs, failed {report.failed}")
        elif delete:
            print(
                f"deleted {report.deleted} videos "
                f"({report.bytes_saved} bytes freed), "
                f"skipped {report.skipped}, failed {report.failed}"
            )
        elif deep:
            print(
                f"deep-archived {report.archived} proxies "
                f"({report.bytes_moved} bytes moved to cold), "
                f"skipped {report.skipped}, failed {report.failed}"
            )
        elif dry_run:
            print(
                f"would archive {report.planned} jobs "
                f"({report.bytes_moved} bytes moved to cold)"
            )
        else:
            print(
                f"archived {report.archived} jobs "
                f"({report.bytes_moved} bytes moved, "
                f"{report.bytes_saved} bytes saved on hot disk), "
                f"skipped {report.skipped}, failed {report.failed}"
            )
        for failure in report.failures:
            print(f"  {failure}")
    except EngineError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1) from e
    finally:
        conn.close()


@app.command(name="config")
def config_cmd(ctx: typer.Context) -> None:
    cfg: Config = ctx.obj["config"]
    print(_format_config_json(cfg))