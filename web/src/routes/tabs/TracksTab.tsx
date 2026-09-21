import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchJobTracks } from "../../api"
import type { JobTracksPage } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"

export function TracksTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobTracksPage, Error>({
    queryKey: ["job-tracks", jobId],
    queryFn: () => fetchJobTracks(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/tracks ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">tracks error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return <TerminalNote>no tracks for job {jobId}</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Tracks</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Track</th>
            <th>Active</th>
            <th>Direction</th>
            <th className="num">Weaving</th>
            <th>Plate</th>
            <th className="num">Events</th>
            <th>Strip</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((track) => (
            <tr key={track.track_id}>
              <td>
                <Link to={`/jobs/${jobId}/tracks/${track.track_id}`}>
                  #{track.track_id}
                </Link>
                {track.class_id === 0 ? (
                  <span className="chip">PERSON</span>
                ) : null}
              </td>
              <td>
                {fmtSec(track.first_sec)}–{fmtSec(track.last_sec)}
              </td>
              <td>{track.direction ?? "—"}</td>
              <td className="num">
                {track.weaving_score !== null
                  ? track.weaving_score.toFixed(2)
                  : "—"}
              </td>
              <td>{track.plate_norm ?? "—"}</td>
              <td className="num">{track.event_ids.length}</td>
              <td>
                {track.strip.length > 0 ? (
                  <img
                    alt={`track ${track.track_id} strip`}
                    className="thumb"
                    loading="lazy"
                    src={track.strip[0]}
                  />
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
