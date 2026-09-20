import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

export interface PlayTick {
  event_id: number
  event_type: string
  tone: Tone
  start_sec: number
}

interface DualPlayerProps {
  jobId: number
  frontSrc: string
  rearSrc: string | null
  events: PlayTick[]
}

const SYNC_INTERVAL_MS = 250
const DRIFT_SEC = 0.25

export function DualPlayer({ jobId, frontSrc, rearSrc, events }: DualPlayerProps) {
  const frontRef = useRef<HTMLVideoElement>(null)
  const rearRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)

  const seek = (t: number) => {
    const front = frontRef.current
    const rear = rearRef.current
    if (front) {
      front.currentTime = t
    }
    if (rear) {
      rear.currentTime = t
    }
    setCurrent(t)
  }

  useEffect(() => {
    if (!playing) {
      return
    }
    const id = window.setInterval(() => {
      const front = frontRef.current
      const rear = rearRef.current
      if (front) {
        setCurrent(front.currentTime)
      }
      if (front && rear && Math.abs(front.currentTime - rear.currentTime) > DRIFT_SEC) {
        rear.currentTime = front.currentTime
      }
    }, SYNC_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [playing])

  const toggle = () => {
    const front = frontRef.current
    const rear = rearRef.current
    if (!front) {
      return
    }
    if (playing) {
      front.pause()
      rear?.pause()
      setPlaying(false)
      return
    }
    void front.play().catch(() => setPlaying(false))
    if (rear) {
      void rear.play().catch(() => undefined)
    }
    setPlaying(true)
  }

  return (
    <div className="dual-player">
      <div className="player-grid">
        <figure className="player-fig">
          <video
            className="player-video"
            controls
            preload="metadata"
            ref={frontRef}
            src={frontSrc}
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
            onEnded={() => {
              rearRef.current?.pause()
              setPlaying(false)
            }}
          />
          <figcaption>front</figcaption>
        </figure>
        {rearSrc ? (
          <figure className="player-fig">
            <video
              className="player-video"
              controls
              preload="metadata"
              ref={rearRef}
              src={rearSrc}
            />
            <figcaption>rear</figcaption>
          </figure>
        ) : null}
      </div>
      {duration > 0 ? (
        <div className="tick-track">
          {events.map((ev) => (
            <span
              className="event-tick-wrap"
              key={ev.event_id}
              style={{ left: `${Math.min(100, (ev.start_sec / duration) * 100)}%` }}
            >
              <button
                className="event-tick"
                onClick={() => seek(ev.start_sec)}
                style={{ background: toneColor(ev.tone) }}
                title={`${ev.event_type} ${fmtSec(ev.start_sec)}`}
                type="button"
              />
              <Link
                className="tick-link"
                title={`open event #${ev.event_id}`}
                to={`/jobs/${jobId}/events/${ev.event_id}`}
              >
                ⧉
              </Link>
            </span>
          ))}
        </div>
      ) : null}
      <div className="player-controls">
        <button className="app-btn" onClick={toggle} type="button">
          {playing ? "pause" : "play"}
        </button>
        <input
          aria-label="seek"
          max={duration}
          min={0}
          onChange={(e) => seek(Number(e.target.value))}
          step={0.1}
          type="range"
          value={Math.min(current, duration)}
        />
        <span className="player-readout">
          {fmtSec(current)} / {fmtSec(duration)}
        </span>
      </div>
    </div>
  )
}
