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
