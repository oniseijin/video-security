import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useSearchParams } from "react-router-dom"
import { fetchCategories, fetchDays, fetchEventsPage } from "../api"
import type { CategoriesPayload, DaysResponse, EventsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

const LIMIT = 50

function DateStrip({
  selected,
  onSelect,
}: {
  selected: string | null
  onSelect: (date: string | null) => void
}) {
  const { data, isPending } = useQuery<DaysResponse, Error>({
    queryKey: ["days"],
    queryFn: fetchDays,
  })
  if (isPending || !data || data.days.length === 0) {
    return null
  }
  const recent = data.days.slice(0, 14)
  return (
    <div className="date-strip">
      <button
        type="button"
        className={`date-chip${selected === null ? " active" : ""}`}
        onClick={() => onSelect(null)}
      >
        ALL
      </button>
      {recent.map((d) => (
        <button
          key={d.date}
          type="button"
          className={`date-chip${selected === d.date ? " active" : ""}`}
          onClick={() => onSelect(d.date)}
          title={`${d.events} events · ${d.jobs} jobs`}
        >
          {d.date.slice(5)}
          <span className="date-chip-count">{d.events}</span>
        </button>
      ))}
    </div>
  )
}

export function Events() {
  const [params, setParams] = useSearchParams()
  const category = params.get("category") ?? ""
  const type = params.get("type") ?? ""
  const status = params.get("status") ?? ""
  const fromDate = params.get("from") ?? ""
  const toDate = params.get("to") ?? ""
  const offset = Number(params.get("offset") ?? 0)
  const query = new URLSearchParams()
  if (category) {
    query.set("category", category)
  }
  if (type) {
    query.set("type", type)
  }
  if (status) {
    query.set("status", status)
  }
  if (fromDate) {
    query.set("from", fromDate)
  }
  if (toDate) {
    query.set("to", toDate)
  }
  query.set("limit", String(LIMIT))
  query.set("offset", String(offset))

  const { data, isPending, isError, error } = useQuery<EventsPage, Error>({
    queryKey: ["events", query.toString()],
    queryFn: () => fetchEventsPage(query),
  })
  const cats = useQuery<CategoriesPayload, Error>({
    queryKey: ["categories"],
    queryFn: fetchCategories,
  })

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) {
      next.set(key, value)
    } else {
      next.delete(key)
    }
    next.delete("offset")
    setParams(next)
  }
  const setDate = (date: string | null) => {
    const next = new URLSearchParams(params)
    if (date) {
      next.set("from", date)
      next.set("to", date)
    } else {
      next.delete("from")
      next.delete("to")
    }
    next.delete("offset")
    setParams(next)
  }
  const page = (delta: number) => {
    const next = new URLSearchParams(params)
    next.set("offset", String(Math.max(0, offset + delta)))
    setParams(next)
  }

  return (
    <>
      <section className="panel">
        <h2>Events</h2>
        {cats.data ? (
          <p className="note">
            {Object.entries(cats.data.categories)
              .map(([k, v]) => `${k} ${v}`)
              .join(" · ")}
          </p>
        ) : null}
        <DateStrip selected={fromDate || null} onSelect={setDate} />
        <div className="filter-bar">
          <label>
            FROM
            <input
              onChange={(e) => setFilter("from", e.target.value)}
              type="date"
              value={fromDate}
            />
          </label>
          <label>
            TO
            <input
              onChange={(e) => setFilter("to", e.target.value)}
              type="date"
              value={toDate}
            />
          </label>
          <label>
            CATEGORY
            <select
              onChange={(e) => setFilter("category", e.target.value)}
              value={category}
            >
              <option value="">all</option>
              <option value="driving">driving</option>
              <option value="parking">parking</option>
              <option value="stationary">stationary</option>
              <option value="unknown">unknown</option>
            </select>
          </label>
          <label>
            TYPE
            <select onChange={(e) => setFilter("type", e.target.value)} value={type}>
              <option value="">all</option>
              {cats.data
                ? Object.keys(cats.data.types)
                    .sort()
                    .map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))
                : null}
            </select>
          </label>
          <label>
            STATUS
            <select
              onChange={(e) => setFilter("status", e.target.value)}
              value={status}
            >
              <option value="">all</option>
              <option value="pending">pending</option>
              <option value="triaged">triaged</option>
              <option value="detailed">detailed</option>
              <option value="suppressed">suppressed</option>
            </select>
          </label>
        </div>
        {isPending ? (
          <TerminalNote>querying /api/events ...</TerminalNote>
        ) : isError ? (
          <TerminalNote tone="threat">events error: {error.message}</TerminalNote>
        ) : !data || data.items.length === 0 ? (
          <TerminalNote>no events match</TerminalNote>
        ) : (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Recorded</th>
                  <th>Type</th>
                  <th>Category</th>
                  <th>ID</th>
                  <th>Job</th>
                  <th className="num">Priority</th>
                  <th>Status</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((evt) => (
                  <tr key={evt.event_id}>
                    <td>{fmtDate(evt.recorded_at)}</td>
                    <td>{evt.event_type}</td>
                    <td>{evt.category}</td>
                    <td>
                      <Link
                        className="event-id"
                        style={{ color: toneColor(evt.tone as Tone) }}
                        to={`/jobs/${evt.job_id}/events/${evt.event_id}`}
                      >
                        #{evt.event_id}
                      </Link>
                    </td>
                    <td>
                      <Link className="job-link" to={`/jobs/${evt.job_id}`}>
                        {evt.job_id}
                      </Link>
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
    </>
  )
}
