import { useQuery } from "@tanstack/react-query"
import { fetchJobGps } from "../../api"
import type { GpsEventMarker, GpsPoint, JobGps } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { toneColor } from "../../theme"

function GpsPlot({ points, events }: { points: GpsPoint[]; events: GpsEventMarker[] }) {
  const lats = points.map((p) => p.lat)
  const lons = points.map((p) => p.lon)
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  const minLon = Math.min(...lons)
  const maxLon = Math.max(...lons)
  const W = 100
  const H = 100
  const x = (lon: number) => 4 + ((lon - minLon) / (maxLon - minLon || 1)) * (W - 8)
  const y = (lat: number) =>
    H - 4 - ((lat - minLat) / (maxLat - minLat || 1)) * (H - 8)
  const line = points
    .map((p) => `${x(p.lon).toFixed(2)},${y(p.lat).toFixed(2)}`)
    .join(" ")
  return (
    <svg
      className="gps-plot"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="GPS track"
    >
      <polyline
        fill="none"
        points={line}
        stroke="var(--vs-ink-dim)"
        strokeWidth="0.7"
        vectorEffect="non-scaling-stroke"
      />
      {events.map((ev) => (
        <circle
          cx={x(ev.lon).toFixed(2)}
          cy={y(ev.lat).toFixed(2)}
          fill={toneColor(ev.tone)}
          key={ev.event_id}
          r="1"
        />
      ))}
    </svg>
  )
}

export function MapTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobGps, Error>({
    queryKey: ["job-gps", jobId],
    queryFn: () => fetchJobGps(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/gps ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">map error: {error.message}</TerminalNote>
  }
  if (!data || data.points.length === 0) {
    return <TerminalNote>no GPS data (adapter-dependent)</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Map</h2>
      <GpsPlot events={data.events} points={data.points} />
      <p className="note">
        {data.points.length} points · {data.events.length} event markers ·
        interactive Leaflet map arrives in phase 8
      </p>
    </section>
  )
}
