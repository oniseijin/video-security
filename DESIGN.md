# Video Security Analyzer — Design Document

## Overview

A local, resumable CLI tool that analyzes security camera footage overnight using
a cheap-first pipeline architecture. Frames are extracted with scene-aware dedup,
prefiltered through fast CV detectors, then suspicious events are escalated to a
local vision LLM for detailed analysis. All processing runs offline on Apple Silicon.

**Hardware**: Apple M2 Pro, 16 GB unified memory, 10-core CPU.

**Models**: triage gemma3:4b / detail gemma4:12b via Ollama, or the mlx_serve
primaries — `mlx-community/gemma-4-e4b-it-4bit` (triage),
`mlx-community/gemma-4-12b-it-4bit` (detail), and
`mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ` (embeddings). mlx_serve is the
primary backend; `[llm] provider` selects it or "ollama" at config load, so
switching is a one-line rollback. gemma4:12b vision capability must be verified
before implementation.

**Key design constraint**: only 39 GB free on system disk. All artifacts
(keyframes, crops, reports) go to an optional external volume. System disk is
reserved for code, models, and OS.

---

## Architecture

```
Source Video
    │
    ├── frames ──→ single decode pass (motion + dHash + MOG2 blob gate)
    │                  │
    │           keep if: motion OR heartbeat(30s) OR scene change
    │           OSD mask applied before diffing/hashing
    │           CLAHE normalization on low-luminance frames
    │                  │
    ├── audio ────→ Whisper (MLX) → transcript
    │           condition_on_previous_text=False
    │           Silero VAD 300ms padding
    │           pin language if monolingual deployment
    │           piped directly from ffmpeg → no temp WAV on disk
    │
    └──→ Prefilter (shared decode pass, 3 consumers)
          │
          ├── Vehicle pipeline:
          │   YOLO CoreML (batch 8-16, relevant classes only: person, car,
          │   truck, bus, motorcycle, bicycle) → ByteTrack (ultralytics built-in,
          │   no separate dep) → per-track: plate crop → upscale 2-4× → Vision
          │   OCR → consensus vote across all frames of the track → plates table
          │
          ├── Threat pipeline:
          │   person + motion intensity + time-of-day weight → anomaly score
          │   → adjacent flags merged deterministically → events table
          │
          └── Scene text pipeline:
              Vision OCR 1-per-30s sample → frame_text table + FTS5 index

    ▼
Prefilter output: events table with event_type + keyframes_json + priority

    ▼
LLM Triage (Pass 1): single keyframe per event, tight prompt, ~60-100 tokens,
    ~6-8s per event. gemma3:4b. Top-N only (--max-llm-events breaker).

    ▼
LLM Detail (Pass 2): multi-keyframe, only events escalated from Pass 1.
    gemma4:12b. ~20-40s per event. Stores model_digest + prompt_version per row.

    ▼
Reports: timeline + plates catalog + events log + transcript export
```

### System layout & data flow

```mermaid
flowchart LR
    subgraph SYS["System disk · Apple M2 Pro"]
        RT["installed runtime · ~/.local/opt/video-security<br>var/config.toml · SQLite catalog (WAL)"]
        LLM["local LLM server · provider: ollama / mlx-serve<br>triage gemma3:4b · detail gemma4:12b"]
        LOCAL["Apple Vision OCR · mlx-whisper · Silero VAD · ffmpeg · YOLO"]
    end

    subgraph HOTV["Artifact volume (hot)"]
        CLIPS["clips/ by import date + mode + channel"]
        EV["frames/job · plate crops · face crops · reports"]
        PROXY["720p proxies (replace archived originals in place)"]
    end

    subgraph COLD["Cold tiers (priority list)"]
        C1["primary · external disk"]
        C2["secondary · Dropbox-synced (manual cloud tier)"]
    end

    SRC["sources · Mazda card · archives · Photos library"] -->|"vs import · hash dedup + verified copy + job registration"| CLIPS
    CLIPS -->|"vs analyze · decode → prefilter → LLM triage/detail"| EV
    RT -.-> LLM & LOCAL
    CLIPS -->|"vs archive · transcode proxy + move original"| C1
    C1 -->|"--relocate"| C2
    C2 -.->|"--restore · bounded size-verified self-healing discovery"| CLIPS
    EV -->|"vs report / vs serve (readonly WAL)"| USER["review · dashboard · search"]
```

---

## Modules

### Module 1: Frame + Audio Ingest

**Frame extraction**
- ffmpeg single decode pass computing: motion score + dHash + MOG2 blob gate
- Keep frame if: motion above threshold OR heartbeat (every 30s) OR scene change
- OSD mask applied before diffing/hashing — burned-in timestamps defeat pixel-diff
- CLAHE normalization on low-luminance frames (night/IR footage)
- Frames are **streamed, never persisted to disk** — only event keyframes survive

**Audio extraction**
- ffmpeg pipe PCM directly into mlx-whisper — no temp WAV file on disk
- `condition_on_previous_text=False` to prevent hallucination loops on noise
- Silero VAD with 300ms padding (prevents speech-onset clipping)
- Pin language if deployment is monolingual (auto-detect can flip on noisy audio)
- Single Whisper stack: mlx-whisper only. Model: small (~0.5 GB) or large-v3-turbo (~1.6 GB)

**Audio event detection** (cheap, deterministic, runs during ingest):
- RMS energy analysis on the PCM stream — sustained energy above baseline flags
  shouting/glass-break/car-alarm candidates (no ML needed)
- Keyword matching on transcript segments — distress words ("help", "police",
  "get out") create `audio_distress` event candidates
- Both feed the events table with `clip_id`, no track_id, no keyframes (LLM gets
  transcript text only, no frames)

### Module 2: Fast Prefilter

Three consumers fed by shared decode pass. Runs serially (not concurrently).

**Vehicle pipeline**
- YOLOv8 CoreML (.mlpackage) for ANE acceleration. Batch 8-16.
- Relevant classes only: person, car, truck, bus, motorcycle, bicycle
- ByteTrack via ultralytics built-in (`model.track(tracker="bytetrack.yaml")`)
- Per-track plate pipeline: vehicle crop → upscale 2-4× → Vision OCR
  (`VNRecognizeTextRequest` via pyobjc-framework-Vision) → per-track consensus
  vote across all frames → best text + confidence → plates table
- Weapon is NOT a detector class. If LLM describes one, it goes in report as
  "described object, unverified" — never emitted as a machine-generated label.

**Threat pipeline**
- Person detection + motion intensity + time-of-day weight → anomaly score
- Adjacent flagged frames merged deterministically into events (SQL/Python, not LLM)
- Events written to events table with closed event type enum defined by source adapter

**Scene text pipeline**
- Vision OCR 1 sample per 30s on full frame for signage, timestamps, billboards
- Stored in frame_text table with FTS5 index

### Module 3: Slow LLM Analysis

**Audio context injection**: each event sent to the LLM includes the transcript
window ±30s around the event time range (from transcript_segments), wrapped in
delimiters as untrusted input. Prompt template:

```json
{
  "frames": ["<keyframe 1>", "<keyframe 2>"],
  "transcript_window": "[00:01:15] hello?\n[00:01:22] who's there? I'm calling police",
  "metadata": {"event_type": "intrusion", "detector_score": 0.83}
}
```

Two-pass system:

**Pass 1 (Triage)**: single keyframe per event. Tight prompt. ~60-100 tokens output.
Gemma3:4b. ~6-8s per event. Only top-N events by score.

**Pass 2 (Detail)**: multi-keyframe. Only events escalated from Pass 1.
Gemma4:12b. ~20-40s per event. Escalation ladder: whole event → tile (10-20%
overlap mandatory) → merge/dedup. No temperature-boost retry.

Per-row storage: model_digest (provider model id; Ollama `ollama show` or the
mlx_serve model name), prompt_version, raw_response (for
re-parseability without re-inference).

### Module 4: Batch Engine

- SQLite state machine: `pending → extracting → filtering → triage → detail → done | failed`
- Content-hash identity on video-level only (no per-frame SHA-256)
- Lease-based recovery for crash safety
- Hard stage serialization enforced — no concurrent heavy stages
- Disk preflight: refuse start if <10 GB free
- Disk runtime watermark: checkpoint + abort if free drops below 5 GB
- Signal handling: first SIGTERM finishes current work + commits; second forces quit
- `--stop-after` time budgets (compound duration parsing: `2h`, `90m`, `45s`)
- `caffeinate -s -i` wrapper — prevent sleep during batch. AC-only (check before asserting)
- Poison-message handling: corrupt clips get max-attempts → dead-letter
- Corrupt/truncated clip policy: catch ffmpeg decode errors, mark clip failed, continue job
- Power: mains-powered required. Document.

### Module 5: Alert & Report

- Timeline report with events + transcript aligned
- Plate catalog FTS5 searchable. Use trigram tokenizer or indexed LIKE for partial
  plate search (default unicode61 splits "ABC-123" into 2 tokens, fails "BC123")
- Driving log with weaving/near-miss events
- Transcript + events export
- HTML reports ~50 MB/night maximum
- No per-frame contact sheets — only event keyframes

---

## Data Model

```sql
-- Core job tracking (content-addressed on video, not frame)
jobs(
    id INTEGER PRIMARY KEY,
    video_path TEXT NOT NULL,
    video_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|extracting|filtering|triage|detail|done|failed
    total_frames INTEGER,
    current_frame INTEGER DEFAULT 0,
    current_stage TEXT DEFAULT 'pending',
    recording_start_utc TEXT,
    import_id TEXT,                  -- card import batch identifier
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Kept frames only — static frames are dropped during dedup
frames(
    job_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    frame_number INTEGER NOT NULL,
    timestamp_sec REAL NOT NULL,
    dhash INTEGER,
    lighting_condition TEXT,  -- day|dusk|night|ir
    PRIMARY KEY (job_id, clip_id, frame_number)
);

-- Closed event taxonomy, defined by source adapter
events(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    clip_id INTEGER NOT NULL,
    track_id INTEGER,               -- NULL for multi-track, audio, or non-vehicle events
    keyframes_json TEXT DEFAULT '[]',  -- empty for audio-only events
    detector_score REAL NOT NULL,
    priority REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|triaged|detailed|resolved|suppressed
    llm_result_id INTEGER
);

-- Vehicle tracks for plate consensus and trajectory analysis
vehicle_tracks(
    job_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    first_frame INTEGER NOT NULL,
    last_frame INTEGER NOT NULL,
    weaving_score REAL,
    direction TEXT,          -- N/S/E/W or wrong_way
    PRIMARY KEY (job_id, track_id, clip_id)
);

-- Per-track plate consensus
plates(
    job_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    raw_text TEXT,
    norm_text TEXT,                -- uppercase, alnum-only for search
    confidence REAL,
    best_frame INTEGER,
    ocr_votes_json TEXT,          -- all individual OCR reads across track frames
    PRIMARY KEY (job_id, track_id, clip_id)
);

-- Scene text capture (1-per-30s samples)
frame_text(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    frame_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_kind TEXT NOT NULL,       -- signage|timestamp|billboard|other
    region_json TEXT,
    confidence REAL NOT NULL
);
CREATE VIRTUAL TABLE frame_text_fts USING fts5(text, content=frame_text, content_rowid=id);

-- Audio transcript
transcript_segments(
    job_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    segment_id INTEGER NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    text TEXT NOT NULL,
    language TEXT,
    PRIMARY KEY (job_id, clip_id, segment_id)
);

-- LLM analysis results (per-event, not per-frame)
analysis_results(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    model_digest TEXT NOT NULL,     -- provider model id (ollama show output or mlx_serve model name)
    prompt_version TEXT NOT NULL,
    analysis_type TEXT NOT NULL,     -- triage|detail|tiled|ocr_fallback
    raw_response TEXT NOT NULL,      -- original LLM JSON output
    confidence REAL,
    retry_count INTEGER DEFAULT 0,
    tiled_results TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-camera configuration
cameras(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    source_pattern TEXT NOT NULL,     -- glob for source adapter to match
    config_json TEXT NOT NULL        -- homography, OSD mask, timezone, after-hours window, thresholds
);

-- Clip metadata (for multi-file sessions)
clips(
    job_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'front',
    priority REAL NOT NULL DEFAULT 0.5,
    recording_start_utc TEXT,
    duration_sec REAL,
    lighting TEXT DEFAULT 'day',
    has_audio BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (job_id, clip_id)
);

-- GPS + G-sensor sidecar data (NMEA: $GPRMC position, $GSENS acceleration)
clip_gps_data(
    job_id INTEGER NOT NULL,
    clip_id INTEGER NOT NULL,
    time_sec REAL NOT NULL,  -- seconds relative to clip start
    lat REAL,
    lon REAL,
    speed_kmh REAL,
    bearing REAL,
    ax REAL,                 -- G-sensor lateral (g)
    ay REAL,                 -- G-sensor longitudinal (g), braking/accel
    az REAL,                 -- G-sensor vertical (g), impact/bumps
    PRIMARY KEY (job_id, clip_id, time_sec)
);

-- Session grouping (multi-file sessions)
sessions(
    job_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    clips_json TEXT NOT NULL
);
```

**Indexes**: `(job_id, frame_number)` on `frames` and `frame_text`.

**SQLite discipline**: WAL mode, `busy_timeout`, single-writer via `BEGIN IMMEDIATE`. Checkpoint on clean shutdown.

---

## Dependencies

| Package | Purpose | Disk |
|---|---|---|
| ffmpeg | Frame + audio extraction | System |
| opencv-python | Motion, dHash, CLAHE, MOG2 | ~50 MB |
| ultralytics | YOLO + ByteTrack | ~2.5 GB (includes torch) |
| Pillow | Image encode/decode | ~10 MB |
| typer | CLI framework | ~5 MB |
| faster-whisper | Transcription engine | ~100 MB |
| silero-vad | VAD gate | ~10 MB |
| mlx-whisper | AS GPU transcription | ~20 MB |
| pyobjc-framework-Vision | Vision OCR + face | System |

**Total dep install**: ~3 GB.

**Removed from plan**: paddleocr (~1 GB saved), separate bytetrack, openai-whisper.

**Web console (Phase 3)**: no new Python runtime deps (stdlib HTTP server).
Node + npm are dev-only requirements for building the committed React bundle.

---

## Hardware Constraints

- Apple M2 Pro, 16 GB unified memory, 10-core CPU
- 39 GB free system disk (96% full)
- Single machine, no distributed setup
- Ollama models already installed: gemma4:12b (7.6 GB), gemma3:4b (3.3 GB);
  mlx_serve (primary backend) serves
  `mlx-community/gemma-4-e4b-it-4bit` + `mlx-community/gemma-4-12b-it-4bit`
  with `--kv-quant 8`
- qwen3:14b and qwen2.5-coder should be deleted (~10.3 GB reclaimed)
- Secondary disk strongly recommended for artifacts

## Mandatory Rules

1. **Sleep prevention**: `caffeinate -s -i` wrapper. AC-only.
2. **Hard stage serialization**: never run two heavy stages concurrently.
   **Single-model residency**: at most one LLM model in memory at a
   time — the client evicts the previous model before generating with a
   different one (triage 4b → detail 12b swaps, never both resident;
   ~10 GB combined was contributing to memory-pressure kills on 16 GB).
   Ollama: `keep_alive: "30m"` sliding window keeps the active model warm
   between sparse calls during a run; explicit unload of all used models
   when the run ends (clean exit, Ctrl+C, or time budget) so nothing stays
   resident after the tool exits. mlx_serve: single-model residency via
   `--idle-evict-secs` on the server (`/v1/unload-model` from the client).
   `num_ctx` capped: 2048 triage, 4096-8192 detail.
3. **Disk preflight** ≥10 GB free + **runtime watermark** ≥5 GB.
   Frames never persisted to disk (streamed in memory). Only event keyframes
   (~3/event, JPEG q80, max width 1280px) + plate crops hit disk.
4. **Vision OCR** for plates and scene text. Apple Vision OCR preferred
   (`VNRecognizeTextRequest` via pyobjc-framework-Vision). PaddleOCR only as
   optional fallback if Vision underperforms on plates.
5. **Single Whisper stack**: mlx-whisper only. Model: small (~0.5 GB) or
   large-v3-turbo (~1.6 GB).
6. **gemma4:12b vision capability must be verified** before implementation.
   If text-only, fall back to Qwen2.5-VL-7B (~5-6 GB pull).

---

## Configuration

`~/.video-security/config.toml` (override with `--config <path>`). All magic
numbers live here — no hardcoded paths or thresholds in code.

**Precedence**: CLI flags > per-camera `cameras.config_json` > `config.toml`
> built-in defaults.

```toml
[storage]
db_path = "~/.video-security/db"                  # SQLite (internal, fast)
artifact_dir = "/Volumes/lacie8/video/vs"    # persistent artifacts (external)
staging_dir = null                                # optional local in-transit dir, cleaned per job

[import]
card_mount = "/Volumes/CX-8"                      # live card source
archive_dirs = ["/Volumes/lacie8/video/CX-8"]  # archived card copies
preflight_gb = 25                                 # artifact_dir free space required

[adapter.mazda_cx8]
timezone = "Asia/Tokyo"                           # camera clock tz (filename → UTC)

[adapter.mazda_cx8.priority]                      # mode → clip priority
EVENT = 1.0
PARKING = 0.8
MANUAL = 0.7
NORMAL = 0.3

[adapter.mazda_cx8.gsens]                         # G-sensor thresholds (g, deviation from baseline)
hard_brake_g = 0.35                               # |ay| longitudinal (braking/accel)
hard_corner_g = 0.30                              # |ax| lateral
impact_g = 0.80                                   # spike, any axis (az baseline ≈ -1.0 gravity)

[engine]
heartbeat_sec = 30                                # dedup keep-floor
max_llm_events = 100                              # LLM budget circuit breaker
retention_days = 30                               # prune disk + DB rows older than N days
disk_preflight_gb = 10                            # system disk: refuse start below
disk_watermark_gb = 5                             # system disk: checkpoint + abort below

[llm]
provider = "mlx-serve"                              # mlx-serve (primary) | ollama
# ollama_url = "http://localhost:11434"
# mlx_url = "http://127.0.0.1:11234"
# Per-provider model overrides: [llm.triage.models] "mlx-serve" = "mlx-community/gemma-4-e4b-it-4bit"

[llm.triage]
model = "gemma3:4b"
num_ctx = 2048
timeout_s = 120

[llm.detail]
model = "gemma4:12b"
num_ctx = 8192
timeout_s = 300

[whisper]
model = "small"                                   # small | large-v3-turbo
language = null                                   # pin (e.g. "ja") if monolingual

[prefilter]
scene_text_sample_sec = 30                        # Vision OCR sampling interval
motion_threshold = 0.05                            # mean abs diff, keep-gate (camera overrides)
scene_change_hash_dist = 12                       # dHash hamming distance for scene change
night_luma = 60                                    # mean luma below → night lighting
ir_max_sat = 20                                    # max saturation for ir classification (0-255)
decode_width = 1280                                # working decode resolution (motion path runs at 480p)
yolo_model = "yolov8n"                             # ultralytics weights (or CoreML path below)
yolo_conf = 0.25                                    # YOLO confidence threshold
yolo_coreml_path = null                             # pre-exported .mlpackage for ANE (optional)
ocr_min_conf = 0.3                                  # Vision OCR read confidence floor
plate_min_votes = 2                                 # consensus votes required for a plate

[threat]
score_threshold = 0.5                               # anomaly score to flag a frame
person_weight = 0.4                                 # person presence weight
motion_weight = 0.3                                 # motion intensity weight
time_weight = 0.3                                    # after-hours/night boost
after_hours = ["22:00-06:00"]                       # local-time windows (camera overrides)
loiter_min_sec = 60                                  # duration for loitering classification
merge_gap_sec = 5                                   # gap that still merges adjacent flags

[threat.priority]                                   # event type → priority
intrusion = 0.9
loitering = 0.6
suspicious_behavior = 0.5

[report]
reverse_geocode = true                              # OSM Nominatim, cached in gps_reverse_geocode

[map]
carto_api_key = null                                # optional CARTO basemap key — local config only, never committed

[web]
host = "127.0.0.1"                                  # vs serve binding (loopback only)
port = 8377

[audio]
rms_window_ms = 100                                # RMS analysis window
rms_sustain_ms = 500                               # min sustained duration for loud events
rms_factor = 4.0                                    # loud = RMS > baseline * factor
rms_floor = 0.02                                    # absolute loud threshold (silence guard)
vad_pad_ms = 300                                    # Silero VAD padding (speech onset)
distress_keywords = ["help", "police", "get out"]   # audio_distress keyword triggers
```

**Per-camera overrides** (`cameras.config_json` in DB — wins over TOML for that
camera's footage):

```json
{
  "osd_mask": [[x1,y1,x2,y2]],
  "after_hours": ["22:00-06:00"],
  "motion_threshold": 0.05,
  "yolo_classes": [0, 1, 2, 3, 4, 5, 6],
  "homography": null
}
```

**Streaming minimizes local-disk need**: no temp WAV (ffmpeg pipes PCM to
whisper), frames never persisted (only event keyframes + plate crops hit
`artifact_dir`). `staging_dir` is an escape hatch for in-transit files only if
a measurable bottleneck appears — cleaned after every job.

**Mid-job external-drive loss**: I/O errors caught, job checkpoints, aborts
cleanly. `--resume` after remount.

---

## Model Strategy

- **Provider selection**: `[llm] provider = "mlx-serve"` (primary) or `"ollama"`;
  `make_llm_client(cfg)` factory validates at load. Per-provider stage model
  maps (`[llm.triage.models]`, `[llm.detail.models]`, `[llm.embed.models]`)
  resolve the active provider's model at config load.
- Lazy fetching — don't pull models at install. Config specifies per-stage models.
  Startup verifies presence, warns if missing. User pre-pulls.
- Installed: Ollama gemma4:12b (detail pass), gemma3:4b (triage pass);
  mlx_serve `mlx-community/gemma-4-e4b-it-4bit` (triage),
  `mlx-community/gemma-4-12b-it-4bit` (detail),
  `mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ` (embeddings)
- `--only-llm` for re-running analysis with different models on existing prefilter
  output — prompt iteration without re-processing video
- Model digest (provider model id; `ollama show` under Ollama, the mlx_serve
  model name under mlx_serve) + prompt_version stored per analysis_result row
- Single-model residency: one model resident at a time; the client
   evicts the previous model on swap, keeps the active model warm via a 30m
   sliding `keep_alive` (Ollama) or server-side `--idle-evict-secs` (mlx_serve),
   and unloads everything when the run ends

---

## Scene Description Strategy

LLM never runs per frame. Descriptions are three-tier, cheapest that works:

1. **LLM detail pass** — prose descriptions for events that survive triage;
   one `analysis_results` row per pass (~1-2 KB). This is the only stored
   prose; per-frame LLM would cost hours per clip and GBs of storage.
2. **Deterministic synthesis** — at report time, events without LLM text
   get a scene description assembled from structured data (driving/
   parking/stationary category from clip mode + GPS speed, speed, G-force,
   face counts, plates, coordinates). Nothing stored; recomputed on render.
3. **Event category** — derived at render time from the clip's dashcam
   mode (path segment, e.g. PARKING) and GPS speed at event time
   (driving ≥ 5 km/h, else stationary; unknown without GPS). Feeds both
   the synthesis and future web-ui filters.

## Theming (Person of Interest design language)

Reports and the future web UI share one visual language, adapted from
two MIT-licensed references — poi-web-ui (Krisztián Kis, Phresh-IT) and
positronick-ui (Nicholas Sollazzo) — rewritten as framework-free modern
CSS custom properties in `report_theme.py`.

- **Two polarities**, one token layer, switched by `data-theme` on the
  root element:
  - **Machine** (dark, default): black surface, white ink, neon-red
    accent `#ff0000` with glow emphasis, graph-paper-free flat black,
    subtle scanlines, pulsing REC dot.
  - **Samaritan** (light): white surface, black ink, crisp red
    `#e8000d` without glow; strictly monochrome (info/success collapse
    to ink, warning to accent). Hairline frame edges + center reticle
    instead of glowing corner brackets.
- **Type**: Barlow Semi Condensed (display, uppercase, 0.08em
  tracking) + JetBrains Mono (data) via Google Fonts with system
  fallbacks.
- **Geometry**: 0px radius, hairline borders, monospace data rows.
- **Subject frames**: keyframes framed as targets — corner brackets
  tinted by event tone (threat red, warning amber, info blue, asset
  white) with a designation tag straddling the top edge.
- **Accessibility**: `prefers-reduced-motion` disables the pulse;
  print stylesheet flattens to black-on-white and hides the toggle.
- The static report embeds the CSS + a tiny vanilla-JS toggle
  (localStorage persistence, no-FOUC restore). The future web UI
  reuses the same `--vs-*` tokens.

## CLI

```
Global flags: --config <path>, --db <path>          override config file / DB location

vs-import <card-mount>           copy new clips off card to artifact disk, dedup by hash
vs-analyze <video>               full pipeline
vs-analyze <video> --no-llm      prefilter + OCR + tracking only (no LLM)
vs-analyze --only-llm            re-run LLM on existing prefilter output
vs-analyze --resume              resume incomplete jobs
vs-analyze --stop-after 8h       time-budgeted run
vs-analyze --watch <dir>         monitor directory, auto-process new files
vs-analyze --retention-days 30    prune jobs older than N days
vs-analyze --max-llm-events 100  LLM event budget circuit breaker
vs-search "ABC1234"              FTS5 search across all captured text
vs-search --kind dangerous       find dangerous driving events
vs-report <job-id>               generate human-readable report
vs-list                         list all jobs with status
vs-serve                        local web console (read-only viewer, 127.0.0.1)
vs-backfill-media               regenerate plate crops for pre-crop jobs
```

---

## Source Adapters (Phase 2)

Pluggable adapter interface. Each adapter provides:
- `parse_filename(filename) → {timestamp, channel, priority}`
- `discover_clips(directory) → [ClipInfo]`
- `extract_audio(clip) → bool`
- `channel_name(clip) → str`
- `event_taxonomy() → [EventType]`

Built-in adapters: `generic`, `mazda_cx8`, `gopro` (motorcycle, snowboard).

Event taxonomy is adapter-defined, not globally hardcoded. Each adapter
declares its own event types and default priority weights.

### mazda_cx8 Adapter (verified against real card)

Card layout (`/Volumes/CX-8/`, 16GB SD, loop-recording — import before overwrite):

```
NORMAL/    front continuous driving, 2-min clips
EVENT/     front event-triggered (G-sensor impact/threshold)
MANUAL/    front manual recordings (driver button press)
PARKING/   front parking mode (event-triggered by definition)
PICTURE/   stills
REAR/<MODE>/            rear cam, IDENTICAL filename to front pair
SYSTEM/NMEA/<MODE>/     per-clip GPS log, same basename, .NMEA
SYSTEM/THUMB/<MODE>/    per-clip thumbnail, same basename, .JPG
```

- **Filename**: `YYMMDDHHMMSS.MP4` in camera-local time. Camera timezone must be
  configured in `cameras.config_json` to convert to UTC.
- **Front/rear pairing**: same basename in `<MODE>/` and `REAR/<MODE>/` = one
  session, two channels.
- **Priority mapping**: EVENT=1.0, PARKING=0.8, MANUAL=0.7, NORMAL=0.3.
- **NMEA sentences**: `$GPRMC` (time/lat/lon/speed-knots/bearing/date) and
  proprietary `$GSENS,x,y,z` — **G-sensor acceleration per second**. $GSENS is
  the ground truth for dangerous driving: hard braking, impact, aggressive
  cornering, detected with zero vision cost. Parsed into clip_gps_data
  (ax/ay/az columns) and G-force threshold crossings generate events directly
  (hard_brake, impact, hard_corner).
- **Audio**: verify per-clip (has_audio).

### Import Pipeline (card swap workflow)

Cards are slow, loop-record (old clips get overwritten), and the user swaps them
every few days. Never process from the card — import first:

```
vs-import <source>
  <source> is either:
    - a live card mount (e.g. /Volumes/CX-8), or
     - an archived card copy (e.g. /Volumes/lacie8/video/CX-8/20250923)
      — identical MODE layout, optionally wrapped in date folders
       (vs-import /Volumes/lacie8/video/CX-8 recurses all date subfolders)

  1. Preflight: artifact_dir mounted, ≥ [import] preflight_gb free
  2. Scan source with mazda_cx8 adapter → clip list with front/rear pairs
     (discover_clips recurses <YYYYMMDD>/ date-wrapped archives)
  3. Skip clips whose video_hash already exists in DB (incremental import)
  4. Copy new clips (+ paired rear, + .NMEA sidecar) to
     artifact_dir/clips/<YYYYMMDD-import>/<mode>/<channel>/
     — matches the existing manual archive convention on lacie8
  5. Verify copy (size + sampled bytes), then register as job with import_id
  6. Report: N new clips imported, M skipped (already imported)
  7. Card can be ejected immediately after import — processing happens later
```

First import target: existing archive at `/Volumes/lacie8/video/CX-8/`
(14 GB, 574 files, full card copies incl. REAR + System + NMEA).

Loop-recording makes frequent imports matter: the card is always near-full, so
the oldest clips are always at risk of overwrite.

---

## Future Phases & Refinements

Post-v1 candidates, ranked by value/effort under the standing constraints
(16 GB RAM, single-model residency, local-only, single user). Pipeline/intelligence-level items live here; web UI
evolution (writes, auth, native report) stays tracked under Web Console →
v2 thoughts and is not duplicated below.

STATUS (2026-09-21): R1-R8 are implemented and shipped in [Unreleased];
treat the sections below as the design record for what shipped. R8
sound classification currently no-ops on macOS 26 (pyobjc/SoundAnalysis
bridge segfault) and activates where the framework is reachable.
R9 (Photos library import + device detection) and R10 (Photos & device
refinements) are implemented and shipped in [Unreleased].

### Phase R1 — Face identity & people search

- **Goal**: "find every clip where this face appears" across the archive.
- **Approach**: compute a feature print per face crop at harvest time
  (`VNGenerateImageFeaturePrintRequest` — pyobjc Vision is already a dep,
  no new packages), gated by face capture quality
  (`VNDetectFaceCaptureQualityRequest`) so only usable crops get embedded.
  New `faces` table (event_id, crop_path, quality, embedding BLOB);
  greedy clustering by distance threshold into a `persons` table — new
  faces join the nearest cluster below threshold else mint a new one.
  Web: person pages with cross-job sightings; face chips on events link
  to their cluster. If Vision feature prints cluster poorly on small
  crops, swap in a small face-embedding .mlmodel (few MB) via
  `VNCoreMLRequest` — a model file, still no new Python deps.
- **Effort**: M
- **Depends on**: —
- **Priority**: P0

### Phase R2 — Watchlists & local notifications

- **Goal**: alert when a specific plate/text/person is seen again.
- **Approach**: `watchlists` table (kind: plate|text|person, pattern,
  note), evaluated deterministically at harvest end — plate norm_text
  LIKE, frame_text FTS5, person cluster id. Hits flag the event, surface
  as a dashboard banner + report section, and fire `[notify] command`, a
  user-supplied shell template (e.g. `osascript -e 'display
  notification'` — local hooks only, no push services). CLI: `vs watch
  add/list/remove`.
- **Effort**: S
- **Depends on**: person watchlists need R1; plate/text stand alone
- **Priority**: P0

### Phase R3 — Capture-quality pass

- **Goal**: better keyframes/crops in → better triage, OCR, and clusters out.
- **Approach**: replace even-spacing keyframe choice
  (`_choose_keyframe_numbers`) with a quality score over frames already
  cached during decode — Laplacian variance (sharpness), luma, detector
  confidence, face capture quality, OCR presence. Face crops: keep the
  best-quality frame per face, not the first. Plates: weight consensus
  votes by frame sharpness; at night, temporal median-stack the aligned
  plate crop across track frames to denoise before Vision OCR.
  Forward-only (like night raw keyframes); old jobs keep their frames.
- **Effort**: M
- **Depends on**: —
- **Priority**: P1

### Phase R4 — Semantic search

- **Goal**: "car parked blocking the driveway" finds events, not just FTS
  tokens.
- **Approach**: embed detail-pass descriptions + transcript segments with
  nomic-embed-text (already pulled) into `event_embeddings` /
  `transcript_embeddings` tables (float32 BLOBs, ~3 KB/row). Query =
  embed once, numpy cosine over the table — thousands of rows
  brute-force in ms, no vector DB. Runs as a dedicated indexing pass
  (`vs index` / post-harvest hook) that respects single-model residency:
  the embed model loads, generates, and unloads like any other model swap
  (nomic-embed-text under Ollama; Qwen3-Embedding-0.6B under mlx_serve).
  Web search gains a semantic results group.
- **Effort**: M
- **Depends on**: value scales with R3's better descriptions
- **Priority**: P1

### Phase R5 — Timeline, calendar & date-range search

- **Goal**: answer "what happened last Tuesday" across the archive.
- **Approach**: SQL date buckets on `recording_start_utc` — calendar heat
  view (jobs + events per day), cross-job timeline page, `--from/--to`
  flags on `vs search` and the events API. Pure read-side; no schema
  change.
- **Effort**: S
- **Depends on**: —
- **Priority**: P1

### Phase R6 — Dashboard analytics

- **Goal**: patterns over the archive, not just per-job views.
- **Approach**: route heatmap (decimated `clip_gps_data` points on a
  canvas grid layer — no new JS lib), time-of-day histograms via
  strftime, per-location event aggregation grouped by the cached geocode
  name, repeat-plate widget (first/last seen, count — extends the
  existing plate sightings data).
- **Effort**: M
- **Depends on**: —
- **Priority**: P2

### Phase R7 — Run automation

- **Goal**: one scheduled command does the whole overnight cycle.
- **Approach**: `vs run` = import → analyze pending → report digest
  (import already enqueues pending jobs and analyze already claims them,
  so this is a thin wrapper). Ship a launchd plist template for
  scheduled overnight runs. Report diffing: `vs diff <job> <prompt-v1>
  <prompt-v2>` compares `analysis_results` across prompt versions — the
  tool for judging prompt iteration without re-watching footage.
- **Effort**: S
- **Depends on**: —
- **Priority**: P2

### Phase R8 — Audio event classification

- **Goal**: glass-break / alarm / siren events without relying on keywords.
- **Approach**: small bundled CoreML sound classifier (few-MB model file)
  over the PCM stream during ingest, thresholded into `audio_*` event
  candidates alongside the existing RMS path. Deferred until RMS+keyword
  misses prove out on real footage.
- **Effort**: M
- **Depends on**: —
- **Priority**: P2

### Phase R9 — Photos library import + device detection

- **Goal**: ingest security-relevant videos that live in the Apple Photos
  app (iPhone clips, Ray-Ban Meta glasses exports) through the existing
  import pipeline, immune to iCloud "Optimize Storage" eviction; give
  every job a device identity regardless of source.
- **Approach**:
  - `adapters/photos.py`: osxphotos enumeration (`photos(movies=True)`,
    skip hidden, `exists()` TOCTOU guard); `detect_adapter()` maps a
    `.photoslibrary` suffix to the adapter. `ClipInfo` gains
    `source_uuid`; mode `"PHOTOS"` (constant, not album-derived), channel
    `"front"`, priority from `[adapter.photos]` (default 0.7),
    `recording_start_utc` from asset creation date.
  - Copy-at-import — never reference in place; eviction is the threat
    model. Destination follows the existing convention
    `clips/<YYYYMMDD>/<PHOTOS>/front/`, filename preserved; collision →
    `<name>-<hash8>.<ext>`. `import_id` label uses the sanitized library
    stem (e.g. `20260921-Photos-Library`).
  - Lifecycle: insert-only `photos_imports` table
    (uuid PK, job_id, video_hash, filename). Known UUIDs skip without
    touching the file — stable across eviction+redownload and across
    job pruning (rows intentionally outlive pruned jobs, ~100 B each).
    Hash-hit on an unknown UUID also writes the mapping. Cloud-only
    assets are skipped and counted (`skipped_cloud` in the import
    report); idempotent re-runs pick them up after a manual download.
    No deferred/promote state machine — copy-at-import has exactly one
    transition (absent → imported), so phototext's offload machinery
    would only promise auto-download work this tool won't do.
  - `--since YYYY-MM-DD` import filter (asset creation date) bounds the
    first-run blast radius against a full-library import.
  - Device detection for ALL adapters, not just Photos: `devices.py`
    `probe()` shells out to `exiftool -s2 -json` (optional binary,
    `shutil.which` guard — absent binary never blocks import) reading
    Make/Model from the copied file. Precedence: file EXIF →
    adapter-declared (Mazda CX-8 clips embed no make/model — verified;
    the adapter knows) → `unknown`. Mapping: `Make=Apple, Model=iPhone*`
    → `iphone`; `Ray-Ban`/`Meta` → `meta_glasses`; adapters declare
    `dashcam` where structure is the only signal. Result stored in
    `jobs.metadata_json` (`device_kind`, `device_make`, `device_model`),
    surfaced via `/api/jobs/{id}`, web job header, and report.
  - `osxphotos>=0.76` joins dependencies (pin proven in phototext);
    lazy import inside the adapter keeps card-only runs free of it.
    Test seam: `VIDEO_SECURITY_TEST_PHOTOS_ASSETS` env var feeds
    `[uuid, path, date, hidden]` rows in place of a real library.
  - CLI: `vs import "~/Pictures/Photos Library.photoslibrary"
    [--since ...]` — auto-detect via suffix, no `--photos` flag;
    `vs run --source <library>` composes import → analyze unchanged, so
    the nightly wrapper needs no change. Retention stays uniform —
    30-day pruning removes rows + derived artifacts, the copied clip in
    `clips/` persists (same as cards).
- **Effort**: S–M
- **Depends on**: —
- **Priority**: P1
- **Verification notes**: iPhone MOV make/model via exiftool and the
  Ray-Ban Meta EXIF shape are unverified on this machine (the library
  currently holds no videos) — confirm at implementation time; `unknown`
  is the graceful default either way.

### Phase R10 — Photos & device refinements

- **Goal**: curation and device-aware semantics on top of R9.
- **Approach**: `--album` filter (osxphotos album membership); per-device
  priorities (e.g. `meta_glasses` 0.8 > `iphone` 0.7 — glasses clips are
  first-person and most security-relevant); device-aware LLM triage
  context (first-person bodycam vs handheld phone vs vehicle dashcam);
  web filter chips and report grouping by device; GPS extraction via the
  same exiftool probe (phone/glasses GPS → `clip_gps_data` → map presence
  + driving/stationary categories for non-dashcam clips, which today
  categorize as `unknown`); optional `--wait-download` (PhotoKit download
  request with hard timeout) only if cloud-only skips prove painful in
  practice.
- **Effort**: M
- **Depends on**: R9
- **Priority**: P2

STATUS (2026-09-21): archive v1 (proxy transcode + local cold dir, tracked
in `archived_originals`, plus `--delete` and `--deep` proxy-to-cold tiers) is
shipped in [Unreleased]. R11 below is the future cold-storage lifecycle —
designed as a sketch, NOT implemented.

### Phase R11 — Cold-storage lifecycle (cloud tiering + gateway + purge)

- **Goal**: give archived originals a full lifecycle: local cold dir →
  optional cloud tier → eventual purge, with tracking at every step — and
  a gateway in the middle so recovery from cloud is first-class, not a
  manual rclone session.
- **Stages**:
  1. **Local cold** (shipped): `vs archive` writes the original to
     `[archive] cold_dir` (tracked in `archived_originals`, location
     `cold`).
  2. **Cloud tier** (this phase): `vs archive --tier` moves cold-dir
     originals to a cloud target (leading candidate: `rclone` subprocess
     against Dropbox — deps-minimal, no SDK). Uploads verify size +
     checksum; the local cold copy is deleted only after verified upload;
     location becomes `cloud` and the row keeps the cloud-side key.
     Local-only rule re-evaluated: original video bytes (not frames or
     text) are the only candidate for cloud egress — decide explicitly
     before building.
  3. **Purge** (this phase): `vs archive --purge N` deletes archived
     originals older than N days (cold or cloud); the row stays with
     location `purged` so the web shows "original no longer exists"
     and restore degrades gracefully.
- **Cold gateway** (the middle layer between the tool and the cloud):
  - Every recovery path checks local cold first; on miss with
    location `cloud`, the gateway fetches the object (rclone copy),
    writes it into local cold (cache fill), and serves it from there.
    Nothing above the gateway needs to know where the bytes live.
  - **Two recovery flavors**:
    - `--restore --job N` (full): the materialized original moves to
      hot (`jobs.video_path`), tracking row cleared — permanent return,
      same as today's cold restore.
    - `--restore --job N --temp [Hh|Nd]` (temporary): the gateway
      materializes into local cold only and serves/uses it there —
      playback, report, evidence review — WITHOUT returning it to hot.
      The cache entry is marked with an eviction deadline; a later sweep
      (`vs archive --evict`, or the nightly) deletes the local copy —
      the cloud object is authoritative and untouched.
  - Tracking additions: `location` (`cold` | `cloud` | `purged`),
    `checksum` for post-move verification, and a `cached_until`
    timestamp for temporary materializations (what's in local cold vs
    cloud-resident).
  - An rclone **mount** can coexist as the zero-code variant (the
    shipped restore already re-resolves under the current `cold_dir`,
    so a mounted cold dir works transparently). Explicit gateway fetch
    is preferred for restore flows: progress, retries, and no FUSE
    dependency; the mount serves browse-only convenience.
- **Web surface**: job detail shows the tier (on disk / cold / cloud /
  purged) plus "cached until X" during temporary restores; restore and
  temporary-restore buttons per job.
- **Effort**: M–L
- **Depends on**: archive v1 (shipped: proxy + cold, `--delete`,
  `--deep`)
- **Priority**: P3 (only when cold-disk pressure or retention policy
  demands it)

### Rejected ideas

| Idea | Why rejected |
|---|---|
| Cloud vision/OCR/LLM APIs | Local-only rule — no frames or text leave the machine. |
| Speaker diarization (pyannote) | ~GB of PyTorch deps + HF auth for near-zero value on single-voice cabin audio; VAD + Whisper already cover it. |
| Vector DB (chroma / sqlite-vec / faiss) | Corpus is thousands of rows; numpy cosine over BLOBs is instant. Minimal-deps rule. |
| Heavy face models (insightface & co.) | Torch-sized dep; R1's Vision path (or a few-MB .mlmodel) is the ceiling unless clustering measurably fails. |
| Person re-id by clothing/gait | Poor accuracy at dashcam resolution + heavy models; face clusters + tracks cover the real cases. |
| Push alerts (ntfy/email/SMS) | Overnight batch tool, not a live monitor; R2's local hooks are the whole requirement. |
| Per-frame LLM / models >12B | 16 GB ceiling + single-model residency; breaks the throughput budget. |
| Deblur / GPU super-resolution for plates (Real-ESRGAN) | Heavy dep for marginal OCR gain over upscale + consensus + R3 stacking. |
| Perf push (deeper frame skipping, ANE tuning, artifact recompression) | 4-6× overnight headroom by design — "don't spend it; spend nothing." |
| Parallel/distributed workers | Single machine; hard stage serialization is a Mandatory Rule. |
| Phototext-style offload demote/deferred lifecycle for Photos imports | Copy-at-import has one transition (absent → imported); deferred rows would promise auto-download work the tool won't do — a skip count is the honest state. |

### Library research (2026-09 scan)

Verified candidates per phase, minimal-deps-first:

- **R1 embeddings**: macOS Vision feature prints via existing pyobjc dep
  (primary plan). Torch-free alternatives if clustering fails:
  `lvface` (github.com/mowshon/lvface, MIT code, ONNX-runtime only,
  76 MB LVFace-T model, built-in cosine DBSCAN clustering — very new,
  watch weight license) or `insightface` (github.com/deepinsight/insightface,
  MIT code, onnxruntime/CoreML — but model weights are non-commercial
  research only, so unfit for any future commercial use). Clustering itself:
  sklearn DBSCAN metric="cosine" or plain numpy — no new deps.
- **R2 notifications**: `osascript -e 'display notification …'` via
  subprocess — zero deps, native macOS. `pync` rejected (dead since 2016).
- **R3 quality metrics**: `cv2.Laplacian(...).var()` for sharpness,
  Brenner focus metric and temporal median stacking via plain numpy —
  no new deps. scikit-image optional (entropy/SSIM) — nice-to-have only.
- **R4 vector search**: numpy brute-force cosine (1-2 ms at ~5k vectors of
  512-d) until >10k embeddings; then `hnswlib`
  (github.com/nmslib/hnswlib, Apache-2.0) or `spotify/voyager`
  (github.com/spotify/voyager, Apache-2.0, macOS arm64 + py3.13
  verified). No vector DB (see rejected).
- **R5 date UI**: `react-day-picker` (github.com/gpbl/react-day-picker,
  MIT, ~12 KB, moment-free) for date filtering;
  `react-calendar-timeline` only if a full Gantt-style trip view is
  wanted. Plain SQL date bucketing may be enough.
- **R6 analytics**: `Leaflet.heat` (github.com/Leaflet/Leaflet.heat,
  BSD-2, ~3 KB) for route heatmaps on the existing map; Chart.js only if
  histograms outgrow hand-rolled SVG (the `--vs-*` design system prefers
  SVG).
- **R7 automation**: launchd plist in ~/Library/LaunchAgents — native,
  no tooling. No app bundling.
- **R8 sound classifier**: `pyobjc-framework-SoundAnalysis` (Apple
  `SNClassifySoundRequest`, 300+ classes, runs on ANE, zero model
  management) — strongly preferred over openl3/PANNs (both torch/TF
  heavy → rejected).
- **Bonus**: `akamhy/videohash` (MIT, needs only ffmpeg) for whole-video
  perceptual dedup — 64-bit hash, Hamming compare, would catch
  accidentally re-imported drives; `imagehash` (Pillow-only) for
  keyframe-level dedup; `pynmea2` only if the hand-rolled NMEA parser
  ever needs hardening.

---

## Web Console (Phase 3)

Local web UI over the same SQLite catalog — the only interface necessary
to see all the reports. Read-only viewer; the CLI remains the only writer.
Built by sub-agents in 10 phases (implementation plan at the end of this
section). Absorbs the former future-work items: report polish (filmstrip
scrubbing, front/rear pair playback, plate gallery, night raw toggle) and
plate crops on disk.

### Serving

- `vs serve [--port N] [--host H] [--open]` — Python stdlib
  `ThreadingHTTPServer`, zero new runtime deps (no FastAPI, no ORM — raw
  `sqlite3` like the rest of the codebase)
- 127.0.0.1 only, default port 8377 (`[web] host/port`), no auth — a
  loopback read-only viewer, stated plainly at startup
- One sqlite3 connection per thread: `file:...?mode=ro` +
  `PRAGMA query_only=ON` + `busy_timeout=5000`. WAL readers coexist with
  a live analyzer run. Startup opens one short read-write connection to
  run idempotent `init_db` migrations, then closes it
- Module layout: `web/server.py` (regex router, static bundle, SPA
  fallback) · `web/api.py` (endpoints as `(conn, cfg, params) -> dict`
  — no HTTP types, testable without sockets) · `web/media.py` (artifact
  mounts, traversal guard, Range streaming) · `web/static/` (committed
  React bundle)

### Frontend

- React + Vite + TypeScript; built bundle **committed to the repo**
  (`web/` source, `src/video_security/web/static/` output). Node is
  dev-only — `install.sh` stays pure pip. Rebuild:
  `npm --prefix web run build` (theme export wired as `prebuild`)
- Runtime deps: react, react-dom, react-router-dom,
  @tanstack/react-query, leaflet. Filters live in URL search params
  (shareable, back-button correct); dashboard polls `/api/stats` every
  5 s — no SSE in v1
- Routes: `/` dashboard (stats, import history, storage, active-job
  progress, recent-events map) · `/jobs` filterable paginated list ·
  `/jobs/:id` tabbed detail · `/jobs/:id/events/:eid` event detail ·
  `/jobs/:id/tracks/:tid` track detail · `/events` cross-job browser ·
  `/plates` + `/plates/:norm` gallery · `/search`
- Recording date (not import date) is the primary label everywhere;
  import date is secondary — same convention as the reports

### Report serving (dynamic, on demand)

- `report.py` splits: `render_report_html(conn, job_id, artifact_dir,
  *, geocode, geocode_network=True, media_base=None, embed=False)
  -> str`; `generate_report` wraps it — `vs report` export unchanged
- Web route `/api/jobs/{id}/report.html` renders with
  `geocode_network=False` (cache-only — the server never calls
  Nominatim), `media_base="/media"` (keyframes/plates served by
  `media.py`), `embed=True` (report masthead toggles suppressed — the
  app shell owns them)
- **Iframe-first with native-tab stubs**: the Report tab embeds the
  rendered route — every current report feature works day one because
  it is the same renderer. All native tabs (Events, Captures, Plates,
  Tracks, Map, Transcript, Playback) ship as live stubs from day one
  (routing + API-backed skeletons) and are promoted panel-by-panel to
  full React implementations as each reaches parity. The Report tab
  stays forever as the canonical full-document view and export path
- Theme sync: iframe is same-origin and shares the `vs-theme`
  localStorage key (no-FOUC on load); the shell `postMessage`s theme
  changes for live swaps

### Theme sharing

- `report_theme.THEME_CSS` splits into `SHARED_CSS + REPORT_CSS`
  (concatenation byte-identical — existing tests pass unchanged).
  SHARED_CSS: tokens, fonts, body/scanlines, masthead, toggles, panel,
  data-table, chips, terminal, subject frames, face boxes, lightbox,
  print. REPORT_CSS: report-only layout (`.wrap`, `.filepath`,
  `.gps-*`, footer)
- `scripts/export_theme.py` (npm `prebuild`) emits
  `web/src/theme/vs-theme.css` + `tokens.json` (per-theme hexes, tone
  colors, tile URLs — single source stays `report_theme.py`; both files
  committed so bundle consumers don't need Python)
- `web/index.html` inlines the same no-FOUC snippet as `THEME_JS`

### Map tiles (CARTO)

- `[map] carto_api_key` — local config only, **never committed**.
  Appended to `basemaps.cartocdn.com` tile URLs at render time via a
  data attribute on the map container; the React TrackMap receives it
  at serve time (injected into the page, never baked into the
  committed bundle)
- Tile keys are client-side by design; loopback-only serving keeps
  exposure local

### API surface

GET-only JSON; errors `{"error": "..."}`; pagination envelope
`{items, total, limit, offset}`; times ISO-8601 UTC; category +
scene-description logic extracted to `enrich.py` and shared with the
report renderer so web and CLI never diverge.

| Endpoint | Returns |
|---|---|
| `/api/health` | version, db mode |
| `/api/stats` | job counts by status, active-job progress, storage, import history |
| `/api/jobs` | list w/ filters (status/mode/channel/import_id/recorded range/q) + pagination; per-job counts, `pair_job_id`, archive flag |
| `/api/jobs/{id}` | detail: clips, pair, event_types, status_counts, has_gps/has_transcript |
| `/api/jobs/{id}/events` | per-job events (type/category/status filters) |
| `/api/events` | cross-job browser (category/type/status filters) |
| `/api/events/{id}` | keyframes (+`raw_url`, faces), plates (+ken, crop_url), transcript window, track strip, location (cache-only geocode) |
| `/api/categories` | category + event_type counts (30 s in-process cache) |
| `/api/jobs/{id}/plates`, `/api/plates`, `/api/plates/{norm}` | reads; gallery grouped by norm_text; sightings per plate |
| `/api/jobs/{id}/tracks` | vehicle tracks (+plate, event_ids, strip) |
| `/api/jobs/{id}/gps` | decimated track + event markers |
| `/api/jobs/{id}/transcript` | segments |
| `/api/search?q=` | grouped: plates (LIKE), frame_text (FTS5), transcripts (LIKE), event types |
| `/api/map/recent` | recent events with coords for the dashboard map |
| `/api/jobs/{id}/report.html` | dynamic report render (above) |
| `/media/frames/...`, `/media/plates/...` | keyframes + plate crops, traversal-guarded |
| `/api/jobs/{id}/video?channel=` | MP4 with HTTP Range (206/416, HEAD — Safari seeking) |

Front/rear pairing: `sessions.clips_json` → job by `video_path`
(`db.get_job_by_video_path`).

### Media + data changes

- **Plate crops on disk**: `PlateRead.best_bbox` (normalized bbox from
  Vision at `best_frame`); `ocr_votes_json` extends to
  `{"votes": n, "best": {"frame": f, "bbox": [x,y,w,h]}}` (old
  `{"votes": n}` rows remain valid). Pipeline writes
  `plates/<job_id>/track_<id>.jpg` (JPEG q85, 15% padding) and sets
  `plates.crop_path`. Backfill for pre-crop rows: `vs backfill-media`
  decodes one frame at `best_frame` (ffmpeg), re-runs Vision OCR,
  locates by text match, crops — idempotent, failures logged
- **Night raw keyframes**: night/IR frames cache both enhanced and
  `_raw` JPEGs during decode (bounded by `JPEG_CACHE_LIMIT`);
  `event_<id>_<i>_raw.jpg` written when a raw exists. Raw/enhanced
  toggle is a web feature; `vs report` output unchanged. Not
  backfilled (frame identity not recoverable) — forward-only
- **Track strips**: ≤5 frames evenly across
  `[first_frame, last_frame]` → `frames/<job_id>/track_<id>_<i>.jpg`,
  paths in `vehicle_tracks.strip_json` — trace a vehicle beyond the
  event window
- Schema (idempotent, `faces_json` pattern): `plates.crop_path TEXT`,
  `vehicle_tracks.strip_json TEXT`; MIGRATIONS v3 indexes:
  `idx_events_job_start`, `idx_events_type`, `idx_plates_norm`,
  `idx_jobs_status`, `idx_jobs_import`
- Retention pruning covers `plates/<job_id>/` and the new frame
  patterns (`frames/<job_id>/` wholesale deletion already covers raw
  + strips)

```
artifact_dir/
  frames/<job_id>/event_<eid>_<i>.jpg        (existing)
                  event_<eid>_<i>_raw.jpg    (new, night/ir only)
                  track_<tid>_<i>.jpg        (new)
  plates/<job_id>/track_<tid>.jpg            (new)
  faces/<job_id>/face_<eid>_<i>_<j>.jpg      (face crops, convention-named)
  clips/<date>/<mode>/<channel>/*.MP4        (existing — video source)
  reports/                                   (existing — unchanged)
```

### Playback

DualPlayer: two `<video>` elements, front = master clock, rear slave
snaps back if |Δ| > 0.25 s (4 Hz corrector). Shared timeline scrubber
with tone-colored event ticks + keyframe filmstrip. Rear-missing jobs
degrade to a single player.

### Sub-agent implementation plan

Each phase lands on a green suite (`ruff` + `mypy` + `pytest`) and is a
self-contained sub-agent task with explicit acceptance criteria.

| # | Phase | Scope + acceptance |
|---|---|---|
| 1 | Report render refactor | `render_report_html` extracted (CLI output byte-identical); `enrich.py` owns clip_mode/event_category/scene_description/nearest_gps/gps_track/format_coord/event_description; THEME_CSS split (concat identical); `media_base`/`embed`/`geocode_network` params; cache-only geocode path. Accept: suite green; `vs-dev report` diff identical; old `{"votes": n}` rows still parse |
| 2 | Schema + analysis-time media | `crop_path` + `strip_json` + `best_bbox` + `ocr_votes_json` extension + MIGRATIONS v3; pipeline writes plate crops, night `_raw`, track strips; retention covers new paths. Accept: idempotent double-init test; real analyze run produces crops/strips/raw |
| 3 | `vs serve` skeleton | server.py + router + static + SPA fallback + readonly thread-local conns; `/api/health` + `/api/stats`; `vs serve` CLI + entries + pyproject script. Accept: HTTP smoke tests (health 200; `GET /../` → 404; ro conn rejects writes); serves alongside a live analyze run |
| 4 | Full read API + media | all endpoints above; media.py mounts + Range streaming; report.html route; pair resolution. Accept: seeded-DB endpoint tests; exact `Content-Range` bytes asserted; Japanese search; embed report uses `/media/frames/...` and has no toggle buttons |
| 5 | React scaffold + theme | Vite+TS+React app; `export_theme.py` prebuild; committed bundle; shell (masthead, ThemeToggle, FacesToggle); Dashboard; `/jobs` list. Accept: clean-checkout build serves at `/`; theme toggle survives reload, no FOUC; `--vs-*` tokens resolve in devtools |
| 6 | Job detail + report tab + stubs | tab bar; Report tab = iframe (`embed=1`); `#event-N` passthrough; postMessage theme sync; ALL native tabs present as live stubs. Accept: every report feature works inside the app; stubs render API-backed skeletons |
| 7 | Native events/captures/tracks | Events browser (cross-job filters); EventDetail w/ Filmstrip scrubber + FaceBoxes + React Lightbox port + plates/crops + transcript window + track context; TrackDetail; night RawToggle. Accept: keyboard scrub; face boxes align at all zooms incl. after RawToggle; category counts match `/api/categories` |
| 8 | Playback + map | DualPlayer sync + shared scrubber w/ event ticks; React TrackMap (theme tiles, CARTO key from serve-time injection, SVG offline fallback). Accept: Safari + Chrome seek (proves Range); drift < 0.25 s; offline → SVG fallback |
| 9 | Plates gallery + search + dashboard map | `/plates` grouped gallery + `/plates/:norm` sightings; `/search` grouped deep links; dashboard recent-events map. Accept: Japanese query finds plates + scene text; all existing plates grouped; markers link to events |
| 10 | Backfill + docs + release | `vs backfill-media` (idempotent, OCR text-locate); README + AGENTS.md web sections; CHANGELOG; 0.3.0 + install.sh refresh. Accept: backfill crops ≥80% of existing plates; installed `vs serve` works; `vs report` unchanged |

### Resolved decisions

Committed bundle (Node dev-only) · iframe-first with native-tab stubs →
incremental React migration · read-only web v1 · polling not SSE ·
cache-only geocode from the server · no raw-night backfill · port 8377 ·
stdlib server · plate crops at analysis time + text-locate backfill ·
track strips as new artifacts.

### v2 thoughts (future evolution)

Direction notes for after v1 ships — not committed work.

**Fully dynamic report (React composition).** v1's iframe report is the
migration scaffold. Suggested ordering:

1. v1 phases 7-9 already promote the panels (Events, EventDetail,
   Captures, Plates, Tracks, Map, Playback) to native React alongside
   the iframe
2. After Phase 9, compose a native Report view from the promoted
   panels behind a config toggle (`[web] native_report`), keeping the
   iframe as fallback — A/B the two while parity is verified
3. Parity checklist before native becomes default: lightbox zoom/pan +
   face boxes at all zoom levels, ken chips, theme tile swap,
   transcript terminal, cross-link navigation, print/export
4. `report.py` stays the renderer for `vs report` and the dynamic
   `/report.html` route forever — the export path never depends on
   React
5. The iframe route remains afterward for deep links and print;
   deprecate only if maintaining both proves costlier than it saves

Supporting migrations when volume justifies them: persist event
category at write time (today computed per request + 30 s cache),
transcript FTS5, residual plate-crop backfill retries.

**When to switch to FastAPI.** The stdlib server is a deliberate v1
constraint, not a destination. Swap triggers — any one suffices:

- Writes from the UI (reviewed/resolved flags, re-run buttons) —
  mutation endpoints want real validation and test ergonomics
- Live progress via SSE/WebSocket (tailing a running analyze) — when
  5 s polling stops being enough
- Auth (LAN access, tokens) — never hand-roll auth
- Route count keeps growing (watchers, upload import)

The swap is cheap by design: `web/api.py` endpoints are pure
`(conn, cfg, params) -> dict` functions with no HTTP types — moving
them behind FastAPI routers is mechanical, no logic rewrite. The
failure mode to avoid is swapping early: stdlib keeps the installer
dependency-free while the API surface is still moving.

**Other v2 candidates** (each a deliberate decision, not a default):
live analyze dispatch from the UI (web becomes a writer — must
coordinate with the single-writer rule), raw/enhanced toggle in
`vs report` export, multi-day trip views, notification digests.

---

## Throughput Budget (per 8h clip, serialized)

| Stage | Time |
|---|---|
| Decode + motion/MOG2/hash (480p motion path) | 30-50 min |
| Whisper small/turbo (8h audio) | 20-30 min |
| Triage: 100 events × ~7s each | 12 min |
| Detail: 20 positives × ~20s each | 7 min |
| Model load (one-time) | 1 min |
| **Total per camera-night** | **1.5-2.5 h** |

4-6x headroom overnight. Don't spend it; spend nothing.

---

## Implementation Specifications

### Package Layout

```
video_security/
├── cli.py            # typer entry: vs-analyze, vs-search, vs-report, vs-list
├── config.py         # TOML config, dataclasses, profile support
├── db.py             # schema, migrations, queries
├── ingest/
│   ├── frames.py     # ffmpeg decode, motion/dHash/MOG2 gate, CLAHE
│   └── audio.py      # PCM pipe → mlx-whisper, VAD, RMS energy
├── prefilter/
│   ├── vehicles.py   # YOLO + ByteTrack + trajectory
│   ├── plates.py     # crop → upscale → Vision OCR → consensus vote
│   ├── threats.py    # person/motion/time scoring → events
│   └── scenetext.py  # 1-per-30s Vision OCR sample
├── llm/
│   ├── ollama.py     # Ollama client, retry/backoff, health check
│   ├── mlx_serve.py  # mlx_serve client (OpenAI-compatible, json_schema output)
│   ├── prompts.py    # prompt templates + PROMPT_VERSION constants
│   ├── triage.py     # pass 1
│   └── detail.py     # pass 2 + escalation ladder
├── engine.py         # state machine, serialization, disk breakers, caffeinate
├── adapters/
│   ├── base.py       # SourceAdapter interface
│   └── generic.py    # default adapter
└── report.py         # timeline, plates, transcript export
```

### LLM Output Schema

Structure is enforced per provider: Ollama `format` parameter; mlx_serve
`json_schema` structured output (both reject malformed responses the same
way client-side). Triage response schema:

```json
{
  "type": "object",
  "properties": {
    "relevant": {"type": "boolean"},
    "event_type": {"enum": ["intrusion", "loitering", "weaving", "near_miss",
                            "plate_capture", "audio_distress", "suspicious_behavior",
                            "hard_brake", "hard_corner", "impact", "none"]},
    "description": {"type": "string"},
    "confidence": {"enum": ["low", "medium", "high"]}
  },
  "required": ["relevant", "event_type", "confidence"]
}
```

Detail response adds `evidence_rationale` (string) and `recommended_action`
(enum: escalate|log_only|none). Any reported weapon goes in description text
with "unverified" — never a dedicated field.

### LLM Retry Policy (both providers)

- Timeout: 120s per request (triage), 300s (detail)
- On timeout/5xx: exponential backoff 2s → 4s → 8s, max 3 attempts
- On malformed JSON: one repair retry with "JSON only, no prose" instruction
- After max attempts: event marked `failed`, job continues (no poison-pill loop)
- Startup health check: ffmpeg present, the configured LLM backend (mlx_serve
  or Ollama) reachable, configured models present — fail fast with actionable
  error before touching video
- mlx_serve specifics: memory-gate (4xx KV) responses trigger the detail
  fallback to the triage model for the rest of the sweep (sticky),
  `/v1/unload-model` for residency control

### Video Hash Method

Chunk-sampled hash: SHA-256 over first 4MB + middle 4MB + last 4MB + file size.
Whole-file hashing is too slow for multi-GB NVR files; chunk sampling catches
accidental duplicates, which is the actual use case.

### Concurrency Model

Single process, hard stage serialization. Lease recovery exists for crash
safety (process died mid-job → lease expires → next run reclaims), NOT for
parallel workers. Parallelism inside a stage is limited to YOLO batching and
ffmpeg's internal threading.

### Watch Mode File Stability

Before enqueueing a file from `--watch`: size must be unchanged across two
checks 5s apart AND no write lock. NVRs write clips continuously; processing
a half-written file is the classic footgun.

### Retention Semantics

`--retention-days N` prunes: keyframe JPEGs + plate crops on disk, DB rows
for jobs (cascades to frames/events/plates/transcript). Runs at startup before
new work. Default: 30 days.

---

## Testing Strategy

- Mock Ollama server (ok, fail500, slow, trickle, junkonce modes) and mock
  mlx_serve (`tests/mock_mlx_serve.py`) for client tests.
- Golden synthetic clip: car with known plate driving known path + static person.
  End-to-end asserts: plate correct, event bounds correct, static-person event
  survives dedup.
- Dedup fixture with burned-in OSD timestamp overlay.
- Per-stage perf benchmark tests with throughput floors.
- Simulated low-disk test: breaker fires, job checkpoints, `--resume` completes.

## Future Work

Not in scope for current phases; captured so the intent isn't lost.

- Memory-pressure test: detail falls back to 4B model.
- Transcript FTS5 index (volume too low to bother yet).
- Web console writes (e.g. reviewed/resolved event flags) — v1 is
  read-only by design; any write path is a deliberate later decision
  (see Web Console → v2 thoughts).
- **Ultralytics → non-AGPL detector** (potential consideration — likely not
  worth the effort absent a concrete trigger): ultralytics is the only
  AGPL-3.0 component in the tree (and its yolov8n.pt weights are AGPL too;
  everything else is MIT/Apache). AGPL binds nothing today (local personal
  use, repo already AGPL) — it only matters if we relicense to MIT/BSD
  (commercial reuse, AGPL-shy adopters). Swap path: YOLOX-n/s (Apache-2.0,
  code *and* weights, COCO classes match ours) via the existing onnxruntime
  dep (or cv2.dnn — zero new deps), ByteTrack from Roboflow `supervision`
  (MIT) or vendored `ifzhang/ByteTrack` (MIT, ~250 lines; `lap` already
  present). Alternative if YOLOX disappoints: RT-DETR (Apache-2.0, heavier).
  Effort: rewrite the vehicles.py inference/tracking surface (~80 lines —
  `YOLO()` load, `model.track()`, `result.boxes` unpacking; downstream
  consumes dataclasses, untouched), letterbox/NMS glue, installer weight
  download+pin, CoreML conversion if wanted, plus re-validation on real
  footage and conf/iou re-tuning — est. 1-2 focused days + a validation
  pass. Benefit: MIT relicensing becomes possible, drop the ultralytics dep
  tree. Risk: detection-quality regression vs tuned yolov8n on dashcam
  angles/night. Verdict: hold as option-value only.

### Phase-1 output review (2026-09-23) — plate crops + overlay boxes

Reviewed real phase-1 output; three linked follow-ups. Deferred until the
night batches finish — implement after 2026-09-24.

- **JP-aware plate crops** (small, hours). The saved plate crop
  (`pipeline.py` crop block, ~line 505) is the OCR text bbox + uniform 15%
  padding inside the vehicle crop — Japanese plates stack
  prefecture/class-number/hiragana/digits, so the text-hugging box clips
  the kanji and hiragana rows (evidence: event #726 lost the prefecture and
  misread の as "N"; job 170 event #1464 kept the top row, lost the bottom).
  Fix: expand the text bbox to a JP plate aspect (≈2.2:1 wide / 1.84:1 kei)
  with the text row anchored to the lower-right quadrant — or the simpler
  tunable asymmetric multipliers (left 0.5 / up 0.6 / down 0.25 / right 0.2
  of bbox dims), clamped to the vehicle crop. Config:
  `[prefilter] plate_crop_pad`. Backfill existing crops by extending
  `backfill_plate_crops` (`backfill.py:308` — FrameReader/ocr_fn seams
  exist). Unit tests with synthetic JP plate bbox layouts; update goldens.
- **Plate crop → source keyframe jump** (medium, ~half day). Persist per
  plate row: `crop_src` (keyframe path) + `crop_box` (normalized rect on
  that source image) — new columns via the existing ALTER TABLE pattern
  (`db.py:334`). Web: clicking a plate crop opens the lightbox on the
  source keyframe with the plate rect highlighted. Report: same treatment
  in the report lightbox (`report_theme.py`). Backfill: derive for old rows
  from `ocr_votes_json.best` via the same machinery as the crop regen.
- **Person + animal boxes on keyframes, toggleable** (larger, 1-2 days).
  Person tracks already exist (`vehicle_tracks.class_id=0`; job 457
  track #1 identified but unboxed) — only `faces_json` is persisted per
  keyframe. Add dog/cat (COCO 16/17) to default `yolo_classes`. Persist
  `boxes_json` per keyframe alongside `faces_json`:
  `[{kind: person|animal, track_id, box}]` in the same normalized
  top-left convention as faces, recorded at keyframe-pick time from
  `frame_dets`. Web: generalize `FaceBoxes` → `BoxOverlay` with
  kind→color from `--vs-*` tokens; toggles `vs-persons` / `vs-animals`
  mirroring `FacesToggle.tsx` (localStorage on/off), in filmstrip +
  lightbox. Report: parallel to `THEME_FACES_JS` so toggles sync across
  web UI and report iframe. Backfill: `vs backfill-media --boxes` —
  single-frame YOLO inference per stored keyframe (no tracking needed)
  via the existing Detector seam in `backfill.py`.
- **Person-flicker false intrusions** (evidence: job 420 track 72 → event
  #2369, priority-0.9 intrusion from a 5-frame night car→person
  misclassification; unanimous track votes, so vote-share gates can't help).
  Person events fire per-frame in `threats.py`, so a short misclassification
  flicker passes motion+night gates. **Wait-and-see: check the triage verdict
  on #2369 after tonight's phases 2-3 complete** — if LLM triage rejects it,
  the pipeline works as designed and no change is warranted. If false
  intrusions survive triage, the cheap gate is person persistence (require
  ≥8 person-class frames on a track, ~0.25s, before it can flag intrusion;
  optional detector-confidence floor). Effort: S.

### Face naming & person management (R1 follow-up, 2026-09-23)

Face identity clustering is built but unused: `vs index-faces` computes
Vision feature prints per face crop and greedy-clusters into `persons`
(distance threshold 0.4, min quality 0.2), yet `faces`/`persons` are empty
— the command has never been run on the archive. What's missing is names:
tag a face once (e.g., family members), the name propagates to every
sighting. Trigger example: job 443 event 2407 — known person, wants
tracking across jobs.

- Run `vs index-faces` on the archive first — validates clustering quality
  on real data (faces across lighting/angles). Not during night batches:
  it writes rows while analyze holds the WAL write lock.
  **Ran 2026-09-23: 0 faces registered — root cause found and fix
  designed (below); re-run after the fix lands.**
- **Quality-gate fix (validated, not yet applied)**:
  `VNDetectFaceCaptureQualityRequest` returns zero results on this
  macOS — verified on all 23 face crops AND full keyframes (request
  "succeeds" with 0 observations). `VNDetectFaceRectanglesRequest`
  still works on keyframes (1 face found) but 0/23 on tight crops
  (face fills the frame; the detector can't lock). Measured: all crops
  are 216-504 px with uniform sharpness (1.5-6.9 Laplacian variance
  after upscale), so resolution is the honest usability gate. Fix:
  in `register_face`, when `face_capture_quality` returns None, fall
  back to a resolution gate — `min(image.shape[:2]) >= 48 px →
  quality = 1.0`, else reject (`FALLBACK_MIN_CROP_PX = 48` constant in
  identity.py) + a None-quality fallback test in tests/test_identity.py
  (~4 lines + test; suite verified green with it applied). Keeps the
  Vision quality path intact for macOS versions where the request
  works.
- `persons.name` column (existing ALTER TABLE pattern) + `vs person
  name|merge|move` CLI — the web console stays read-only (v1 design;
  click-to-tag would be the first deliberate web write path, later).
- Propagation is automatic: one name covers every face in the cluster, and
  future index runs assign new faces to the nearest named cluster
  (`assign_person` at index time).
- Display: web person pages + event face chips + report show names. Names
  live in the local DB only — never in the repo (public).
- Cluster QA: threshold clustering fragments (one person → several
  clusters) and over-merges (similar family faces); rename/merge/move is
  the minimal management set — tune `[identity] distance_threshold` if
  the splits/merges look systematic.
- Effort: S-M (CLI + column + display wiring; no pipeline changes).

### Shipped (was future work)

- **Web console** (0.3.0): `vs serve` — local read-only web UI over the
  SQLite catalog (loopback, stdlib server, zero new Python deps):
  dashboard, jobs/events/plates/search, dynamic report embedding,
  filmstrip + lightbox + faces, plate gallery with crops + ken, GPS
  Leaflet map, dual front/rear playback. React source in `web/`, built
  bundle committed at `src/video_security/web/static/` (rebuild:
  `npm --prefix web run build`). Full design in the Web Console
  section, including v2 evolution notes.
- **Report polish** (0.3.0): keyframe filmstrip scrubbing in the web
  console event view, side-by-side front/rear pair playback with event
  ticks, plate gallery across jobs, night raw/enhanced toggle.
- **Plate crops on disk** (0.3.0): per-plate crop JPEGs at analysis time
  (`plates/<job_id>/track_<id>.jpg`, q85, 15% padding) +
  `vs backfill-media` OCR text-locate backfill for pre-crop jobs.
- **POI theming, dual polarity, lightbox zoom** (0.2.0): Machine dark /
  Samaritan light toggle; zoomable lightbox on every image with
  wheel/pan/controls and face-box preservation; Faces On/Off toggle.
- **Face highlighting** (0.2.0): Apple Vision
  `VNDetectFaceRectanglesRequest` on chosen keyframes, `faces_json`
  per event, boxes over keyframes, counts in designation tags. Local
  detection only; no recognition or embeddings.
- **Plate-to-event linkage** (0.2.0): plates link to events via
  `track_id` (`vs search` prints them; chips, table rows and driving-log
  vehicles jump to event anchors); Read At timestamps replace raw
  frame numbers.
- **GPS location track** (0.2.0): interactive Leaflet map (theme-synced
  CARTO tiles) with event markers and SVG offline fallback;
  reverse-geocoded place names (Nominatim, cached).
- **Event categories + scene descriptions** (0.2.0): driving/parking/
  stationary from clip mode + GPS speed; deterministic scene
  descriptions at render time for events without LLM prose.
- **Recording vs import dates** (0.2.0): recorded time in masthead,
  archive-import flag, absolute Recorded column in the timeline.