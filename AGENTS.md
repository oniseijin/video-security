# Video Security Analyzer — OpenCode Agent Instructions

## Project Overview

Local, resumable CLI tool for overnight security camera footage analysis on Apple
Silicon. Cheap-first pipeline: dedup → prefilter → LLM triage → detail.

**Source of truth**: [DESIGN.md](DESIGN.md). Read it first.

## Public Repository Guardrails

This repo is **public**: https://github.com/oniseijin/video-security.
Everything committed and pushed is world-visible.

- Never commit personal data: real plates/faces/GPS in screenshots
  (regenerate with `npm --prefix web run shots -- --redact`), personal
  volume paths (`/Volumes/lacie8/Ryan/...`), names, emails, or API keys —
  the CARTO key lives only in local config; `config.example.toml` keeps
  the placeholder.
- Before committing docs or media, scan for personal traces:
  `git grep -iE 'ryan|mills|cb1_[A-Za-z0-9]{16,}' -- . ':(exclude)src/video_security/web/static'`
  must come back empty (except this rule itself; tests use the `cb1_test_key`
  fixture, and the minified web bundle is excluded because it trips substring
  false positives).
- Push `main` to `origin` only when cutting a release tag (`vX.Y.Z`) —
  commits stay local between tags (decision 2026-09-23).
- Never force-push or rewrite `main` history without explicit instruction.
- License is AGPL-3.0-only (ultralytics dep requires it): new dependencies
  must be AGPL-compatible; never add proprietary code.

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

ffmpeg (external), LLM backend (mlx-serve primary or Ollama), typer,
opencv-python, ultralytics, lap, Pillow, silero-vad, mlx-whisper,
onnxruntime, osxphotos, pyobjc-framework-Vision,
pyobjc-framework-SoundAnalysis