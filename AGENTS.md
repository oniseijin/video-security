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

## Commands

| Command | Purpose |
|---|---|
| `ruff check .` | Lint |
| `mypy .` | Typecheck |
| `pytest` | Run tests |
| `pytest -xvs` | Verbose, stop on first failure |

## Hardware Constraints

- Apple M2 Pro, 16 GB unified memory, 10-core
- 39 GB free system disk
- External artifact disk recommended

## Mandatory Rules

See [DESIGN.md → Mandatory Rules](DESIGN.md#mandatory-rules). The 6 rules there are non-negotiable.

## Dependencies

ffmpeg, opencv-python, ultralytics, Pillow, typer, faster-whisper, silero-vad,
mlx-whisper, pyobjc-framework-Vision