# Implementation Plan

## Agent Assignment Strategy

| Phase | Description | Agent | Why |
|---|---|---|---|
| 1 | DB schema + migrations | `code-helper` | DDL + migration logic. Well-specified. |
| 2 | Config system + CLI skeleton | `code-helper` | Boilerplate. TOML config + typer wiring. |
| 3 | Frame ingest | `code-helper` | ffmpeg + motion + dHash + MOG2 + OSD mask + CLAHE. Algorithmic but well-specified. |
| 4 | Audio ingest | `code-helper` | mlx-whisper pipeline. Clear spec. |
| 5 | Prefilter — YOLO + ByteTrack + plate crop | `code-helper` | Known patterns, integration work. |
| 6 | Prefilter — per-track OCR consensus + Vision OCR | `code-helper` | Apple Vision binding + vote logic. |
| 7 | Prefilter — person/threat scoring → events table | `code-helper` | Scoring math + adjacent-event merge logic. |
| 8 | LLM triage + detail pass | `deep-helper` | Prompt engineering, Ollama format schemas, JSON parsing. Highest complexity — justifies cost. |
| 9 | Batch engine | `code-helper` | Serialization, leases, signal handling, disk breakers, caffeinate. System-level but well-specified. |
| 10 | Reports | `code-helper` | Template rendering + DB queries. |
| 11 | Mock Ollama + golden test clip | `code-helper` | Test infrastructure. Deterministic mocks. |
| 12 | E2E integration + perf benchmarks | `fast-helper` | Plumbing tests together. Verification, not creation. |
| 13 | AGENTS.md + README + config defaults | `fast-helper` | Documentation. |

## Agent Selection Rules

- **`code-helper`**: default agent. Use for all well-specified implementation tasks.
- **`deep-helper`**: prompt engineering + LLM interaction code only. Highest complexity, justifies higher cost.
- **`fast-helper`**: documentation, test plumbing, CI, config defaults. Trivial work, cheapest agent.
- **`explore`**: research/discovery only. Never for code generation.
- **`apex-helper`**: design review only. Never for implementation.

## Parallelization

Phases 5 + 6 can run concurrently (same module, different files). Phases 10 + 11 + 13 can run concurrently after phases 1-9 complete.

All other phases are sequential — each depends on the output of the prior phase.

## Implementation Order

```
1  DB schema + migrations
2  Config system + CLI skeleton
3  Frame ingest
4  Audio ingest
5  Prefilter: YOLO + ByteTrack + plate crop     ┐
6  Prefilter: OCR consensus + Vision OCR          ├── parallel
7  Prefilter: threat scoring → events             │
8  LLM triage + detail pass
9  Batch engine (serialization, leases, disk, caffeinate)
10 Reports                                      ┐
11 Mock Ollama + golden test clip                ├── parallel
12 E2E integration + perf benchmarks             │
13 AGENTS.md + README + config defaults           ┘
```

### Before Starting

- Verify gemma4:12b accepts images (`ollama show gemma4:12b` + test prompt)
- Delete qwen3:14b and qwen2.5-coder:1.5b to reclaim ~10.3 GB disk
- Check for local Time Machine snapshots (`tmutil listlocalsnapshots /`)
- Set up Python 3.11+ venv with dependencies

### After Core Complete (Phase 2)

Source adapters: `mazda_cx8`, `gopro` (motorcycle, snowboard). These layer on top without changing the core pipeline.