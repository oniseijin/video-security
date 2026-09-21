import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchMapRecent, fetchStats, fetchWatchlistHits } from "../api"
import type {
  GpsEventMarker,
  GpsPoint,
  MapRecent,
  Stats,
  WatchlistHitsPage,
} from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { TrackMap } from "../components/TrackMap"
import { fmtBytes, fmtDate } from "../format"
import { toneColor } from "../theme"

function progressPct(current: number, total: number | null): number {
  if (total === null || total <= 0) {
    return 0
  }
  return Math.min(100, Math.round((current / total) * 100))
}

function WatchlistBanner() {
  const { data } = useQuery<WatchlistHitsPage, Error>({
    queryKey: ["watchlist-hits-banner"],
    queryFn: () => fetchWatchlistHits(new URLSearchParams({ limit: "5" })),
    refetchInterval: 15000,
  })
  if (!data || data.total === 0) {
    return null
  }
  return (
    <section className="panel threat">
      <h2>
        <Link to="/events">WATCHLIST — {data.total} hit(s)</Link>
      </h2>
      <div className="terminal">
        {data.items.map((h) => (
          <p key={h.hit_id}>
            [{h.kind}] {h.pattern} matched {h.detail} · job {h.job_id}
            {h.note != null && h.note !== "" ? ` · ${h.note}` : ""}
          </p>
        ))}
      </div>
    </section>
  )
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}

function RecentEvents() {
  const { data, isPending, isError, error } = useQuery<MapRecent, Error>({
    queryKey: ["map-recent"],
    queryFn: () => fetchMapRecent(200),
  })

  if (isPending) {
    return <TerminalNote>querying /api/map/recent ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">map error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return null
  }

  const points: GpsPoint[] = data.items.map((item) => ({
    t: 0,
    lat: item.lat,
    lon: item.lon,
    speed: null,
    bearing: null,
  }))
  const events: GpsEventMarker[] = data.items.map((item) => ({
    event_id: item.event_id,
    lat: item.lat,
    lon: item.lon,
    type: item.type,
    tone: item.tone,
    time: null,
    job_id: item.job_id,
    recorded_at: item.recorded_at,
    label: item.label,
  }))

  return (
    <section className="panel">
      <h2>Recent Events</h2>
      <TrackMap events={events} height={340} points={points} route={false} />
      <p className="note">recent {data.items.length} events with GPS</p>
    </section>
  )
}

export function Dashboard() {
  const { data, isPending, isError, error } = useQuery<Stats, Error>({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: 5000,
  })

  if (isPending) {
    return <TerminalNote>querying /api/stats ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">stats error: {error.message}</TerminalNote>
  }
  if (!data) {
    return null
  }

  const pending = data.jobs.by_status.pending ?? 0
  const done = data.jobs.by_status.done ?? 0
  const active = data.active_job
  const storage = data.storage
  const usedPct =
    storage.free_gb !== null &&
    storage.total_gb !== null &&
    storage.total_gb > 0
      ? Math.min(
          100,
          Math.round(
            ((storage.total_gb - storage.free_gb) / storage.total_gb) * 100
          )
        )
      : null

  return (
    <>
      <WatchlistBanner />
      <section className="panel">
        <h2>System</h2>
        <div className="stat-grid">
          <Stat value={data.jobs.total} label="Jobs" />
          <Stat value={pending} label="Pending" />
          <Stat value={done} label="Done" />
          <Stat value={data.events} label="Events" />
          <Stat value={data.plates} label="Plates" />
          <Stat value={data.faces} label="Faces" />
          <Stat value={data.frames_kept} label="Frames Kept" />
          <Stat value={data.transcript_segments} label="Transcript" />
        </div>
      </section>
      <section className="panel">
        <h2>Jobs By Status</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th className="num">Jobs</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.jobs.by_status).map(([status, count]) => (
              <tr
                key={status}
                className={status === "failed" ? "row-threat" : undefined}
              >
                <td>{status}</td>
                <td className="num">{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Active Job</h2>
        {active ? (
          <div>
            <p className="active-line">
              JOB {active.id} — stage {active.stage} — frame {active.current_frame}
              {active.total_frames !== null ? ` / ${active.total_frames}` : ""}
            </p>
            <div className="progress">
              <div
                className="progress-fill"
                style={{
                  width: `${progressPct(active.current_frame, active.total_frames)}%`,
                  background: toneColor("info"),
                }}
              />
            </div>
          </div>
        ) : (
          <TerminalNote>no active job</TerminalNote>
        )}
      </section>
      <section className="panel">
        <h2>Import History</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Import</th>
              <th className="num">Jobs</th>
              <th className="num">Done</th>
              <th>First</th>
              <th>Last</th>
            </tr>
          </thead>
          <tbody>
            {data.imports.map((row) => (
              <tr key={row.import_id}>
                <td>{row.import_id}</td>
                <td className="num">{row.jobs}</td>
                <td className="num">{row.done}</td>
                <td>{fmtDate(row.first_at)}</td>
                <td>{fmtDate(row.last_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Storage</h2>
        <p className="path-note">{storage.artifact_dir}</p>
        {usedPct !== null ? (
          <div>
            <div className="progress">
              <div
                className="progress-fill progress-fill--storage"
                style={{ width: `${usedPct}%` }}
              />
            </div>
            <p className="note">
              {storage.free_gb} GB free of {storage.total_gb} GB — db{" "}
              {fmtBytes(storage.db_bytes)}
            </p>
          </div>
        ) : (
          <p className="note">
            artifact dir offline — db {fmtBytes(storage.db_bytes)}
          </p>
        )}
      </section>
      <RecentEvents />
    </>
  )
}
