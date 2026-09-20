import { Link } from "react-router-dom"
import { TerminalNote } from "../../components/TerminalNote"

export function PlaybackTab({
  pair,
}: {
  pair: { job_id: number; channel: string | null } | null
}) {
  return (
    <section className="panel">
      <h2>Playback</h2>
      <TerminalNote>dual front/rear playback arrives in phase 8</TerminalNote>
      {pair ? (
        <p className="note">
          pair:{" "}
          <Link className="job-link" to={`/jobs/${pair.job_id}`}>
            ⇄ {pair.channel ?? "pair"} #{pair.job_id}
          </Link>
        </p>
      ) : (
        <p className="note">no pair</p>
      )}
    </section>
  )
}
