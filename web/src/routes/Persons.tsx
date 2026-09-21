import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { fetchPersons } from "../api"
import type { PersonsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate } from "../format"

const LIMIT = 36

export function Persons() {
  const [params, setParams] = useSearchParams()
  const offset = Number(params.get("offset") ?? 0)
  const query = new URLSearchParams()
  query.set("limit", String(LIMIT))
  query.set("offset", String(offset))

  const { data, isPending, isError, error } = useQuery<PersonsPage, Error>({
    queryKey: ["persons", query.toString()],
    queryFn: () => fetchPersons(query),
  })

  const page = (delta: number) => {
    const next = new URLSearchParams(params)
    next.set("offset", String(Math.max(0, offset + delta)))
    setParams(next)
  }

  return (
    <section className="panel">
      <h2>Persons</h2>
      <TerminalNote>
        face clusters across all jobs — local detection only
      </TerminalNote>
      {isPending ? (
        <TerminalNote>querying /api/persons ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">persons error: {error.message}</TerminalNote>
      ) : !data || data.items.length === 0 ? (
        <TerminalNote>
          no persons indexed yet — run `vs index-faces` after harvest
        </TerminalNote>
      ) : (
        <>
          <p className="note">
            {data.total} person{data.total === 1 ? "" : "s"} · most sighted
            first
          </p>
          <div className="face-gallery">
            {data.items.map((p) => (
              <figure className="subject face-card" key={p.person_id}>
                <span className="designation">
                  PERSON {String(p.person_id).padStart(3, "0")} //{" "}
                  {p.sightings} sighting{p.sightings === 1 ? "" : "s"}
                </span>
                <Link to={`/persons/${p.person_id}`}>
                  {p.representative_crop_url ? (
                    <img
                      alt={`person ${p.person_id}`}
                      loading="lazy"
                      src={p.representative_crop_url}
                    />
                  ) : (
                    <span className="note">no crop</span>
                  )}
                </Link>
                <figcaption>
                  {fmtDate(p.first_seen)} – {fmtDate(p.last_seen)}
                </figcaption>
              </figure>
            ))}
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
