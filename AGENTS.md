# Video Security Analyzer — OpenCode Agent Instructions

## Project Overview

A local, resumable CLI tool that analyzes security camera footage overnight using
a cheap-first pipeline architecture. Frames are extracted with scene-aware dedup,
prefiltered through fast CV detectors, then suspicious events are escalated to a
local vision LLM for detailed analysis.

## Conventions

- **DESIGN.md is the source of truth.** Any ambiguity → read DESIGN.md
- No surprise features — stick to the plan unless explicitly asked
- No comments unless asked
- Minimal token output — direct answers, no explanations unless requested
- Plan mode enforced: never make edits in plan mode

## Commands

| Command | Purpose |
|---|---|
| `ruff check .` | Lint |
| `mypy .` | Typecheck |
| `pytest` | Run tests |
| `pytest -xvs` | Run tests verbose, stop on first failure |

## Hardware Constraints

- Apple M2 Pro, 16 GB unified memory, 10-core
- 39 GB free system disk
- External artifact disk recommended

## Mandatory Rules (from DESIGN.md)

1. **Sleep prevention**: `caffeinate -s -i` wrapper. AC-only.
2. **Hard stage serialization**: no concurrent heavy stages.
   `keep_alive: 0` after last LLM request. `OLLAMA_MAX_LOADED_MODELS=1`.
   `num_ctx` capped: 2048 triage, 4096-8192 detail.
3. **Disk preflight** ≥10 GB free + **runtime watermark** ≥5 GB.
   Frames never persisted to disk. Only event keyframes + plate crops hit disk.
4. **Vision OCR** for plates and scene text. Apple Vision OCR preferred
   (`VNRecognizeTextRequest` via pyobjc-framework-Vision).
5. **Single Whisper stack**: mlx-whisper only.
6. **gemma4:12b vision capability must be verified** before implementation.

## Dependencies

ffmpeg, opencv-python, ultralytics, Pillow, typer, faster-whisper, silero-vad,
mlx-whisper, pyobjc-framework-Vision