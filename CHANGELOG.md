# Changelog

All notable changes to video-security are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow semantic versioning.

## [Unreleased]

### Added

- **POI-themed reports**: `vs report` now renders a Person-of-Interest
  surveillance-style HTML report — masthead with pulsing REC dot,
  monospace data tables, keyframes framed as targeting subjects with
  tone-colored corner brackets and designation tags, plate chips, and a
  transcript terminal. Dual polarity via a masthead toggle: **Machine**
  (dark: white + neon red on black, glowing brackets, scanlines) and
  **Samaritan** (light: black + crisp red on white, hairline edges +
  reticle), persisted to localStorage. Theme is a self-contained
  modern-CSS token layer (`report_theme.py`), framework-free, and
  carries forward to the future web UI. Design language adapted from
  the MIT-licensed poi-web-ui (Krisztián Kis) and positronick-ui
  (Nicholas Sollazzo).
- **Keyframe zoom**: click any keyframe for a lightbox view with
  mouse-wheel zoom (toward cursor), +/-/reset controls, double-click
  toggle, and drag-to-pan. Face-detection boxes are cloned into the
  lightbox and scale with the zoom; the Faces On/Off toggle (persisted)
  hides them everywhere.
- **Japanese plate support**: plate OCR now runs with ja-JP recognition
  and keeps CJK characters, so the place-name kanji survives; a
  place-to-prefecture map (~130 entries, `jp_plates.py`) shows the ken
  on plate chips and in the plates table (e.g. 習志野 → 千葉県/Chiba).
- **Face detection** (Apple Vision `VNDetectFaceRectanglesRequest`) on
  chosen event keyframes, stored per event as `faces_json` (idempotent
  migration); boxes render over keyframes in the report with face
  counts in the designation tags.
- **Plate-to-event linkage**: `vs search <plate>` prints linked events;
  plate chips and table rows link to the event anchor in reports. The
  plates table now uses readable columns — Read At (timestamp of the
  clearest OCR read) and Vehicle (tracked vehicle ID) — with an
  explanatory note.
- **GPS in reports**: Location column in the timeline (coordinates at
  event time) and a Location Track panel — an interactive Leaflet map
  (dark CARTO tiles in Machine mode, light in Samaritan, swapped with
  the theme) with the GPS trace, tone-colored event markers and
  popups; falls back to the offline SVG plot when tiles are
  unavailable. Rendered only when the source adapter provided GPS.
- **Reverse geocoding**: with `[report] reverse_geocode = true`
  (default), report generation resolves GPS coordinates to place
  descriptions via OpenStreetMap Nominatim (rate-limited, cached in
  the DB, coordinates-only) — shown in the Location column and the
  track caption, with coordinates preserved on hover/fallback.
- **Linked navigation**: timeline rows link to their event captures
  below; driving-log vehicles link to their first event with readable
  active windows (timestamps, frame spans on hover) instead of raw
  frame numbers; plate table rows link to events. Lightbox zoom now
  applies to every image in the report, not just keyframes.
- **Event categories and scene descriptions**: each event is
  categorized (driving / parking / stationary / unknown) from the
  dashcam mode path segment and GPS speed; events without LLM prose get
  a deterministic scene description synthesized at render time
  (category, speed, G-force, faces, plates, coordinates) — nothing
  extra stored (see DESIGN.md Scene Description Strategy).
- **Recording vs import dates**: the masthead shows the recorded time
  (from the CX-8 filename) alongside the import time; archive imports
  (gap > 1 day) get a warning flag, and the timeline gains an absolute
  Recorded column.

### Changed

- Reports reference keyframes in `frames/<job_id>/` directly instead
  of copying JPEGs into `job_<id>_assets/` — no duplicate assets on
  disk; legacy already-copied paths still resolve.

### Fixed

- Fresh installs crashed on the first tracked clip with
  `ModuleNotFoundError: No module named 'lap'` — ultralytics needs `lap`
  for ByteTrack but does not declare it; it is now an explicit dependency.
- The installer's venv (uv-created) shipped without `pip`; it is now
  seeded via `ensurepip` for manual debugging.
- Ollama client changes:
  - `keep_alive` defaulted to `0` (unload model seconds after each
    request) — and the local server's default now also expires models
    immediately — so every LLM call paid a cold model load. The client
    now keeps models resident for 30 minutes per call, keeping triage
    and detail cheap across a run.
  - Audited against Ollama 0.34.2 with `OLLAMA_CONTEXT_LENGTH=32768`,
    flash attention and q8_0 KV cache: per-request `num_ctx` is still
    honored and `/api/tags` digests are unchanged, so digests and
    health checks are unaffected.
  - Models are now explicitly unloaded when an analyze run ends
    (normal finish, Ctrl+C stop or time budget), so nothing stays
    resident in RAM after the tool exits.
  - Only one model is resident at a time during a run: the client
    tracks the loaded model and evicts the previous one before
    generating with a different model (e.g. gemma3:4b triage →
    gemma4:12b detail swaps, never both). Prevents the ~10 GB
    triage+detail residency that contributed to memory-pressure
    kills on the 16 GB machine.

## [0.1.0] - 2026-09-20

First working release: local, resumable CLI for overnight dashcam /
security-camera footage analysis on Apple Silicon, with the full
cheap-first pipeline (dedup → CV prefilter → local LLM triage → detail).

### Added

- **Core pipeline**: single ffmpeg decode pass with motion + dHash +
  MOG2 dedup gate, OSD timestamp masking, CLAHE night/IR enhancement;
  YOLO + ByteTrack vehicle tracking with weaving/direction analysis;
  per-track license-plate crops OCR'd via Apple Vision (accurate mode)
  with cross-frame consensus voting; scene-text capture (1/30s) in FTS5;
  person/motion/time-of-day threat scoring (intrusion, loitering,
  suspicious_behavior); audio analysis with mlx-whisper, Silero VAD and
  RMS loudness (transcripts, loud regions, distress keywords).
- **LLM passes**: Ollama triage (gemma3:4b, single keyframe, drops
  irrelevant events) and detail (gemma4:12b, multi-keyframe with tile
  escalation), JSON-repair retries, model digests recorded per result.
- **Batch engine**: serialized claim/lease job processing, crash-safe
  resume (`--resume`), stale-job reclaim, dead-letter after 3 attempts,
  time budgets (`--stop-after`), LLM event budget breaker, retention
  pruning, disk preflight + runtime watermark, caffeinate + AC-power
  guards, signal-safe shutdown.
- **Source adapters**: `mazda_cx8` (camera-local filename timestamps →
  UTC, front/rear pairing, mode→priority, NMEA `$GPRMC`/`$GSENS`
  sidecars → `clip_gps_data` plus `hard_brake`/`hard_corner`/`impact`
  events at zero vision cost), `gopro`, `generic`; auto-detection from
  directory layout.
- **`vs import`**: card-swap workflow — scans live cards or date-wrapped
  archives, dedups by content hash (incremental), atomic verified copy
  to `artifact_dir/clips/<date>/<mode>/<channel>/`, registers jobs with
  import ids, sessions and clip metadata; NMEA sidecars travel with
  their clips.
- **Reports + search**: per-job HTML report (timeline, keyframes,
  plates, transcript, GPS/driving log); `vs search` across FTS5 text,
  plates and dangerous-driving kinds.
- **Installer** (`install.sh`): self-contained install to
  `~/.local/opt/video-security` (phototext pattern) — venv snapshot,
  `var/config.toml` bound to the configured artifact dir, catalog
  migrated from `~/.video-security`, PATH wrappers for every entry
  point plus a `vs-dev` workspace wrapper; `--uninstall [--purge]`,
  upgrade by re-running.
- **Tests + benchmarks**: 180 tests including a deterministic golden
  clip (moving bus + person + readable plate), mock Ollama server, and
  throughput benchmarks with floors (decode, YOLO, full prefilter).

### Fixed

- dHash values overflowed SQLite's signed 64-bit integers on real
  footage (~50% of frames); now stored as signed ints, losslessly.
- Real Ollama integration: model digests come from `/api/tags` (the
  client's GET `/api/show` returned 405), and failed-job retries now
  wipe partial rows first instead of hitting unique-constraint errors.
- Mazda NMEA parsing: real `$GSENS` lines carry no NMEA checksum and
  were silently dropped; checksum-less sentences are now accepted.
