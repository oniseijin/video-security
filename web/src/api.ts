import type { Tone } from "./theme"

export async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

export interface StatsByStatus {
  [status: string]: number
}

export interface ActiveJob {
  id: number
  stage: string
  current_frame: number
  total_frames: number | null
}

export interface ImportRow {
  import_id: string
  jobs: number
  done: number
  first_at: string | null
  last_at: string | null
}

export interface StorageInfo {
  artifact_dir: string
  free_gb: number | null
  total_gb: number | null
  db_bytes: number
}

export interface Stats {
  jobs: { total: number; by_status: StatsByStatus }
  events: number
  plates: number
  plates_with_crops: number
  frames_kept: number
  transcript_segments: number
  active_job: ActiveJob | null
  storage: StorageInfo
  imports: ImportRow[]
}

export interface JobItem {
  id: number
  status: string
  mode: string | null
  channel: string | null
  recorded_at: string | null
  imported_at: string | null
  import_id: string | null
  archive: boolean
  duration_sec: number | null
  counts: { events: number; plates: number; faces: number }
  has_gps: boolean
  has_transcript: boolean
  pair_job_id: number | null
}

export interface JobsPage {
  items: JobItem[]
  total: number
  limit: number
  offset: number
}

export function fetchStats(): Promise<Stats> {
  return fetchJson<Stats>("/api/stats")
}

export function fetchJobs(params: URLSearchParams): Promise<JobsPage> {
  return fetchJson<JobsPage>(`/api/jobs?${params.toString()}`)
}

export interface JobClip {
  clip_id: number
  filename: string
  channel: string
  recording_start_utc: string | null
  duration_sec: number | null
  lighting: string | null
  has_audio: boolean
}

export interface JobDetail {
  id: number
  status: string
  video_path: string
  recorded_at: string | null
  imported_at: string | null
  import_id: string | null
  archive: boolean
  mode: string
  channel: string | null
  duration_sec: number | null
  has_audio: boolean
  has_gps: boolean
  has_transcript: boolean
  pair: { job_id: number; channel: string | null } | null
  counts: {
    events: number
    plates: number
    faces: number
    transcript_segments: number
    frames_kept: number
  }
  event_types: Record<string, number>
  status_counts: Record<string, number>
  clips: JobClip[]
}

export interface EventSummary {
  event_id: number
  job_id: number
  event_type: string
  category: string
  start_sec: number
  end_sec: number
  recorded_at: string | null
  priority: number
  detector_score: number
  status: string
  tone: Tone
  description: string
  description_source: string
  face_count: number
  plate_norm: string | null
}

export interface EventKeyframe {
  url: string
  raw_url: string | null
  faces: number[][][]
}

export interface EventPlate {
  track_id: number
  norm_text: string | null
  raw_text: string | null
  confidence: number | null
  ken: string | null
  ken_en: string | null
  crop_url: string | null
  event_id: number | null
}

export interface PlateRow extends EventPlate {
  read_at_sec: number | null
}

export interface EventDetail extends EventSummary {
  clip_id: number
  track_id: number | null
  keyframes: EventKeyframe[]
  plates: EventPlate[]
  transcript_window: TranscriptSegment[]
}

export interface JobEventsPage {
  items: EventSummary[]
}

export interface JobPlatesPage {
  items: PlateRow[]
}

export interface TrackRow {
  track_id: number
  clip_id: number
  first_sec: number | null
  last_sec: number | null
  weaving_score: number | null
  direction: string | null
  plate_norm: string | null
  event_ids: number[]
  strip: string[]
}

export interface JobTracksPage {
  items: TrackRow[]
}

export interface GpsPoint {
  t: number
  lat: number
  lon: number
  speed: number | null
  bearing: number | null
}

export interface GpsEventMarker {
  event_id: number
  lat: number
  lon: number
  type: string
  tone: Tone
  time: number
}

export interface JobGps {
  points: GpsPoint[]
  events: GpsEventMarker[]
}

export interface TranscriptSegment {
  start_time: number
  end_time: number
  text: string
  language: string | null
}

export interface JobTranscriptPage {
  items: TranscriptSegment[]
}

export function fetchJobDetail(id: number): Promise<JobDetail> {
  return fetchJson<JobDetail>(`/api/jobs/${id}`)
}

export function fetchJobEvents(id: number): Promise<JobEventsPage> {
  return fetchJson<JobEventsPage>(`/api/jobs/${id}/events`)
}

export function fetchEventDetail(id: number): Promise<EventDetail> {
  return fetchJson<EventDetail>(`/api/events/${id}`)
}

export function fetchJobPlates(id: number): Promise<JobPlatesPage> {
  return fetchJson<JobPlatesPage>(`/api/jobs/${id}/plates`)
}

export function fetchJobTracks(id: number): Promise<JobTracksPage> {
  return fetchJson<JobTracksPage>(`/api/jobs/${id}/tracks`)
}

export function fetchJobGps(id: number): Promise<JobGps> {
  return fetchJson<JobGps>(`/api/jobs/${id}/gps`)
}

export function fetchJobTranscript(id: number): Promise<JobTranscriptPage> {
  return fetchJson<JobTranscriptPage>(`/api/jobs/${id}/transcript`)
}
