import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchEventDetail, fetchJobEvents } from "../../api"
import type { EventDetail, JobEventsPage } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"

function CaptureItem({ jobId, eventId }: { jobId: number; eventId: number }) {
  const { data, isError } = useQuery<EventDetail, Error>({
    queryKey: ["event", eventId],
    queryFn: () => fetchEventDetail(eventId),
  })
  if (isError) {
    return (
      <figure className="capture-item">
        <figcaption>event #{eventId} unavailable</figcaption>
      </figure>
    )
  }
  if (!data || data.keyframes.length === 0) {
    return null
  }
  return (
    <figure className="capture-item">
      <Link
        className="capture-link"
        to={`/jobs/${jobId}/report?event=${eventId}`}
      >
        {data.keyframes.map((kf, index) => (
          <img
            alt={`event ${eventId} keyframe ${index + 1}`}
            className="capture-img"
            key={kf.url}
            loading="lazy"
            src={kf.url}
          />
        ))}
      </Link>
      <figcaption>
        {data.event_type} · {fmtSec(data.start_sec)}
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
          <CaptureItem jobId={jobId} eventId={evt.event_id} key={evt.event_id} />
        ))}
      </div>
      <TerminalNote>filmstrip scrubber + lightbox arrive in phase 7</TerminalNote>
    </section>
  )
}
