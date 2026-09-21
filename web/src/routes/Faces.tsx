import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { fetchFaces } from "../api"
import type { FacesPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

const LIMIT = 48

export function Faces() {
  const [params, setParams] = useSearchParams()
  const offset = Number(params.get("offset") ?? 0)
  const query = new URLSearchParams()
  query.set("limit", String(LIMIT))
  query.set("offset", String(offset))
  const jobId = params.get("job_id")
  if (jobId) {
    query.set("job_id", jobId)
  }

  const { data, isPending, isError, error } = useQuery<FacesPage, Error>({
    queryKey: ["faces", query.toString()],
    queryFn: () => fetchFaces(query),
  })

  const page = (delta: number) => {
    const next = new URLSearchParams(params)
    next.set("offset", String(Math.max(0, offset + delta)))
    setParams(next)
  }

  return (
    <section className="panel">
      <h2>Faces</h2>
      <TerminalNote>
        local detection only — no recognition or embeddings
      </TerminalNote>
      {isPending ? (
        <TerminalNote>querying /api/faces ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">faces error: {error.message}</TerminalNote>
      ) : !data || data.items.length === 0 ? (
        <TerminalNote>no faces detected yet</TerminalNote>
      ) : (
        <>
          <p className="note">
            {data.total} face capture{data.total === 1 ? "" : "s"}
            {jobId ? ` · job ${jobId}` : ""} · newest first
          </p>
          <div className="face-gallery">
            {data.items.flatMap((item) =>
              item.crops.map((url, idx) => (
                <figure
                  className={"subject subject--" + item.tone + " face-card"}
                  key={url}
                >
                  <span
                    className="designation"
                    style={{ color: toneColor(item.tone as Tone) }}
                  >
                    {item.event_type} // {fmtSec(item.start_sec)}
                  </span>
                  <Link to={`/jobs/${item.job_id}/events/${item.event_id}`}>
                    <img
                      alt={`face job ${item.job_id} event ${item.event_id}`}
                      loading="lazy"
                      src={url}
                    />
                  </Link>
                  <figcaption>
                    {item.person_ids[idx] != null ? (
                      <>
                        <Link to={`/persons/${item.person_ids[idx]}`}>
                          PERSON {String(item.person_ids[idx]).padStart(3, "0")}
                        </Link>{" "}
                        ·{" "}
                      </>
                    ) : null}
                    {fmtDate(item.recorded_at)} ·{" "}
                    <Link className="job-link" to={`/jobs/${item.job_id}`}>
                      job {item.job_id}
                    </Link>
                  </figcaption>
                </figure>
              ))
            )}
          </div>
          <div className="pager">
            <button
              className="app-btn"
              disabled={offset === 0}
              onClick={() => page(-LIMIT)}
              type="button"
            >
              ◀ PREV
            </button>
            <span className="pager-info">
              {data.total === 0
                ? "0"
                : `${offset + 1}–${Math.min(offset + LIMIT, data.total)}`}{" "}
              OF {data.total}
            </span>
            <button
              className="app-btn"
              disabled={offset + LIMIT >= data.total}
              onClick={() => page(LIMIT)}
              type="button"
            >
              NEXT ▶
            </button>
          </div>
        </>
      )}
    </section>
  )
}
