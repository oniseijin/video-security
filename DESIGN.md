# Video Security Analyzer — Design Document

## Overview

A local, resumable CLI tool that analyzes security camera footage overnight using
a cheap-first pipeline architecture. Frames are extracted with scene-aware dedup,
prefiltered through fast CV detectors, then suspicious events are escalated to a
local vision LLM for detailed analysis. All processing runs offline on Apple Silicon.

**Hardware**: Apple M2 Pro, 16 GB unified memory, 10-core CPU.

**Models**: gemma4:12b (detail pass), gemma3:4b (triage pass) — already installed.
gemma4:12b vision capability must be verified before implementation.

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

Per-row storage: model_digest (`ollama show`), prompt_version, raw_response (for
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
    model_digest TEXT NOT NULL,     -- ollama show output
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

---

## Hardware Constraints

- Apple M2 Pro, 16 GB unified memory, 10-core CPU
- 39 GB free system disk (96% full)
- Single machine, no distributed setup
- Ollama models already installed: gemma4:12b (7.6 GB), gemma3:4b (3.3 GB)
- qwen3:14b and qwen2.5-coder should be deleted (~10.3 GB reclaimed)
- Secondary disk strongly recommended for artifacts

## Mandatory Rules

1. **Sleep prevention**: `caffeinate -s -i` wrapper. AC-only.
2. **Hard stage serialization**: never run two heavy stages concurrently.
   **Single-model residency**: at most one Ollama model in memory at a
   time — the client evicts the previous model before generating with a
   different one (triage 4b → detail 12b swaps, never both resident;
   ~10 GB combined was contributing to memory-pressure kills on 16 GB).
   `keep_alive: "30m"` sliding window keeps the active model warm between
   sparse calls during a run; explicit unload of all used models when the
   run ends (clean exit, Ctrl+C, or time budget) so nothing stays resident
   after the tool exits. `num_ctx` capped: 2048 triage, 4096-8192 detail.
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
artifact_dir = "/Volumes/lacie8/Ryan/video/vs"    # persistent artifacts (external)
staging_dir = null                                # optional local in-transit dir, cleaned per job

[import]
card_mount = "/Volumes/CX-8"                      # live card source
archive_dirs = ["/Volumes/lacie8/Ryan/video/CX-8"]  # archived card copies
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

- Lazy fetching — don't pull models at install. Config specifies per-stage models.
  Startup verifies presence, warns if missing. User pre-pulls.
- Already installed: gemma4:12b (detail pass), gemma3:4b (triage pass)
- `--only-llm` for re-running analysis with different models on existing prefilter
  output — prompt iteration without re-processing video
- Model digest (`ollama show`) + prompt_version stored per analysis_result row
- Single-model residency: one Ollama model resident at a time; the client
  evicts the previous model on swap, keeps the active model warm via a 30m
  sliding `keep_alive`, and unloads everything when the run ends

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
    - an archived card copy (e.g. /Volumes/lacie8/Ryan/video/CX-8/20250923)
      — identical MODE layout, optionally wrapped in date folders
      (vs-import /Volumes/lacie8/Ryan/video/CX-8 recurses all date subfolders)

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

First import target: existing archive at `/Volumes/lacie8/Ryan/video/CX-8/`
(14 GB, 574 files, full card copies incl. REAR + System + NMEA).

Loop-recording makes frequent imports matter: the card is always near-full, so
the oldest clips are always at risk of overwrite.

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
│   ├── ollama.py     # client, retry/backoff, health check
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

Ollama `format` parameter enforces structure. Triage response schema:

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

### Ollama Retry Policy

- Timeout: 120s per request (triage), 300s (detail)
- On timeout/5xx: exponential backoff 2s → 4s → 8s, max 3 attempts
- On malformed JSON: one repair retry with "JSON only, no prose" instruction
- After max attempts: event marked `failed`, job continues (no poison-pill loop)
- Startup health check: ffmpeg present, Ollama reachable, configured models
  present — fail fast with actionable error before touching video

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

- Mock Ollama server: ok, fail500, slow, trickle, junkonce modes.
- Golden synthetic clip: car with known plate driving known path + static person.
  End-to-end asserts: plate correct, event bounds correct, static-person event
  survives dedup.
- Dedup fixture with burned-in OSD timestamp overlay.
- Per-stage perf benchmark tests with throughput floors.
- Simulated low-disk test: breaker fires, job checkpoints, `--resume` completes.

## Future Work

Not in scope for current phases; captured so the intent isn't lost.

- **Web app for tracking**: a full local web UI over the same SQLite
  catalog — job list with status/filters, event timeline browsing, plate
  and dangerous-driving search, report viewing. Read-mostly; the CLI
  remains the writer, so the app can stay a thin viewer. Reuses the
  `--vs-*` theme tokens from `report_theme.py` and the Leaflet/CARTO
  map layer from the report's Location Track. Data-model notes for
  the UI: distinguish recording date (from dashcam filenames) from
  import date everywhere (timelines, day grouping, navigation);
  surface the event category (driving/parking/stationary) as a filter;
  GPS panels are adapter-dependent (CX-8 only) and must degrade
  gracefully; plates link to events via track_id; reverse-geocoded
  place names come from the `gps_reverse_geocode` cache table.
- **Report polish**: timeline scrubbing with a keyframe filmstrip,
  side-by-side front/rear pair playback, plate gallery across jobs,
  night-mode/CLAHE comparison toggles.
- **Plate crops on disk**: persist per-plate crop images at analysis
  time so reports and the web UI can show the exact plate crop inline
  (currently only the event keyframes exist).
- Memory-pressure test: detail falls back to 4B model.

### Shipped (was future work)

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