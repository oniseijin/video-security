import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import * as L from "leaflet"
import "leaflet/dist/leaflet.css"
import { fetchAnalyticsHeatmap, fetchAppConfig } from "../api"
import type { AppConfig, HeatmapResponse } from "../api"
import { TOKENS } from "../theme"
import type { Theme } from "../theme"

const DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
const LIGHT_TILES = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>'
const TILE_TIMEOUT_MS = 7000

function themeAttr(): Theme {
  const raw = document.documentElement.getAttribute("data-theme")
  return raw === "samaritan" ? "samaritan" : "machine"
}

const RADIUS_MIN = 5
const RADIUS_MAX = 30
const OPACITY_MIN = 0.12
const OPACITY_MAX = 0.55

export function HeatMap({ height = 360 }: { height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [theme, setTheme] = useState<Theme>(themeAttr)
  const [offline, setOffline] = useState(false)
  const { data: appConfig, isPending: configPending } = useQuery<AppConfig, Error>({
    queryKey: ["app-config"],
    queryFn: fetchAppConfig,
    staleTime: Infinity,
  })
  const { data, isPending, isError } = useQuery<HeatmapResponse, Error>({
    queryKey: ["analytics-heatmap"],
    queryFn: fetchAnalyticsHeatmap,
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
    if (offline || configPending || !data || data.cells.length === 0 || !container) {
      return
    }
    const apiKey = appConfig?.carto_api_key ?? null
    const base = theme === "machine" ? DARK_TILES : LIGHT_TILES
    const url = apiKey ? `${base}?key=${encodeURIComponent(apiKey)}` : base
    const tone = TOKENS[theme].threat
    const map = L.map(container, { zoomControl: true, attributionControl: true })
    const layer = L.tileLayer(url, { attribution: ATTRIBUTION }).addTo(map)
    const latlngs: [number, number][] = []
    for (const cell of data.cells) {
      const radius = RADIUS_MIN + (RADIUS_MAX - RADIUS_MIN) * cell.weight
      const opacity = OPACITY_MIN + (OPACITY_MAX - OPACITY_MIN) * cell.weight
      L.circleMarker([cell.lat, cell.lon], {
        radius,
        color: tone,
        fillColor: tone,
        fillOpacity: opacity,
        weight: 0,
      }).addTo(map)
      latlngs.push([cell.lat, cell.lon])
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
  }, [appConfig, configPending, data, offline, theme])

  if (offline || isError || (data && data.cells.length === 0)) {
    return (
      <div>
        <div className="gps-plot" style={{ height }} />
        <p className="note">
          {offline
            ? "map tiles unavailable (offline)"
            : isError
              ? "heatmap error"
              : "no GPS data"}
        </p>
      </div>
    )
  }
  if (isPending || configPending) {
    return (
      <div>
        <div className="gps-plot" style={{ height }} />
        <p className="note">loading heatmap...</p>
      </div>
    )
  }
  return <div className="track-map" ref={containerRef} style={{ height }} />
}