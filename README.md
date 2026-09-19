# Video Security Analyzer

A local, resumable CLI for overnight security camera / dashcam footage analysis on Apple Silicon. Cheap-first pipeline: dedup → CV prefilter → local LLM triage → detail.

## Install

Requires Python 3.12+, ffmpeg on PATH, [Ollama](https://ollama.com) running locally:

```bash
ollama pull gemma3:4b
ollama pull gemma4:12b
python3.12 -m venv .venv && .venv/bin/pip install -e .
```

macOS Vision OCR and mlx-whisper are automatic dependencies.

## Quick Start

```bash
vs analyze <video.mp4>          # Full pipeline
vs analyze <dir>              # Batch directory
vs analyze <video> --no-llm  # Prefilter only
vs list                       # List jobs
vs report <job-id>            # HTML report
vs search "ABC123"            # Search plates/text
vs search --kind dangerous    # Filter by kind
vs config                     # Show config
```

### Common Flags

`--stop-after 8h` `--resume` `--watch <dir>` `--only-llm` `--max-llm-events 100` `--retention-days 30` `--retention-days 0` (no cleanup). Global: `--config <path>` `--db <path>`

## Configuration

`~/.video-security/config.toml` (see `config.example.toml`). Every threshold is configurable. Precedence: CLI > camera JSON > TOML > defaults.

SQLite DB at `~/.video-security/db`. Artifacts on external volume: `/Volumes/lacie8/Ryan/video/vs` by default.

## Pipeline Overview

Single ffmpeg decode pass → motion + dHash + MOG2 dedup gate → OSD masking → CLAHE night enhancement → YOLO/ByteTrack vehicle + plate OCR consensus (Apple Vision) + person threat scoring + 30s scene-text OCR → audio: mlx-whisper + Silero VAD + RMS loudness events → LLM triage (gemma3:4b, single keyframe) → detail (gemma4:12b, multi-keyframe + tile escalation) → HTML report.

## Safety Rules (built-in, automatic)

- Caffeinate (sleep prevention)
- AC-power warning, disk preflight (refuses <10 GB free)
- Runtime watermark (aborts <5 GB)
- Corrupt clips: dead-letter after 3 attempts
- Stage serialization, crash-safe job leases + `--resume`

## Status

Core complete. Phase-2 (dashcam source adapters, `vs-import` for Mazda CX-8 card) not yet implemented.

## Development

```bash
.venv/bin/ruff check .
.venv/bin/mypy video_security tests
.venv/bin/pytest
.venv/bin/pytest -m benchmark
```