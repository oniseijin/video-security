import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import {
  fetchAnalyticsHours,
  fetchAnalyticsLocations,
  fetchAnalyticsPlates,
  fetchMapRecent,
  fetchStats,
  fetchWatchlistHits,
} from "../api"
import type {
  GpsEventMarker,
  GpsPoint,
  HoursResponse,
  LocationsResponse,
  MapRecent,
  RepeatPlatesResponse,
  Stats,
  WatchlistHitsPage,
} from "../api"
import { HeatMap } from "../components/HeatMap"
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

function HoursChart({ data }: { data: HoursResponse }) {
  const W = 240
  const H = 120
  const pad = { top: 10, right: 8, bottom: 18, left: 28 }
  const chartW = W - pad.left - pad.right
  const chartH = H - pad.top - pad.bottom
  const maxCount = Math.max(1, ...data.hours.map((h) => h.count))
  const barW = Math.max(2, Math.floor(chartW / 24) - 2)
  const gap = (chartW - barW * 24) / 23
  return (
    <svg
      className="chart-svg"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="events by hour"
    >
      {[0, Math.floor(maxCount / 2), maxCount].map((v, i) => (
        <text
          key={i}
          x={pad.left - 4}
          y={pad.top + chartH - (v / maxCount) * chartH + 3}
          textAnchor="end"
          className="chart-label"
        >
          {v}
        </text>
      ))}
      <line
        x1={pad.left}
        y1={pad.top}
        x2={pad.left}
        y2={pad.top + chartH}
        style={{ stroke: "var(--vs-line)" }}
      />
      <line
        x1={pad.left}
        y1={pad.top + chartH}
        x2={pad.left + chartW}
        y2={pad.top + chartH}
        style={{ stroke: "var(--vs-line)" }}
      />
      {Array.from({ length: 24 }, (_, h) => {
        const bar = data.hours.find((b) => b.hour === h)
        const count = bar ? bar.count : 0
        const barH = Math.max(0, (count / maxCount) * chartH)
        const x = pad.left + h * (barW + gap)
        return (
          <g key={h}>
            <rect
              x={x}
              y={pad.top + chartH - barH}
              width={barW}
              height={barH}
              style={{ fill: "var(--vs-accent)", opacity: 0.7 + (count / maxCount) * 0.3 }}
            />
            {h % 3 === 0 ? (
              <text
                x={x + barW / 2}
                y={H - 3}
                textAnchor="middle"
                className="chart-label"
              >
                {h}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}

function Analytics() {
  const hours = useQuery<HoursResponse, Error>({
    queryKey: ["analytics-hours"],
    queryFn: fetchAnalyticsHours,
  })
  const locations = useQuery<LocationsResponse, Error>({
    queryKey: ["analytics-locations"],
    queryFn: fetchAnalyticsLocations,
  })
  const plates = useQuery<RepeatPlatesResponse, Error>({
    queryKey: ["analytics-plates"],
    queryFn: fetchAnalyticsPlates,
  })

  return (
    <>
      <section className="panel">
        <h2>Route Heatmap</h2>
        <HeatMap height={360} />
        <p className="note">decimated GPS points</p>
      </section>
      <section className="panel">
        <h2>Events by Hour</h2>
        {hours.isPending ? (
          <TerminalNote>loading...</TerminalNote>
        ) : hours.isError ? (
          <TerminalNote tone="threat">hours error: {hours.error.message}</TerminalNote>
        ) : !hours.data || hours.data.hours.length === 0 ? (
          <TerminalNote>no event hour data</TerminalNote>
        ) : (
          <HoursChart data={hours.data} />
        )}
      </section>
      <section className="panel">
        <h2>Repeat Plates</h2>
        {plates.isPending ? (
          <TerminalNote>loading...</TerminalNote>
        ) : plates.isError ? (
          <TerminalNote tone="threat">plates error: {plates.error.message}</TerminalNote>
        ) : !plates.data || plates.data.items.length === 0 ? (
          <TerminalNote>no repeat plates yet</TerminalNote>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Plate</th>
                <th className="num">Seen</th>
                <th>First</th>
                <th>Last</th>
                <th className="num">Conf</th>
              </tr>
            </thead>
            <tbody>
              {plates.data.items.map((pl) => (
                <tr key={pl.norm_text}>
                  <td>
                    <Link to={`/plates/${encodeURIComponent(pl.norm_text)}`}>
                      {pl.norm_text}
                    </Link>
                  </td>
                  <td className="num">{pl.count}</td>
                  <td>
                    {fmtDate(pl.first_seen)}
                    {pl.first_job != null ? ` · ${pl.first_job}` : ""}
                  </td>
                  <td>
                    {fmtDate(pl.last_seen)}
                    {pl.last_job != null ? ` · ${pl.last_job}` : ""}
                  </td>
                  <td className="num">{pl.best_confidence.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="panel">
        <h2>Top Locations</h2>
        {locations.isPending ? (
          <TerminalNote>loading...</TerminalNote>
        ) : locations.isError ? (
          <TerminalNote tone="threat">locations error: {locations.error.message}</TerminalNote>
        ) : !locations.data || locations.data.locations.length === 0 ? (
          <TerminalNote>no location data</TerminalNote>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Location</th>
                <th className="num">Events</th>
              </tr>
            </thead>
            <tbody>
              {locations.data.locations.map((loc) => (
                <tr key={loc.name}>
                  <td>{loc.name}</td>
                  <td className="num">{loc.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
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
      <Analytics />
    </>
  )
}
