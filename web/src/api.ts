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
  faces: number
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
  flag_note: string | null
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
  flag_note: string | null
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
  device: { kind: string; make: string | null; model: string | null } | null
  archived: {
    archived_at: string | null
    original_bytes: number
    proxy_bytes: number | null
    location: string
    deep: boolean
  } | null
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

export interface EventKeyframeBox {
  kind: "person" | "animal"
  track_id: number | null
  box: number[]
}

export interface EventKeyframe {
  url: string
  raw_url: string | null
  faces: number[][]
  face_crops: string[]
  boxes: EventKeyframeBox[]
}

export interface EventPlate {
  track_id: number
  norm_text: string | null
  raw_text: string | null
  confidence: number | null
  ken: string | null
  ken_en: string | null
  crop_url: string | null
  crop_src_url: string | null
  crop_box: number[] | null
  event_id: number | null
}

export interface PlateRow extends EventPlate {
  read_at_sec: number | null
}

export interface EventTrack {
  track_id: number
  first_sec: number | null
  last_sec: number | null
  weaving_score: number | null
  direction: string | null
  strip: string[]
}

export interface EventLocation {
  lat: number
  lon: number
  speed_kmh: number | null
  label: string | null
}

export interface EventDetail extends EventSummary {
  clip_id: number
  track_id: number | null
  keyframes: EventKeyframe[]
  plates: EventPlate[]
  transcript_window: TranscriptSegment[]
  track: EventTrack | null
  location: EventLocation | null
  links: { report: string }
}

export interface EventsPage {
  items: EventSummary[]
  total: number
  limit: number
  offset: number
}

export interface CategoriesPayload {
  categories: Record<string, number>
  types: Record<string, number>
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
  class_id: number
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
  time: number | null
  job_id?: number
  recorded_at?: string | null
  label?: string | null
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

export function fetchEventsPage(params: URLSearchParams): Promise<EventsPage> {
  return fetchJson<EventsPage>(`/api/events?${params.toString()}`)
}

export function fetchCategories(): Promise<CategoriesPayload> {
  return fetchJson<CategoriesPayload>("/api/categories")
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

export interface AppConfig {
  carto_api_key: string | null
}

export function fetchAppConfig(): Promise<AppConfig> {
  return fetchJson<AppConfig>("/api/config")
}

export interface PlateGalleryItem {
  norm_text: string
  sightings: number
  best_confidence: number | null
  best_crop_url: string | null
  crop_src_url: string | null
  crop_box: number[] | null
  ken?: string | null
  jobs: number[]
  first_seen: string | null
  last_seen: string | null
}

export interface PlatesGalleryPage {
  items: PlateGalleryItem[]
  total: number
  limit: number
  offset: number
}

export interface PlateSighting {
  job_id: number
  track_id: number
  confidence: number | null
  read_at_sec: number | null
  recorded_at: string | null
  crop_url: string | null
  crop_src_url: string | null
  crop_box: number[] | null
  event_id: number | null
}

export interface PlateDetail {
  norm_text: string
  ken: string | null
  ken_en: string | null
  sightings: PlateSighting[]
}

export interface SearchPlateHit {
  job_id: number
  track_id: number
  norm_text: string | null
  raw_text: string | null
  confidence: number | null
  crop_url: string | null
}

export interface SearchTextHit {
  job_id: number
  clip_id: number
  frame_number: number
  text: string
}

export interface SearchTranscriptHit {
  job_id: number
  clip_id: number
  start_time: number
  end_time: number
  text: string
}

export interface SearchEventHit {
  job_id: number
  event_id: number
  event_type: string
  start_sec: number
}

export interface SearchSemanticHit {
  event_id: number
  score: number
  snippet: string
}

export interface SearchSemanticTranscriptHit {
  job_id: number
  segment_id: number
  start_time: number
  end_time: number
  text: string
  score: number
}

export interface SearchResults {
  q: string
  plates: SearchPlateHit[]
  text: SearchTextHit[]
  transcripts: SearchTranscriptHit[]
  events: SearchEventHit[]
  semantic_available: boolean
  semantic: SearchSemanticHit[]
  semantic_transcripts: SearchSemanticTranscriptHit[]
}

export interface MapRecentItem {
  event_id: number
  job_id: number
  lat: number
  lon: number
  type: string
  tone: Tone
  recorded_at: string | null
  label: string | null
}

export interface MapRecent {
  items: MapRecentItem[]
}

export function fetchPlatesGallery(
  params: URLSearchParams
): Promise<PlatesGalleryPage> {
  return fetchJson<PlatesGalleryPage>(`/api/plates?${params.toString()}`)
}

export function fetchPlateDetail(normText: string): Promise<PlateDetail> {
  return fetchJson<PlateDetail>(`/api/plates/${encodeURIComponent(normText)}`)
}

export function fetchSearch(q: string): Promise<SearchResults> {
  return fetchJson<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`)
}

export function fetchMapRecent(limit: number): Promise<MapRecent> {
  return fetchJson<MapRecent>(`/api/map/recent?limit=${limit}`)
}

export interface FaceItem {
  job_id: number
  event_id: number
  event_type: string
  tone: Tone
  recorded_at: string | null
  start_sec: number
  crops: string[]
  person_ids: (number | null)[]
}

export interface FacesPage {
  items: FaceItem[]
  total: number
  limit: number
  offset: number
}

export function fetchFaces(params: URLSearchParams): Promise<FacesPage> {
  return fetchJson<FacesPage>(`/api/faces?${params.toString()}`)
}

export interface AnimalKeyframe {
  url: string
  boxes: EventKeyframeBox[]
}

export interface AnimalSighting {
  job_id: number
  event_id: number
  event_type: string
  tone: Tone
  recorded_at: string | null
  start_sec: number
  keyframes: AnimalKeyframe[]
}

export interface AnimalsPage {
  items: AnimalSighting[]
  total: number
  limit: number
  offset: number
}

export function fetchAnimals(params: URLSearchParams): Promise<AnimalsPage> {
  return fetchJson<AnimalsPage>(`/api/animals?${params.toString()}`)
}

export interface PersonSummary {
  person_id: number
  name: string | null
  sightings: number
  representative_crop_url: string | null
  first_seen: string | null
  last_seen: string | null
}

export interface PersonsPage {
  items: PersonSummary[]
  total: number
  limit: number
  offset: number
}

export interface PersonTrackContext {
  track_id: number
  first_frame: number
  last_frame: number
  direction: string | null
}

export interface PersonSighting {
  face_id: number
  job_id: number
  event_id: number
  event_type: string
  tone: Tone
  recorded_at: string | null
  start_sec: number
  quality: number | null
  crop_url: string
  track: PersonTrackContext | null
}

export interface PersonDetail {
  person_id: number
  name: string | null
  sightings: PersonSighting[]
  total: number
}

export function fetchPersons(params: URLSearchParams): Promise<PersonsPage> {
  return fetchJson<PersonsPage>(`/api/persons?${params.toString()}`)
}

export function fetchPersonDetail(id: number): Promise<PersonDetail> {
  return fetchJson<PersonDetail>(`/api/persons/${id}`)
}

export interface JobFaceCrop {
  event_id: number
  event_type: string
  tone: Tone
  start_sec: number
  quality: number | null
  person_id: number | null
  person_name: string | null
  crop_url: string
}

export interface JobFaceGroup {
  person_id: number | null
  count: number
  crops: JobFaceCrop[]
}

export interface JobFaces {
  job_id: number
  total: number
  groups: JobFaceGroup[]
}

export function fetchJobFaces(jobId: number): Promise<JobFaces> {
  return fetchJson<JobFaces>(`/api/jobs/${jobId}/faces`)
}

export interface PersonTrackSighting {
  job_id: number
  track_id: number
  recorded_at: string | null
  first_frame: number
  last_frame: number
  direction: string | null
  n_events: number
  person_id: number | null
  strips: string[]
}

export interface PeopleTracksPage {
  items: PersonTrackSighting[]
  total: number
  limit: number
  offset: number
}

export function fetchPeopleTracks(
  params: URLSearchParams
): Promise<PeopleTracksPage> {
  return fetchJson<PeopleTracksPage>(`/api/people/tracks?${params.toString()}`)
}

export interface WatchlistRow {
  id: number
  kind: string
  pattern: string
  note: string | null
  created_at: string
  hits: number
}

export interface WatchlistHitItem {
  hit_id: number
  watchlist_id: number
  kind: string
  pattern: string
  note: string | null
  job_id: number
  event_id: number | null
  detail: string | null
  created_at: string
}

export interface WatchlistHitsPage {
  items: WatchlistHitItem[]
  total: number
  limit: number
  offset: number
}

export function fetchWatchlists(): Promise<{ items: WatchlistRow[] }> {
  return fetchJson<{ items: WatchlistRow[] }>("/api/watchlists")
}

export function fetchWatchlistHits(
  params: URLSearchParams
): Promise<WatchlistHitsPage> {
  return fetchJson<WatchlistHitsPage>(`/api/watchlist-hits?${params.toString()}`)
}

export interface DayBucket {
  date: string
  jobs: number
  analyzed: number
  events: number
}

export interface DaysResponse {
  days: DayBucket[]
}

export function fetchDays(): Promise<DaysResponse> {
  return fetchJson<DaysResponse>("/api/days")
}

export interface HeatmapCell {
  lat: number
  lon: number
  count: number
  weight: number
}

export interface HeatmapResponse {
  cells: HeatmapCell[]
}

export function fetchAnalyticsHeatmap(): Promise<HeatmapResponse> {
  return fetchJson<HeatmapResponse>("/api/analytics/heatmap")
}

export interface HourBucket {
  hour: number
  count: number
}

export interface HoursResponse {
  hours: HourBucket[]
}

export function fetchAnalyticsHours(): Promise<HoursResponse> {
  return fetchJson<HoursResponse>("/api/analytics/hours")
}

export interface LocationBucket {
  name: string
  count: number
}

export interface LocationsResponse {
  locations: LocationBucket[]
}

export function fetchAnalyticsLocations(): Promise<LocationsResponse> {
  return fetchJson<LocationsResponse>("/api/analytics/locations")
}

export interface RepeatPlate {
  norm_text: string
  raw_text: string
  count: number
  best_confidence: number
  first_seen: string | null
  last_seen: string | null
  first_job: number | null
  last_job: number | null
  crop_url: string | null
}

export interface RepeatPlatesResponse {
  items: RepeatPlate[]
  total: number
}

export function fetchAnalyticsPlates(): Promise<RepeatPlatesResponse> {
  return fetchJson<RepeatPlatesResponse>("/api/analytics/plates")
}
