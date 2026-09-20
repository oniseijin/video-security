# Video Security Analyzer

A local, resumable CLI for overnight security camera / dashcam footage analysis on Apple Silicon. Cheap-first pipeline: dedup → CV prefilter → local LLM triage → detail.

## Install

Two ways:

**Installer (self-contained, recommended for daily use):**

```bash
./install.sh                    # installs to ~/.local/opt/video-security
./install.sh --artifact-dir /Volumes/x/vs   # configure for another output volume
```

Creates a venv snapshot at `~/.local/opt/video-security`, moves the catalog to `~/.local/opt/video-security/var/db` (existing `~/.video-security` DB is copied, original kept), writes `var/config.toml` pointing at the configured artifact dir, and puts `vs`, `vs-analyze`, `vs-import`, `vs-search`, `vs-report`, `vs-list` wrappers on `~/.local/bin` — all pre-configured. `vs-dev` runs the workspace copy instead. Re-run `install.sh` to upgrade (var/ is kept); `--uninstall [--purge]` removes.

**Manual (dev):**

Requires Python 3.12+, ffmpeg on PATH, [Ollama](https://ollama.com) running locally:

```bash
ollama pull gemma3:4b
ollama pull gemma4:12b
python3.12 -m venv .venv && .venv/bin/pip install -e .
```

macOS Vision OCR and mlx-whisper are automatic dependencies.

## Quick Start

```bash
vs import <card-or-archive>    # Copy + dedup new clips (auto-detects Mazda CX-8)
vs analyze <video.mp4>          # Full pipeline
vs analyze <dir>              # Batch directory
vs analyze <video> --no-llm  # Prefilter only
vs list                       # List jobs
vs report <job-id>            # HTML report
vs search "ABC123"            # Search plates/text
vs search --kind dangerous    # Filter by kind
vs config                     # Show config
```

Typical card-swap workflow: `vs import /Volumes/CX-8`, eject card, then `vs analyze` (processes all pending jobs). Archive imports work too: `vs import /Volumes/lacie8/Ryan/video/CX-8` recurses date-wrapped folders. Incremental — clips already imported (same content hash) are skipped.

**Reports are on demand**: `vs analyze` never writes reports — everything lands in the DB and keyframe JPEGs under `frames/<job_id>/`. When you want to review a job, run `vs report <job-id>`, which renders `reports/job_<id>.html` from the DB without re-analyzing. Find interesting jobs with `vs list` (done status) or `vs search`, then report the ones you care about.

Reports are POI-themed (Machine dark / Samaritan light toggle) with: zoomable keyframe lightbox (wheel zoom, pan, face-detection boxes), Japanese plate ken display, GPS location track (interactive Leaflet map, SVG offline fallback), reverse-geocoded place names (`[report] reverse_geocode`, default on — OSM Nominatim, cached), event categories (driving/parking/stationary), scene descriptions, recording-vs-import date flags, and cross-linked navigation (timeline → captures, plates → events, driving log → events).

### Common Flags

`--stop-after 8h` `--resume` `--watch <dir>` `--only-llm` `--max-llm-events 100` `--retention-days 30` `--retention-days 0` (no cleanup). Global: `--config <path>` `--db <path>`

## Configuration

`~/.video-security/config.toml` (see `config.example.toml`). Every threshold is configurable. Precedence: CLI > camera JSON > TOML > defaults.

SQLite DB at `~/.video-security/db`. Artifacts on external volume: `/Volumes/lacie8/Ryan/video/vs` by default.

## Pipeline Overview

Single ffmpeg decode pass → motion + dHash + MOG2 dedup gate → OSD masking → CLAHE night enhancement → YOLO/ByteTrack vehicle + plate OCR consensus (Apple Vision) + person threat scoring + 30s scene-text OCR → audio: mlx-whisper + Silero VAD + RMS loudness events → NMEA G-sensor events (Mazda CX-8) → LLM triage (gemma3:4b, single keyframe) → detail (gemma4:12b, multi-keyframe + tile escalation). Reports are rendered separately on demand via `vs report`.

## Safety Rules (built-in, automatic)

- Caffeinate (sleep prevention)
- AC-power warning, disk preflight (refuses <10 GB free)
- Runtime watermark (aborts <5 GB)
- Corrupt clips: dead-letter after 3 attempts
- Stage serialization, crash-safe job leases + `--resume`

## Status

Core complete. Source adapters: `mazda_cx8` (filename timestamps, front/rear pairing, NMEA `$GPRMC`/`$GSENS` → GPS track + hard-brake/corner/impact events), `gopro`, `generic`. `vs import` implements scan → hash dedup → verified copy → job registration.

Mazda CX-8 G-sensor events (`hard_brake`, `hard_corner`, `impact`) are detected from NMEA sidecars at zero vision cost and flow through LLM triage/detail like visual events.

## Development

```bash
.venv/bin/ruff check .
.venv/bin/mypy src/video_security tests
.venv/bin/pytest
.venv/bin/pytest -m benchmark
```

Package lives under `src/video_security/` (src layout).

### Smoke testing (isolated from real data)

`test-output/` is gitignored and holds the isolated smoke environment: `test-output/smoke.toml` points the DB and artifact dir at `test-output/` (never `~/.video-security/db` or the real artifact volume). Run smoke checks from the repo root:

```bash
.venv/bin/vs --config test-output/smoke.toml analyze <clip>
.venv/bin/vs --config test-output/smoke.toml report <job-id>
```