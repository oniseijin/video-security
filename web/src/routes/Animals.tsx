import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { fetchAnimals } from "../api"
import type { AnimalsPage } from "../api"
import { BoxOverlay } from "../components/BoxOverlay"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

const LIMIT = 24

export function Animals() {
  const [params, setParams] = useSearchParams()
  const offset = Number(params.get("offset") ?? 0)
  const query = new URLSearchParams()
  query.set("limit", String(LIMIT))
  query.set("offset", String(offset))
  const jobId = params.get("job_id")
  if (jobId) {
    query.set("job_id", jobId)
  }

  const { data, isPending, isError, error } = useQuery<AnimalsPage, Error>({
    queryKey: ["animals", query.toString()],
    queryFn: () => fetchAnimals(query),
  })

  const page = (delta: number) => {
    const next = new URLSearchParams(params)
    next.set("offset", String(Math.max(0, offset + delta)))
    setParams(next)
  }

  return (
    <section className="panel">
      <h2>Animals</h2>
      <TerminalNote>
        dog/cat detections on event keyframes — green overlay marks the animal
      </TerminalNote>
      {isPending ? (
        <TerminalNote>querying /api/animals ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">animals error: {error.message}</TerminalNote>
      ) : !data || data.items.length === 0 ? (
        <TerminalNote>no animals detected yet</TerminalNote>
      ) : (
        <>
          <p className="note">
            {data.total} event{data.total === 1 ? "" : "s"} with animals
            {jobId ? ` · job ${jobId}` : ""} · newest first
          </p>
          <div className="face-gallery">
            {data.items.flatMap((item) =>
              item.keyframes.map((kf) => (
                <figure
                  className={
                    "subject subject--" + item.tone + " face-card animal-card"
                  }
                  key={`${item.event_id}:${kf.url}`}
                >
                  <span
                    className="designation"
                    style={{ color: toneColor(item.tone as Tone) }}
                  >
                    {item.event_type} // {fmtSec(item.start_sec)}
                  </span>
                  <Link to={`/jobs/${item.job_id}/events/${item.event_id}`}>
                    <span className="kf-wrap">
                      <img
                        alt={`animal job ${item.job_id} event ${item.event_id}`}
                        loading="lazy"
                        src={kf.url}
                      />
                      <BoxOverlay boxes={kf.boxes} />
                    </span>
                  </Link>
                  <figcaption>
                    {kf.boxes.length}{" "}
                    {kf.boxes.length === 1 ? "animal" : "animals"} ·{" "}
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
