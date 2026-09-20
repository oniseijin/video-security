import { useQuery } from "@tanstack/react-query"
import { fetchStats } from "../api"
import type { Stats } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtBytes, fmtDate } from "../format"
import { toneColor } from "../theme"

function progressPct(current: number, total: number | null): number {
  if (total === null || total <= 0) {
    return 0
  }
  return Math.min(100, Math.round((current / total) * 100))
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}

export function Dashboard() {
  const { data, isPending, isError, error } = useQuery<Stats, Error>({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: 5000,
  })

  if (isPending) {
    return <TerminalNote>querying /api/stats ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">stats error: {error.message}</TerminalNote>
  }
  if (!data) {
    return null
  }

  const pending = data.jobs.by_status.pending ?? 0
  const done = data.jobs.by_status.done ?? 0
  const active = data.active_job
  const storage = data.storage
  const usedPct =
    storage.free_gb !== null &&
    storage.total_gb !== null &&
    storage.total_gb > 0
      ? Math.min(
          100,
          Math.round(
            ((storage.total_gb - storage.free_gb) / storage.total_gb) * 100
          )
        )
      : null

  return (
    <>
      <section className="panel">
        <h2>System</h2>
        <div className="stat-grid">
          <Stat value={data.jobs.total} label="Jobs" />
          <Stat value={pending} label="Pending" />
          <Stat value={done} label="Done" />
          <Stat value={data.events} label="Events" />
          <Stat value={data.plates} label="Plates" />
          <Stat value={data.frames_kept} label="Frames Kept" />
          <Stat value={data.transcript_segments} label="Transcript" />
        </div>
      </section>
      <section className="panel">
        <h2>Jobs By Status</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th className="num">Jobs</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.jobs.by_status).map(([status, count]) => (
              <tr
                key={status}
                className={status === "failed" ? "row-threat" : undefined}
              >
                <td>{status}</td>
                <td className="num">{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Active Job</h2>
        {active ? (
          <div>
            <p className="active-line">
              JOB {active.id} — stage {active.stage} — frame {active.current_frame}
              {active.total_frames !== null ? ` / ${active.total_frames}` : ""}
            </p>
            <div className="progress">
              <div
                className="progress-fill"
                style={{
                  width: `${progressPct(active.current_frame, active.total_frames)}%`,
                  background: toneColor("info"),
                }}
              />
            </div>
          </div>
        ) : (
          <TerminalNote>no active job</TerminalNote>
        )}
      </section>
      <section className="panel">
        <h2>Import History</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Import</th>
              <th className="num">Jobs</th>
              <th className="num">Done</th>
              <th>First</th>
              <th>Last</th>
            </tr>
          </thead>
          <tbody>
            {data.imports.map((row) => (
              <tr key={row.import_id}>
                <td>{row.import_id}</td>
                <td className="num">{row.jobs}</td>
                <td className="num">{row.done}</td>
                <td>{fmtDate(row.first_at)}</td>
                <td>{fmtDate(row.last_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Storage</h2>
        <p className="path-note">{storage.artifact_dir}</p>
        {usedPct !== null ? (
          <div>
            <div className="progress">
              <div
                className="progress-fill progress-fill--storage"
                style={{ width: `${usedPct}%` }}
              />
            </div>
            <p className="note">
              {storage.free_gb} GB free of {storage.total_gb} GB — db{" "}
              {fmtBytes(storage.db_bytes)}
            </p>
          </div>
        ) : (
          <p className="note">
            artifact dir offline — db {fmtBytes(storage.db_bytes)}
          </p>
        )}
      </section>
    </>
  )
}
