# Implementation Plan

## Before Starting

- Verify gemma4:12b accepts images: `ollama show gemma4:12b` + test prompt with a keyframe
- Delete `qwen3:14b` and `qwen2.5-coder:1.5b`: ~10.3 GB reclaimed
- Check Time Machine snapshots: `tmutil listlocalsnapshots /`
- Set up Python 3.11+ venv

## Agent Assignment Strategy

| Phase | Description | Agent | Why |
|---|---|---|---|
| 1 | DB schema + migrations | `code-helper` | DDL + migration logic. Well-specified. |
| 2 | Config system + CLI skeleton | `code-helper` | TOML config + typer wiring. Boilerplate. |
| 3 | Frame ingest | `code-helper` | ffmpeg + motion + dHash + MOG2 + OSD mask + CLAHE. Well-specified algorithm. |
| 4 | Audio ingest | `code-helper` | mlx-whisper pipeline. Clear spec. |
| 5 | Prefilter — YOLO + ByteTrack + plate crop | `code-helper` | Known patterns, integration work. |
| 6 | Prefilter — OCR consensus + Vision OCR | `code-helper` | Apple Vision binding + vote logic. |
| 7 | Prefilter — threat scoring → events | `code-helper` | Scoring math + adjacent-event merge. |
| 8 | LLM triage + detail pass | `deep-helper` | Prompt engineering, Ollama format schemas, JSON parsing. Highest complexity — justifies cost. |
| 9 | Batch engine | `code-helper` | Serialization, leases, signal handling, disk breakers, caffeinate. System-level but well-specified. |
| 10 | Reports | `code-helper` | Template rendering + DB queries. |
| 11 | Mock Ollama + golden test clip | `code-helper` | Test infrastructure. Deterministic mocks. |
| 12 | E2E integration + perf benchmarks | `fast-helper` | Plumbing + verification, not creation. |
| 13 | AGENTS.md + README + config defaults | `fast-helper` | Documentation. |

## Agent Selection Rules

- `code-helper`: default for all well-specified implementation
- `deep-helper`: prompt engineering + LLM interaction only
- `fast-helper`: docs, test plumbing, CI, config defaults
- `explore`: research/discovery only, never code generation
- `apex-helper`: design review only, never implementation

## Dependency Graph

```
Phase 1 (DB)
  ↓
Phase 2 (Config + CLI)
  ↓
Phase 3 (Frame ingest) ──→ Phase 5 (Vehicle prefilter) ──┐
  ↓                                                        │
Phase 4 (Audio ingest) ──→ Phase 6 (OCR prefilter) ────────┤
  ↓                                                        │
Phase 7 (Threat scoring) ──────────────────────────────────┘
  ↓
Phase 8 (LLM triage + detail)
  ↓
Phase 9 (Batch engine)
  ↓
Phase 10, 11, 12, 13 (parallel)
```

## Implementation Order

```
Phase 1:  DB schema + migrations
Phase 2:  Config system + CLI skeleton
Phase 3:  Frame ingest
Phase 4:  Audio ingest
Phase 5:  Prefilter — YOLO + ByteTrack + plate crop  ┐
Phase 6:  Prefilter — OCR consensus + Vision OCR      ├── parallel
Phase 7:  Prefilter — threat scoring → events          │
Phase 8:  LLM triage + detail pass
Phase 9:  Batch engine
Phase 10: Reports                                    ┐
Phase 11: Mock Ollama + golden test clip             ├── parallel
Phase 12: E2E integration + perf benchmarks          │
Phase 13: AGENTS.md + README + config defaults        ┘
```

## Phase 2 (After Core)

Source adapters layer on top without changing the core pipeline:
- `mazda_cx8` — dashcam, multi-clip, parking events, GPS sidecar
- `gopro` — motorcycle + snowboard, action cam, wide-angle, GPS embedded