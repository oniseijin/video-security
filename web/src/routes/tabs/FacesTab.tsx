import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchJobFaces } from "../../api"
import type { JobFaceGroup } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"
import { toneColor } from "../../theme"

function Group({ jobId, group }: { jobId: number; group: JobFaceGroup }) {
  return (
    <section className="panel">
      {group.person_id != null ? (
        <h3>
          <Link to={`/persons/${group.person_id}`}>
            PERSON {String(group.person_id).padStart(3, "0")}
          </Link>{" "}
          <span className="note">
            · {group.count} capture{group.count === 1 ? "" : "s"} in this job
          </span>
        </h3>
      ) : (
        <h3>
          UNCLUSTERED{" "}
          <span className="note">
            · {group.count} capture{group.count === 1 ? "" : "s"} — run{" "}
            `vs index-faces` to cluster
          </span>
        </h3>
      )}
      <div className="face-gallery">
        {group.crops.map((c) => (
          <figure
            className={"subject subject--" + c.tone + " face-card"}
            key={c.crop_url}
          >
            <span
              className="designation"
              style={{ color: toneColor(c.tone) }}
            >
              {c.event_type} // {fmtSec(c.start_sec)}
            </span>
            <Link to={`/jobs/${jobId}/events/${c.event_id}`}>
              <img
                alt={`face event ${c.event_id}`}
                loading="lazy"
                src={c.crop_url}
              />
            </Link>
            <figcaption>
              <Link
                className="event-id"
                style={{ color: toneColor(c.tone) }}
                to={`/jobs/${jobId}/events/${c.event_id}`}
              >
                #{c.event_id}
              </Link>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}

export function FacesTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["job-faces", jobId],
    queryFn: () => fetchJobFaces(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/faces ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">faces error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.total === 0) {
    return <TerminalNote>no face captures for job {jobId}</TerminalNote>
  }

  return (
    <>
      <section className="panel">
        <h2>Faces</h2>
        <TerminalNote>
          {data.total} face capture{data.total === 1 ? "" : "s"} ·{" "}
          {data.groups.filter((g) => g.person_id != null).length} person
          {data.groups.filter((g) => g.person_id != null).length === 1
            ? ""
            : "s"}{" "}
          in this job
        </TerminalNote>
      </section>
      {data.groups.map((g) => (
        <Group jobId={jobId} group={g} key={g.person_id ?? "unclustered"} />
      ))}
    </>
  )
}
