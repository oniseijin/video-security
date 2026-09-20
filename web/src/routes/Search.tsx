import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { fetchSearch } from "../api"
import type { SearchResults } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtSec } from "../format"
import { platePath } from "./Plates"

export function Search() {
  const [searchParams, setSearchParams] = useSearchParams()
  const q = searchParams.get("q") ?? ""
  const [qInput, setQInput] = useState(q)
  useEffect(() => {
    setQInput(q)
  }, [q])
  const trimmed = q.trim()

  const { data, isPending, isError, error } = useQuery<SearchResults, Error>({
    queryKey: ["search", trimmed],
    queryFn: () => fetchSearch(trimmed),
    enabled: trimmed !== "",
  })

  const hasHits =
    data !== undefined &&
    (data.plates.length > 0 ||
      data.text.length > 0 ||
      data.transcripts.length > 0 ||
      data.events.length > 0)

  return (
    <>
      <section className="panel">
        <h2>Search</h2>
        <form
          className="filter-bar"
          onSubmit={(event) => {
            event.preventDefault()
            const next = new URLSearchParams()
            const value = qInput.trim()
            if (value) {
              next.set("q", value)
            }
            setSearchParams(next)
          }}
        >
          <label>
            q
            <input
              value={qInput}
              onChange={(event) => setQInput(event.target.value)}
              placeholder="query"
            />
          </label>
        </form>
        {trimmed === "" ? (
          <TerminalNote>
            enter a search query — plates, scene text, transcripts, event types
          </TerminalNote>
        ) : isPending ? (
          <TerminalNote>querying /api/search?q={trimmed} ...</TerminalNote>
        ) : isError ? (
          <TerminalNote tone="threat">
            search error: {error.message}
          </TerminalNote>
        ) : data && !hasHits ? (
          <TerminalNote>no matches for {trimmed}</TerminalNote>
        ) : data ? (
          <p className="note">
            plates {data.plates.length} · scene text {data.text.length} ·
            transcripts {data.transcripts.length} · events{" "}
            {data.events.length}
          </p>
        ) : null}
      </section>
      {data && hasHits ? (
        <>
          <section className="panel">
            <h2>Plates</h2>
            {data.plates.length === 0 ? (
              <TerminalNote>no plate matches</TerminalNote>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Crop</th>
                    <th>Plate</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {data.plates.map((hit) => (
                    <tr key={`${hit.job_id}-${hit.track_id}`}>
                      <td>
                        {hit.crop_url ? (
                          <img
                            alt="plate crop"
                            className="thumb"
                            loading="lazy"
                            src={hit.crop_url}
                          />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        {hit.norm_text ? (
                          <Link
                            className="chip chip--plate"
                            to={platePath(hit.norm_text)}
                          >
                            {hit.norm_text}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        <Link className="job-link" to={`/jobs/${hit.job_id}`}>
                          {hit.job_id}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="panel">
            <h2>Scene Text</h2>
            {data.text.length === 0 ? (
              <TerminalNote>no scene text matches</TerminalNote>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th className="num">Frame</th>
                    <th>Text</th>
                  </tr>
                </thead>
                <tbody>
                  {data.text.map((hit) => (
                    <tr key={`${hit.job_id}-${hit.clip_id}-${hit.frame_number}`}>
                      <td>
                        <Link className="job-link" to={`/jobs/${hit.job_id}`}>
                          {hit.job_id}
                        </Link>
                      </td>
                      <td className="num">{hit.frame_number}</td>
                      <td className="wrap-cell">{hit.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="panel">
            <h2>Transcripts</h2>
            {data.transcripts.length === 0 ? (
              <TerminalNote>no transcript matches</TerminalNote>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Time</th>
                    <th>Text</th>
                  </tr>
                </thead>
                <tbody>
                  {data.transcripts.map((hit) => (
                    <tr key={`${hit.job_id}-${hit.clip_id}-${hit.start_time}`}>
                      <td>
                        <Link className="job-link" to={`/jobs/${hit.job_id}`}>
                          {hit.job_id}
                        </Link>
                      </td>
                      <td>{fmtSec(hit.start_time)}</td>
                      <td className="wrap-cell">{hit.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="panel">
            <h2>Events</h2>
            {data.events.length === 0 ? (
              <TerminalNote>no event matches</TerminalNote>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Event</th>
                    <th>Type</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((hit) => (
                    <tr key={`${hit.job_id}-${hit.event_id}`}>
                      <td>
                        <Link className="job-link" to={`/jobs/${hit.job_id}`}>
                          {hit.job_id}
                        </Link>
                      </td>
                      <td>
                        <Link
                          className="event-id"
                          to={`/jobs/${hit.job_id}/events/${hit.event_id}`}
                        >
                          #{hit.event_id}
                        </Link>
                      </td>
                      <td>{hit.event_type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      ) : null}
    </>
  )
}
