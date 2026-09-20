import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import * as L from "leaflet"
import "leaflet/dist/leaflet.css"
import { fetchAppConfig } from "../api"
import type { AppConfig, GpsEventMarker, GpsPoint } from "../api"
import { fmtDate, fmtSec } from "../format"
import { TOKENS, themeAttr, toneColor } from "../theme"
import type { Theme, Tone } from "../theme"

const DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
const LIGHT_TILES = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>'
const TILE_TIMEOUT_MS = 7000

interface TrackMapProps {
  points: GpsPoint[]
  events: GpsEventMarker[]
  height: number
  jobId?: number
  route?: boolean
}

function toneHex(tone: Tone, theme: Theme): string {
  return TOKENS[theme][tone]
}

function lineColor(theme: Theme): string {
  return theme === "machine" ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.55)"
}

function popupLines(ev: GpsEventMarker, jobId: number | undefined): string {
  const lines: string[] = []
  if (ev.recorded_at != null) {
    lines.push(`${ev.type} · ${fmtDate(ev.recorded_at)}`)
    if (ev.label != null && ev.label !== "") {
      lines.push(ev.label)
    }
  } else {
    lines.push(
      ev.time !== null ? `${ev.type} @ ${fmtSec(ev.time)}` : `${ev.type}`
    )
    lines.push(`${ev.lat.toFixed(5)}, ${ev.lon.toFixed(5)}`)
  }
  const linkJob = jobId ?? ev.job_id
  if (linkJob !== undefined) {
    lines.push(
      `<a href="/jobs/${linkJob}/events/${ev.event_id}">event #${ev.event_id}</a>`
    )
  }
  return lines.join("<br>")
}

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
        strokeWidth="0.7"
        style={{ stroke: "var(--vs-ink-dim)" }}
        vectorEffect="non-scaling-stroke"
      />
      {events.map((ev) => (
        <circle
          cx={x(ev.lon).toFixed(2)}
          cy={y(ev.lat).toFixed(2)}
          key={ev.event_id}
          r="1"
          style={{ fill: toneColor(ev.tone) }}
        />
      ))}
    </svg>
  )
}

export function TrackMap({
  points,
  events,
  height,
  jobId,
  route = true,
}: TrackMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [theme, setTheme] = useState<Theme>(themeAttr)
  const [offline, setOffline] = useState(false)
  const { data: appConfig, isPending: configPending } = useQuery<AppConfig, Error>({
    queryKey: ["app-config"],
    queryFn: fetchAppConfig,
    staleTime: Infinity,
  })

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(themeAttr()))
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (offline || configPending || points.length < 2 || !container) {
      return
    }
    const apiKey = appConfig?.carto_api_key ?? null
    const base = theme === "machine" ? DARK_TILES : LIGHT_TILES
    const url = apiKey ? `${base}?api_key=${encodeURIComponent(apiKey)}` : base
    const map = L.map(container, { zoomControl: true, attributionControl: true })
    const layer = L.tileLayer(url, { attribution: ATTRIBUTION }).addTo(map)
    const latlngs = points.map((p) => [p.lat, p.lon] as [number, number])
    if (route) {
      L.polyline(latlngs, {
        color: lineColor(theme),
        weight: 2,
        opacity: 0.9,
      }).addTo(map)
      L.circleMarker(latlngs[0], {
        radius: 5,
        color: toneHex("asset", theme),
        fill: false,
      })
        .addTo(map)
        .bindPopup("start")
      L.circleMarker(latlngs[latlngs.length - 1], {
        radius: 5,
        color: toneHex("asset", theme),
        fillOpacity: 1,
      })
        .addTo(map)
        .bindPopup("end")
    }
    for (const ev of events) {
      const color = toneHex(ev.tone, theme)
      const marker = L.circleMarker([ev.lat, ev.lon], {
        radius: 6,
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      }).addTo(map)
      marker.bindPopup(popupLines(ev, jobId))
    }
    map.fitBounds(L.latLngBounds(latlngs).pad(0.15))
    let loaded = false
    const onTileLoad = () => {
      loaded = true
    }
    const onTileError = () => setOffline(true)
    layer.on("tileload", onTileLoad)
    layer.on("tileerror", onTileError)
    const timer = window.setTimeout(() => {
      if (!loaded) {
        setOffline(true)
      }
    }, TILE_TIMEOUT_MS)
    return () => {
      window.clearTimeout(timer)
      map.remove()
    }
  }, [appConfig, configPending, events, jobId, offline, points, route, theme])

  if (offline || points.length < 2) {
    return (
      <div>
        <GpsPlot events={events} points={points} />
        <p className="note">
          {offline ? "map tiles unavailable (offline)" : "insufficient GPS points"}
        </p>
      </div>
    )
  }
  return <div className="track-map" ref={containerRef} style={{ height }} />
}
