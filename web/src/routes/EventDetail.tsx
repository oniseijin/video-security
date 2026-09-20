import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { fetchEventDetail } from "../api"
import type { EventDetail } from "../api"
import { Filmstrip } from "../components/Filmstrip"
import type { FilmFrame } from "../components/Filmstrip"
import { TerminalNote } from "../components/TerminalNote"
import { fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

export function EventDetailRoute() {
  const { eid } = useParams()
  const eventId = Number(eid)
  const { data, isPending, isError, error } = useQuery<EventDetail, Error>({
    queryKey: ["event", eventId],
    queryFn: () => fetchEventDetail(eventId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/events/{eventId} ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">event error: {error.message}</TerminalNote>
  }
  if (!data) {
    return <TerminalNote>event {eventId} not found</TerminalNote>
  }

  const frames: FilmFrame[] = data.keyframes.map((kf, i) => ({
    url: kf.url,
    raw_url: kf.raw_url,
    faces: kf.faces,
    caption: `${data.event_type} // ${fmtSec(data.start_sec)} // frame ${i + 1}`,
  }))

  return (
    <>
      <section className="panel">
        <h2>
          Event {data.event_id}
          <span
            className="tone-tag"
            style={{ color: toneColor(data.tone as Tone) }}
          >
            {data.event_type}
          </span>
        </h2>
        <div className="meta-grid">
          <div>
            <span className="meta-label">RECORDED</span>
            <span className="meta-value">
              {data.recorded_at ? data.recorded_at.replace("T", " ").slice(0, 19) : "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">WINDOW</span>
            <span className="meta-value">
              {fmtSec(data.start_sec)}–{fmtSec(data.end_sec)}
            </span>
          </div>
          <div>
            <span className="meta-label">CATEGORY</span>
            <span className="meta-value">{data.category}</span>
          </div>
          <div>
            <span className="meta-label">PRIORITY</span>
            <span className="meta-value">{data.priority.toFixed(2)}</span>
          </div>
          <div>
            <span className="meta-label">STATUS</span>
            <span className="meta-value">{data.status}</span>
          </div>
          <div>
            <span className="meta-label">LOCATION</span>
            <span className="meta-value">
              {data.location
                ? data.location.label ??
                  `${data.location.lat.toFixed(5)}, ${data.location.lon.toFixed(5)}`
                : "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">JOB</span>
            <span className="meta-value">
              <Link className="job-link" to={`/jobs/${data.job_id}`}>
                {data.job_id}
              </Link>
            </span>
          </div>
          <div>
            <span className="meta-label">REPORT</span>
            <span className="meta-value">
              <Link className="job-link" to={data.links.report}>
                ↗ view in report
              </Link>
            </span>
          </div>
        </div>
        <p className="desc-full">{data.description}</p>
      </section>

      {frames.length > 0 ? (
        <section className="panel">
          <h2>Captures</h2>
          <Filmstrip allowRaw frames={frames} />
        </section>
      ) : null}

      {data.plates.length > 0 ? (
        <section className="panel">
          <h2>Plates</h2>
          <div className="plate-block">
            {data.plates.map((plate) => (
              <div className="plate-card" key={plate.track_id}>
                {plate.crop_url ? (
                  <img
                    alt={`plate ${plate.norm_text ?? ""}`}
                    src={plate.crop_url}
                  />
                ) : null}
                <div className="chip-row">
                  <span className="chip chip--plate">{plate.norm_text ?? "—"}</span>
                  {plate.ken ? (
                    <span className="chip">
                      {plate.ken} / {plate.ken_en}
                    </span>
                  ) : null}
                </div>
                <p className="note">
                  conf {plate.confidence?.toFixed(2) ?? "—"}
                  {plate.event_id !== null ? (
                    <>
                      {" · "}
                      <Link
                        className="event-id"
                        to={`/jobs/${data.job_id}/events/${plate.event_id}`}
                      >
                        event #{plate.event_id}
                      </Link>
                    </>
                  ) : null}
                </p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {data.transcript_window.length > 0 ? (
        <section className="panel">
          <h2>Transcript</h2>
          <div className="terminal">
            {data.transcript_window.map((seg, i) => (
              <p key={i}>
                <span className="ts">[{fmtSec(seg.start_time)}]</span>{" "}
                {seg.text}
              </p>
            ))}
          </div>
        </section>
      ) : null}

      {data.track ? (
        <section className="panel">
          <h2>Vehicle Track</h2>
          <div className="meta-grid">
            <div>
              <span className="meta-label">TRACK</span>
              <span className="meta-value">
                <Link
                  className="job-link"
                  to={`/jobs/${data.job_id}/tracks/${data.track.track_id}`}
                >
                  #{data.track.track_id}
                </Link>
              </span>
            </div>
            <div>
              <span className="meta-label">ACTIVE</span>
              <span className="meta-value">
                {fmtSec(data.track.first_sec)}–{fmtSec(data.track.last_sec)}
              </span>
            </div>
            <div>
              <span className="meta-label">DIRECTION</span>
              <span className="meta-value">{data.track.direction ?? "—"}</span>
            </div>
            <div>
              <span className="meta-label">WEAVING</span>
              <span className="meta-value">
                {data.track.weaving_score?.toFixed(2) ?? "—"}
              </span>
            </div>
          </div>
          {data.track.strip.length > 0 ? (
            <div className="strip-row">
              {data.track.strip.map((url) => (
                <img
                  alt={`track ${data.track?.track_id} strip`}
                  key={url}
                  src={url}
                />
              ))}
            </div>
          ) : (
            <TerminalNote>no strip frames persisted for this track</TerminalNote>
          )}
        </section>
      ) : null}
    </>
  )
}
