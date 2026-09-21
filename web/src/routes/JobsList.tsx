import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { fetchJobs } from "../api"
import type { JobsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate } from "../format"

const LIMIT = 50

const STATUSES = [
  "pending",
  "extracting",
  "filtering",
  "harvested",
  "triage",
  "triaged",
  "detail",
  "done",
  "failed",
]
const MODES = ["EVENT", "PARKING", "MANUAL", "NORMAL"]
const CHANNELS = ["front", "rear"]
const SORTS = ["id", "recorded", "imported"]
const ORDERS = ["desc", "asc"]

export function JobsList() {
  const [searchParams, setSearchParams] = useSearchParams()
  const status = searchParams.get("status") ?? ""
  const mode = searchParams.get("mode") ?? ""
  const channel = searchParams.get("channel") ?? ""
  const q = searchParams.get("q") ?? ""
  const sort = searchParams.get("sort") ?? "id"
  const order = searchParams.get("order") ?? "desc"
  const offset = Math.max(
    0,
    Number.parseInt(searchParams.get("offset") ?? "0", 10) || 0
  )

  const [qInput, setQInput] = useState(q)
  useEffect(() => {
    setQInput(q)
  }, [q])

  const params = new URLSearchParams()
  if (status) {
    params.set("status", status)
  }
  if (mode) {
    params.set("mode", mode)
  }
  if (channel) {
    params.set("channel", channel)
  }
  if (q) {
    params.set("q", q)
  }
  params.set("sort", sort)
  params.set("order", order)
  params.set("limit", String(LIMIT))
  params.set("offset", String(offset))

  const { data, isPending, isError, error } = useQuery<JobsPage, Error>({
    queryKey: ["jobs", params.toString()],
    queryFn: () => fetchJobs(params),
  })

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
      <h2>Jobs</h2>
      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          setFilter("q", qInput.trim())
        }}
      >
        <label>
          status
          <select
            value={status}
            onChange={(event) => setFilter("status", event.target.value)}
          >
            <option value="">all</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          mode
          <select
            value={mode}
            onChange={(event) => setFilter("mode", event.target.value)}
          >
            <option value="">all</option>
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          channel
          <select
            value={channel}
            onChange={(event) => setFilter("channel", event.target.value)}
          >
            <option value="">all</option>
            {CHANNELS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          q
          <input
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </label>
        <label>
          sort
          <select
            value={sort}
            onChange={(event) => setFilter("sort", event.target.value)}
          >
            {SORTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          order
          <select
            value={order}
            onChange={(event) => setFilter("order", event.target.value)}
          >
            {ORDERS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
      </form>
      {isPending ? (
        <TerminalNote>querying /api/jobs ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">jobs error: {error.message}</TerminalNote>
      ) : data ? (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Status</th>
                <th>Mode</th>
                <th>Channel</th>
                <th>Recorded</th>
                <th>Imported</th>
                <th>Flags</th>
                <th className="num">Events</th>
                <th className="num">Plates</th>
                <th className="num">Faces</th>
                <th>Pair</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((job) => (
                <tr
                  key={job.id}
                  className={job.status === "failed" ? "row-threat" : undefined}
                >
                  <td>
                    <Link className="job-link" to={`/jobs/${job.id}`}>
                      {job.id}
                    </Link>
                  </td>
                  <td>{job.status}</td>
                  <td>{job.mode ?? "—"}</td>
                  <td>{job.channel ?? "—"}</td>
                  <td>{fmtDate(job.recorded_at)}</td>
                  <td>{fmtDate(job.imported_at)}</td>
                  <td>
                    <span className="chip-row">
                      {job.archive ? <span className="chip">ARCH</span> : null}
                      {job.has_gps ? <span className="chip">GPS</span> : null}
                      {job.has_transcript ? <span className="chip">TR</span> : null}
                    </span>
                  </td>
                  <td className="num">{job.counts.events}</td>
                  <td className="num">{job.counts.plates}</td>
                  <td className="num">{job.counts.faces}</td>
                  <td>
                    {job.pair_job_id !== null ? (
                      <Link className="job-link" to={`/jobs/${job.pair_job_id}`}>
                        {job.pair_job_id}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
                ? "0 jobs"
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
      ) : null}
    </section>
  )
}
