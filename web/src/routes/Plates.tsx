import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { fetchPlatesGallery } from "../api"
import type { PlatesGalleryPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate } from "../format"

const LIMIT = 50

export function platePath(normText: string): string {
  return `/plates/${encodeURIComponent(normText)}`
}

export function Plates() {
  const [searchParams, setSearchParams] = useSearchParams()
  const q = searchParams.get("q") ?? ""
  const offset = Math.max(
    0,
    Number.parseInt(searchParams.get("offset") ?? "0", 10) || 0
  )

  const [qInput, setQInput] = useState(q)
  useEffect(() => {
    setQInput(q)
  }, [q])

  const params = new URLSearchParams()
  if (q) {
    params.set("q", q)
  }
  params.set("limit", String(LIMIT))
  params.set("offset", String(offset))

  const { data, isPending, isError, error } = useQuery<PlatesGalleryPage, Error>(
    {
      queryKey: ["plates-gallery", params.toString()],
      queryFn: () => fetchPlatesGallery(params),
    }
  )

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) {
      next.set(key, value)
    } else {
      next.delete(key)
    }
    if (key !== "offset") {
      next.delete("offset")
    }
    setSearchParams(next)
  }

  return (
    <section className="panel">
      <h2>Plates</h2>
      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          setFilter("q", qInput.trim())
        }}
      >
        <label>
          q
          <input
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
            placeholder="plate substring"
          />
        </label>
      </form>
      {isPending ? (
        <TerminalNote>querying /api/plates ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">plates error: {error.message}</TerminalNote>
      ) : !data || data.items.length === 0 ? (
        <TerminalNote>no plates match</TerminalNote>
      ) : (
        <>
          <div className="plate-grid">
            {data.items.map((plate) => (
              <div className="plate-card" key={plate.norm_text}>
                <Link
                  className="plate-card-link"
                  to={platePath(plate.norm_text)}
                >
                  {plate.best_crop_url ? (
                    <img
                      alt={`plate ${plate.norm_text} crop`}
                      loading="lazy"
                      src={plate.best_crop_url}
                    />
                  ) : (
                    <span className="plate-card-blank">no crop</span>
                  )}
                  <span className="chip-row">
                    <span className="chip chip--plate">{plate.norm_text}</span>
                    {plate.ken ? (
                      <span className="chip" title={plate.ken ?? undefined}>
                        {plate.ken}
                      </span>
                    ) : null}
                  </span>
                </Link>
                <p className="note">
                  sightings {plate.sightings} · conf{" "}
                  {plate.best_confidence !== null
                    ? plate.best_confidence.toFixed(2)
                    : "—"}
                </p>
                <p className="note">
                  {fmtDate(plate.first_seen)} → {fmtDate(plate.last_seen)}
                </p>
                <div className="chip-row">
                  {plate.jobs.map((jobId) => (
                    <Link
                      className="count-chip"
                      key={`${plate.norm_text}-${jobId}`}
                      to={`/jobs/${jobId}`}
                    >
                      job {jobId}
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="pager">
            <button
              type="button"
              className="app-btn"
              disabled={offset === 0}
              onClick={() =>
                setFilter("offset", String(Math.max(0, offset - LIMIT)))
              }
            >
              Prev
            </button>
            <span className="pager-info">
              {data.total === 0
                ? "0 plates"
                : `${offset + 1}–${Math.min(offset + LIMIT, data.total)} of ${
                    data.total
                  }`}
            </span>
            <button
              type="button"
              className="app-btn"
              disabled={offset + LIMIT >= data.total}
              onClick={() => setFilter("offset", String(offset + LIMIT))}
            >
              Next
            </button>
          </div>
        </>
      )}
    </section>
  )
}
