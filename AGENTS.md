# Video Security Analyzer — OpenCode Agent Instructions

## Project Overview

Local, resumable CLI tool for overnight security camera footage analysis on Apple
Silicon. Cheap-first pipeline: dedup → prefilter → LLM triage → detail.

**Source of truth**: [DESIGN.md](DESIGN.md). Read it first.

## Conventions

- No surprise features — stick to the plan
- No comments unless asked
- Minimal token output — direct answers, no explanations
- Plan mode enforced: never make edits in plan mode
- Rebuild the web bundle (`npm --prefix web run build`) after touching web/

## Commands

Run via `.venv/bin/...` from the repo root (dev venv); the installed
runtime is a snapshot at `~/.local/opt/video-security` managed by
`install.sh` (refresh it after committing user-facing changes).

| Command | Purpose |
|---|---|
| `.venv/bin/ruff check .` | Lint |
| `.venv/bin/mypy src/video_security tests` | Typecheck |
| `.venv/bin/pytest` | Run tests |
| `.venv/bin/pytest -xvs` | Verbose, stop on first failure |

## Reports / Theming

- `src/video_security/report_theme.py` is the single source of the
  POI design system: `--vs-*` CSS tokens, Machine/Samaritan themes,
  lightbox/faces/map JS. The web UI must reuse these tokens — do not
  fork the design language.
- Reports are rendered on demand by `vs report <job-id>` — analyze
  never writes reports.
- Reports reference keyframes in `frames/<job_id>/` via relative
  paths; never copy assets into the report.

## Releases

1. Update CHANGELOG.md (Keep a Changelog format)
2. Bump `version` in pyproject.toml
3. Commit, tag `vX.Y.Z` (annotated), run `./install.sh` to promote the
   installed snapshot
4. Full suite (`ruff`, `mypy`, `pytest`) must be green before tagging

## Hardware Constraints

- Apple M2 Pro, 16 GB unified memory, 10-core
- 39 GB free system disk
- External artifact disk recommended

## Mandatory Rules

See [DESIGN.md → Mandatory Rules](DESIGN.md#mandatory-rules). The 6 rules there are non-negotiable.

## Dependencies

ffmpeg, opencv-python, ultralytics, lap, Pillow, typer, faster-whisper,
silero-vad, mlx-whisper, pyobjc-framework-Vision