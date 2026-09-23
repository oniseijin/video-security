# Screenshots

Every major visual element of the design system, captured from a real dashcam
job (front/rear pair, GPS track, plates, transcript) in both themes:
**Machine** (dark) and **Samaritan** (light). One design system drives both the
HTML report and the web console (`report_theme.py` → `--vs-*` tokens).

The captures are redacted for publishing: photographic content (keyframes,
plate/face crops, map tiles, video) is blurred at capture time, and the shots
are served from a scrubbed DB clone — plate numbers keep their prefecture
ken but the rest is masked (`千葉■■■`), transcripts / scene OCR text /
reverse-geocoded place names / LLM scene descriptions are replaced with
`[redacted]`, and file paths are blurred. See `scripts/scrub_db.py`.

Regenerate everything after design changes:

```bash
npm --prefix web run shots            # spawns vs serve, drives headless Chromium
npm --prefix web run shots -- --job 355   # pin the featured job
```

Images land in `docs/images/` as `<surface>-<theme>.png` (1440×900 @2x).

## Report — `vs report <job-id>`

Rendered on demand from the DB; also embedded in the web console's Report tab.

### Masthead + summary

| Machine | Samaritan |
|---|---|
| ![report top, machine theme](images/report-top-machine.png) | ![report top, samaritan theme](images/report-top-samaritan.png) |

### Summary panel

| Machine | Samaritan |
|---|---|
| ![report summary, machine theme](images/report-summary-machine.png) | ![report summary, samaritan theme](images/report-summary-samaritan.png) |

### Timeline

| Machine | Samaritan |
|---|---|
| ![report timeline, machine theme](images/report-timeline-machine.png) | ![report timeline, samaritan theme](images/report-timeline-samaritan.png) |

### Location track

| Machine | Samaritan |
|---|---|
| ![report location track, machine theme](images/report-location-track-machine.png) | ![report location track, samaritan theme](images/report-location-track-samaritan.png) |

### Keyframes

| Machine | Samaritan |
|---|---|
| ![report keyframes, machine theme](images/report-keyframes-machine.png) | ![report keyframes, samaritan theme](images/report-keyframes-samaritan.png) |

### Plates

| Machine | Samaritan |
|---|---|
| ![report plates, machine theme](images/report-plates-machine.png) | ![report plates, samaritan theme](images/report-plates-samaritan.png) |

### Transcript

| Machine | Samaritan |
|---|---|
| ![report transcript, machine theme](images/report-transcript-machine.png) | ![report transcript, samaritan theme](images/report-transcript-samaritan.png) |

### Driving log

| Machine | Samaritan |
|---|---|
| ![report driving log, machine theme](images/report-driving-log-machine.png) | ![report driving log, samaritan theme](images/report-driving-log-samaritan.png) |

### Full report

- [Machine (full page)](images/report-full-machine.png)
- [Samaritan (full page)](images/report-full-samaritan.png)

## Web console — global routes (`vs serve`)

### Dashboard

Stats, active-job progress, import history, activity heatmap.

| Machine | Samaritan |
|---|---|
| ![dashboard, machine theme](images/web-dashboard-machine.png) | ![dashboard, samaritan theme](images/web-dashboard-samaritan.png) |

### Jobs list

| Machine | Samaritan |
|---|---|
| ![jobs list, machine theme](images/web-jobs-machine.png) | ![jobs list, samaritan theme](images/web-jobs-samaritan.png) |

### Events browser

| Machine | Samaritan |
|---|---|
| ![events, machine theme](images/web-events-machine.png) | ![events, samaritan theme](images/web-events-samaritan.png) |

### Timeline

| Machine | Samaritan |
|---|---|
| ![timeline, machine theme](images/web-timeline-machine.png) | ![timeline, samaritan theme](images/web-timeline-samaritan.png) |

### Plates gallery

| Machine | Samaritan |
|---|---|
| ![plates gallery, machine theme](images/web-plates-machine.png) | ![plates gallery, samaritan theme](images/web-plates-samaritan.png) |

### Plate detail

| Machine | Samaritan |
|---|---|
| ![plate detail, machine theme](images/web-plate-detail-machine.png) | ![plate detail, samaritan theme](images/web-plate-detail-samaritan.png) |

### Faces

| Machine | Samaritan |
|---|---|
| ![faces, machine theme](images/web-faces-machine.png) | ![faces, samaritan theme](images/web-faces-samaritan.png) |

### Search

| Machine | Samaritan |
|---|---|
| ![search, machine theme](images/web-search-machine.png) | ![search, samaritan theme](images/web-search-samaritan.png) |

## Web console — job tabs (`/jobs/<id>/<tab>`)

### Captures

| Machine | Samaritan |
|---|---|
| ![job captures, machine theme](images/web-job-captures-machine.png) | ![job captures, samaritan theme](images/web-job-captures-samaritan.png) |

### Events

| Machine | Samaritan |
|---|---|
| ![job events, machine theme](images/web-job-events-machine.png) | ![job events, samaritan theme](images/web-job-events-samaritan.png) |

### Map

| Machine | Samaritan |
|---|---|
| ![job map, machine theme](images/web-job-map-machine.png) | ![job map, samaritan theme](images/web-job-map-samaritan.png) |

### Plates

| Machine | Samaritan |
|---|---|
| ![job plates, machine theme](images/web-job-plates-machine.png) | ![job plates, samaritan theme](images/web-job-plates-samaritan.png) |

### Playback

Dual front/rear synchronized playback with an event-tick timeline.

| Machine | Samaritan |
|---|---|
| ![job playback, machine theme](images/web-job-playback-machine.png) | ![job playback, samaritan theme](images/web-job-playback-samaritan.png) |

### Transcript

| Machine | Samaritan |
|---|---|
| ![job transcript, machine theme](images/web-job-transcript-machine.png) | ![job transcript, samaritan theme](images/web-job-transcript-samaritan.png) |

### Report (embedded)

| Machine | Samaritan |
|---|---|
| ![job report tab, machine theme](images/web-job-report-machine.png) | ![job report tab, samaritan theme](images/web-job-report-samaritan.png) |

## Detail routes

### Event detail

Filmstrip with keyframe navigation.

| Machine | Samaritan |
|---|---|
| ![event detail, machine theme](images/web-event-detail-machine.png) | ![event detail, samaritan theme](images/web-event-detail-samaritan.png) |

### Track detail

| Machine | Samaritan |
|---|---|
| ![track detail, machine theme](images/web-track-detail-machine.png) | ![track detail, samaritan theme](images/web-track-detail-samaritan.png) |

## Overlays

### Lightbox

Wheel zoom, pan, face-detection boxes.

| Machine | Samaritan |
|---|---|
| ![lightbox, machine theme](images/web-lightbox-machine.png) | ![lightbox, samaritan theme](images/web-lightbox-samaritan.png) |
