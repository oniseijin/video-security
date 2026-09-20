import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchEventDetail, fetchJobEvents } from "../../api"
import type { EventDetail, JobEventsPage } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"
import { toneColor } from "../../theme"
import type { Tone } from "../../theme"

function CaptureItem({ jobId, eventId, tone }: { jobId: number; eventId: number; tone: Tone }) {
  const { data, isError } = useQuery<EventDetail, Error>({
    queryKey: ["event", eventId],
    queryFn: () => fetchEventDetail(eventId),
  })
  if (isError) {
    return (
      <figure className="subject capture-item">
        <figcaption>event #{eventId} unavailable</figcaption>
      </figure>
    )
  }
  if (!data || data.keyframes.length === 0) {
    return null
  }
  const kf = data.keyframes[0]
  return (
    <figure className={"subject subject--" + data.tone + " capture-item"}>
      <span className="designation">
        {data.event_type} // {fmtSec(data.start_sec)}
      </span>
      <div className="kf-wrap">
        <Link to={`/jobs/${jobId}/events/${eventId}`}>
          <img
            alt={`event ${eventId} keyframe`}
            className="capture-img"
            loading="lazy"
            src={kf.url}
          />
        </Link>
        {kf.faces.length > 0 ? (
          kf.faces
            .filter((box) => box.length === 4)
            .map((box, i) => (
              <span
                className="face-box"
                key={i}
                style={{
                  left: `${box[0] * 100}%`,
                  top: `${box[1] * 100}%`,
                  width: `${box[2] * 100}%`,
                  height: `${box[3] * 100}%`,
                }}
              />
            ))
        ) : null}
      </div>
      <figcaption>
        <Link
          className="event-id"
          style={{ color: toneColor(tone) }}
          to={`/jobs/${jobId}/events/${eventId}`}
        >
          #{eventId}
        </Link>{" "}
        {data.face_count > 0 ? `${data.face_count} face(s) · ` : ""}
        {data.keyframes.length} frame(s)
      </figcaption>
    </figure>
  )
}

export function CapturesTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobEventsPage, Error>({
    queryKey: ["job-events", jobId],
    queryFn: () => fetchJobEvents(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/events ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">captures error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return <TerminalNote>no events with keyframes for job {jobId}</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Captures</h2>
      <div className="capture-grid">
        {data.items.map((evt) => (
          <CaptureItem
            jobId={jobId}
            eventId={evt.event_id}
            key={evt.event_id}
            tone={evt.tone}
          />
        ))}
      </div>
    </section>
  )
}
