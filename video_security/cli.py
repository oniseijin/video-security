from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import typer

from video_security.config import Config, ConfigError, load_config
from video_security.db import connect, init_db, list_jobs

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
    video: Path | None = typer.Argument(None, help="Video file or directory"),  # noqa: B008
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM analysis"),  # noqa: B008
    only_llm: bool = typer.Option(False, "--only-llm", help="Re-run LLM on existing prefilter output"),  # noqa: B008, E501
    resume: bool = typer.Option(False, "--resume", help="Resume incomplete jobs"),  # noqa: B008
    stop_after: str | None = typer.Option(None, "--stop-after", help="Stop after duration"),  # noqa: B008
    watch: Path | None = typer.Option(None, "--watch", help="Monitor directory"),  # noqa: B008
    retention_days: int | None = typer.Option(None, "--retention-days", help="Prune jobs older than N days"),  # noqa: B008, E501
    max_llm_events: int | None = typer.Option(None, "--max-llm-events", help="LLM event budget circuit breaker"),  # noqa: B008, E501
) -> None:
    print("analyze: not implemented")


@app.command(name="import")
def import_clips_cmd(
    source: Path = typer.Argument(..., help="Source path (card mount or archive)"),  # noqa: B008
) -> None:
    print("import: not implemented")


@app.command(name="search")
def search_cmd(
    query: str | None = typer.Argument(None, help="FTS5 search query string"),  # noqa: B008
    kind: str | None = typer.Option(None, "--kind", help="Event kind filter"),  # noqa: B008
) -> None:
    print("search: not implemented")


@app.command(name="report")
def report_cmd(
    job_id: int = typer.Argument(..., help="Job ID to generate report for"),  # noqa: B008
) -> None:
    print("report: not implemented")


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


@app.command(name="config")
def config_cmd(ctx: typer.Context) -> None:
    cfg: Config = ctx.obj["config"]
    print(_format_config_json(cfg))