import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { fetchPersonDetail } from "../api"
import type { PersonDetail } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"

export function PersonDetailRoute() {
  const { id } = useParams()
  const personId = Number(id)

  const { data, isPending, isError, error } = useQuery<PersonDetail, Error>({
    queryKey: ["person", personId],
    queryFn: () => fetchPersonDetail(personId),
  })

  return (
    <section className="panel">
      <h2>Person {String(personId).padStart(3, "0")}</h2>
      {isPending ? (
        <TerminalNote>querying /api/persons/{personId} ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">person error: {error.message}</TerminalNote>
      ) : !data || data.sightings.length === 0 ? (
        <TerminalNote>no sightings on record</TerminalNote>
      ) : (
        <>
          <p className="note">
            {data.total} sighting{data.total === 1 ? "" : "s"} across jobs ·
            newest first
          </p>
          <div className="face-gallery">
            {data.sightings.map((s) => (
              <figure
                className={"subject subject--" + s.tone + " face-card"}
                key={`${s.event_id}-${s.crop_url}`}
              >
                <span
                  className="designation"
                  style={{ color: toneColor(s.tone) }}
                >
                  {s.event_type} // {fmtSec(s.start_sec)}
                </span>
                <Link to={`/jobs/${s.job_id}/events/${s.event_id}`}>
                  <img alt="face sighting" loading="lazy" src={s.crop_url} />
                </Link>
                <figcaption>
                  {fmtDate(s.recorded_at)} ·{" "}
                  <Link className="job-link" to={`/jobs/${s.job_id}`}>
                    job {s.job_id}
                  </Link>{" "}
                  <Link
                    className="job-link"
                    to={`/jobs/${s.job_id}/faces`}
                  >
                    faces
                  </Link>
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
