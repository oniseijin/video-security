import { useEffect, useState } from "react"
import { Lightbox } from "./Lightbox"
import type { LightboxFrame } from "./Lightbox"
import { FaceBoxes, toFaceBoxes } from "./FaceBoxes"

export interface FilmFrame {
  url: string
  raw_url?: string | null
  faces: number[][]
  caption: string
}

interface FilmstripProps {
  frames: FilmFrame[]
  allowRaw?: boolean
}

export function Filmstrip({ frames, allowRaw = false }: FilmstripProps) {
  const [active, setActive] = useState(0)
  const [raw, setRaw] = useState(false)
  const [lightbox, setLightbox] = useState(-1)

  useEffect(() => {
    setActive(0)
    setRaw(false)
  }, [frames])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (lightbox >= 0) {
        return
      }
      const target = e.target as HTMLElement
      if (
        target.tagName === "INPUT" ||
        target.tagName === "SELECT" ||
        target.tagName === "TEXTAREA"
      ) {
        return
      }
      if (e.key === "ArrowRight") {
        setActive((i) => Math.min(frames.length - 1, i + 1))
      } else if (e.key === "ArrowLeft") {
        setActive((i) => Math.max(0, i - 1))
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [frames.length, lightbox])

  if (frames.length === 0) {
    return null
  }
  const frame = frames[Math.min(active, frames.length - 1)]
  const useRaw = raw && allowRaw && frame.raw_url !== null && frame.raw_url !== undefined
  const src = useRaw ? (frame.raw_url as string) : frame.url
  const lightboxFrames: LightboxFrame[] = frames.map((f) => ({
    url: raw && allowRaw && f.raw_url ? f.raw_url : f.url,
    faces: f.faces,
    caption: f.caption,
  }))
  const hasRaw = allowRaw && frames.some((f) => f.raw_url)

  return (
    <div className="filmstrip">
      <figure className="subject kf-fig">
        <span className="designation">
          {frame.caption}
        </span>
        <div className="kf-wrap">
          <img
            alt={frame.caption}
            onClick={() => setLightbox(active)}
            src={src}
          />
          <FaceBoxes boxes={toFaceBoxes(frame.faces)} />
        </div>
      </figure>
      <div className="filmstrip-controls">
        <button
          className="app-btn"
          disabled={active === 0}
          onClick={() => setActive((i) => Math.max(0, i - 1))}
          type="button"
        >
          ◀ PREV
        </button>
        <input
          aria-label="Frame scrubber"
          max={frames.length - 1}
          min={0}
          onChange={(e) => setActive(Number(e.target.value))}
          type="range"
          value={active}
        />
        <button
          className="app-btn"
          disabled={active >= frames.length - 1}
          onClick={() => setActive((i) => Math.min(frames.length - 1, i + 1))}
          type="button"
        >
          NEXT ▶
        </button>
        <span className="lb-level">
          {active + 1}/{frames.length}
        </span>
        {hasRaw ? (
          <button className="app-btn" onClick={() => setRaw((r) => !r)} type="button">
            {raw ? "ENHANCED" : "RAW"}
          </button>
        ) : null}
      </div>
      <div className="filmstrip-thumbs">
        {frames.map((f, i) => (
          <button
            className={"film-thumb" + (i === active ? " active" : "")}
            key={f.url}
            onClick={() => setActive(i)}
            type="button"
          >
            <img
              alt={f.caption}
              src={raw && allowRaw && f.raw_url ? f.raw_url : f.url}
            />
          </button>
        ))}
      </div>
      <Lightbox
        frames={lightboxFrames}
        index={lightbox}
        onClose={() => setLightbox(-1)}
        onIndex={setLightbox}
      />
    </div>
  )
}
