import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState } from "react"
import { fetchDays, fetchEventsPage } from "../api"
import type { DayBucket, EventsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

const MONTHS = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
]

function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${d.getFullYear()}-${m}-${day}`
}

function CalendarHeat({
  days,
  selected,
  onSelect,
}: {
  days: DayBucket[]
  selected: string | null
  onSelect: (day: string) => void
}) {
  const [cursor, setCursor] = useState(() => {
    const first = days[0]?.date ?? dayKey(new Date())
    return { year: Number(first.slice(0, 4)), month: Number(first.slice(5, 7)) - 1 }
  })
  const byDate = new Map(days.map((d) => [d.date, d]))
  const maxEvents = Math.max(1, ...days.map((d) => d.events))
  const firstOfMonth = new Date(cursor.year, cursor.month, 1)
  const daysInMonth = new Date(cursor.year, cursor.month + 1, 0).getDate()
  const lead = firstOfMonth.getDay()
  const cells: (DayBucket | null)[] = Array.from({ length: lead }, () => null)
  for (let i = 1; i <= daysInMonth; i++) {
    const key = dayKey(new Date(cursor.year, cursor.month, i))
    cells.push(byDate.get(key) ?? null)
  }
  const shift = (delta: number) => {
    const next = new Date(cursor.year, cursor.month + delta, 1)
    setCursor({ year: next.getFullYear(), month: next.getMonth() })
  }
  const hasData = (y: number, m: number) =>
    days.some((d) => d.date.startsWith(`${y}-${String(m + 1).padStart(2, "0")}`))

  return (
    <div className="cal-heat">
      <div className="cal-heat-nav">
        <button className="app-btn" onClick={() => shift(-1)} type="button">
          ◀ PREV
        </button>
        <span className="cal-heat-title">
          {MONTHS[cursor.month]} {cursor.year}
        </span>
        <button className="app-btn" onClick={() => shift(1)} type="button">
          NEXT ▶
        </button>
      </div>
      <div className="cal-heat-grid">
        {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
          <span className="cal-heat-dow" key={i}>
            {d}
          </span>
        ))}
        {cells.map((cell, i) => {
          if (cell === null) {
            return <span className="cal-heat-cell empty" key={`e${i}`} />
          }
          const intensity = cell.events / maxEvents
          const isSelected = selected === cell.date
          return (
            <button
              className={
                "cal-heat-cell" + (isSelected ? " selected" : "") +
                (cell.events > 0 || cell.jobs > 0 ? "" : " muted")
              }
              key={cell.date}
              onClick={() => onSelect(cell.date)}
              style={
                cell.events > 0
                  ? {
                      background: `color-mix(in srgb, var(--vs-threat) ${
                        20 + 70 * intensity
                      }%, transparent)`,
                    }
                  : undefined
              }
              title={`${cell.date}: ${cell.jobs} jobs, ${cell.events} events`}
              type="button"
            >
              {Number(cell.date.slice(8, 10))}
            </button>
          )
        })}
      </div>
      {!hasData(cursor.year, cursor.month) ? (
        <p className="note">no recordings this month</p>
      ) : null}
    </div>
  )
}

function DayTimeline({ day }: { day: string }) {
  const params = new URLSearchParams({
    from: day,
    to: day,
    limit: "500",
  })
  const { data, isPending, isError, error } = useQuery<EventsPage, Error>({
    queryKey: ["timeline", day],
    queryFn: () => fetchEventsPage(params),
  })
  if (isPending) {
    return <TerminalNote>querying events for {day} ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">timeline error: {error.message}</TerminalNote>
  }
  const items = data?.items ?? []
  if (items.length === 0) {
    return <TerminalNote>no events recorded on {day}</TerminalNote>
  }
  const withTime = items.filter((e) => e.recorded_at != null)
  return (
    <>
      <p className="note">
        {items.length} events on {fmtDate(day)} · position = time of day ·
        color = event tone
      </p>
      <div className="day-timeline">
        <div className="day-timeline-axis">
          {Array.from({ length: 9 }, (_, i) => (
            <span key={i}>{`${String(i * 3).padStart(2, "0")}:00`}</span>
          ))}
        </div>
        <div className="day-timeline-track">
          {withTime.map((e) => {
            const t = new Date(`${e.recorded_at}Z`)
            const frac =
              (t.getUTCHours() * 3600 +
                t.getUTCMinutes() * 60 +
                t.getUTCSeconds() +
                e.start_sec) /
              86400
            return (
              <Link
                className="day-timeline-marker"
                key={e.event_id}
                style={{
                  left: `${Math.min(99.5, Math.max(0, frac * 100))}%`,
                  background: toneColor(e.tone as Tone),
                }}
                title={`${e.event_type} · ${fmtDate(e.recorded_at)} · job ${e.job_id}`}
                to={`/jobs/${e.job_id}/events/${e.event_id}`}
              />
            )
          })}
        </div>
      </div>
      <div className="capture-grid">
        {withTime.slice(0, 24).map((e) => (
          <figure
            className={"subject subject--" + e.tone + " face-card"}
            key={`row-${e.event_id}`}
          >
            <span
              className="designation"
              style={{ color: toneColor(e.tone as Tone) }}
            >
              {e.event_type} //{" "}
              {e.recorded_at ? e.recorded_at.slice(11, 19) : ""}
            </span>
            <Link to={`/jobs/${e.job_id}/events/${e.event_id}`}>
              <span className="timeline-row-link">event #{e.event_id}</span>
            </Link>
            <figcaption>
              {fmtDate(e.recorded_at)} ·{" "}
              <Link className="job-link" to={`/jobs/${e.job_id}`}>
                job {e.job_id}
              </Link>{" "}
              · {e.status}
            </figcaption>
          </figure>
        ))}
      </div>
    </>
  )
}

export function Timeline() {
  const [selected, setSelected] = useState<string | null>(null)
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["days"],
    queryFn: fetchDays,
  })
  if (isPending) {
    return (
      <section className="panel">
        <h2>Timeline</h2>
        <TerminalNote>querying /api/days ...</TerminalNote>
      </section>
    )
  }
  if (isError) {
    return (
      <section className="panel">
        <h2>Timeline</h2>
        <TerminalNote tone="threat">days error: {error.message}</TerminalNote>
      </section>
    )
  }
  const days = data?.days ?? []
  return (
    <section className="panel">
      <h2>Timeline</h2>
      <TerminalNote>
        calendar heat = recordings per day · click a day for its cross-job
        timeline
      </TerminalNote>
      {days.length === 0 ? (
        <TerminalNote>no recordings indexed yet</TerminalNote>
      ) : (
        <>
          <CalendarHeat
            days={days}
            onSelect={setSelected}
            selected={selected}
          />
          {selected != null ? (
            <DayTimeline day={selected} />
          ) : (
            <p className="note">select a day to load its timeline</p>
          )}
        </>
      )}
      {selected != null ? (
        <button
          className="app-btn"
          onClick={() => setSelected(null)}
          type="button"
        >
          CLEAR DAY
        </button>
      ) : null}
      <p className="note">
        <Link to={`/events?from=${selected ?? ""}`}>
          open this day in Events browser →
        </Link>
      </p>
    </section>
  )
}
