import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchJobEvents } from "../../api"
import type { JobEventsPage } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"
import { toneColor } from "../../theme"
import type { Tone } from "../../theme"

function EventLink({ jobId, eventId, tone }: { jobId: number; eventId: number; tone: Tone }) {
  return (
    <Link
      className="event-id"
      style={{ color: toneColor(tone) }}
      to={`/jobs/${jobId}/report?event=${eventId}`}
    >
      #{eventId}
    </Link>
  )
}

export function EventsTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobEventsPage, Error>({
    queryKey: ["job-events", jobId],
    queryFn: () => fetchJobEvents(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/events ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">events error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return <TerminalNote>no events for job {jobId}</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Events</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Start</th>
            <th>End</th>
            <th>Type</th>
            <th>Category</th>
            <th>ID</th>
            <th className="num">Priority</th>
            <th>Status</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((evt) => (
            <tr key={evt.event_id}>
              <td>{fmtSec(evt.start_sec)}</td>
              <td>{fmtSec(evt.end_sec)}</td>
              <td>{evt.event_type}</td>
              <td>{evt.category}</td>
              <td>
                <EventLink
                  jobId={jobId}
                  eventId={evt.event_id}
                  tone={evt.tone}
                />
              </td>
              <td className="num">{evt.priority.toFixed(2)}</td>
              <td>{evt.status}</td>
              <td className="desc-cell" title={evt.description}>
                {evt.description}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TerminalNote>filmstrip + lightbox arrive in phase 7</TerminalNote>
    </section>
  )
}
