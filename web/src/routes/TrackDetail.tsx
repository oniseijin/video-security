import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { fetchJobTracks } from "../api"
import type { JobTracksPage } from "../api"
import { Filmstrip } from "../components/Filmstrip"
import type { FilmFrame } from "../components/Filmstrip"
import { TerminalNote } from "../components/TerminalNote"
import { fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

export function TrackDetailRoute() {
  const { id, tid } = useParams()
  const jobId = Number(id)
  const trackId = Number(tid)
  const { data, isPending, isError, error } = useQuery<JobTracksPage, Error>({
    queryKey: ["job-tracks", jobId],
    queryFn: () => fetchJobTracks(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/tracks ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">tracks error: {error.message}</TerminalNote>
  }
  const track = data?.items.find((t) => t.track_id === trackId)
  if (!track) {
    return <TerminalNote>track {trackId} not found for job {jobId}</TerminalNote>
  }

  const frames: FilmFrame[] = track.strip.map((url, i) => ({
    url,
    faces: [],
    caption: `track ${trackId} // frame ${i + 1}`,
  }))

  return (
    <>
      <section className="panel">
        <h2>Track {track.track_id}</h2>
        <div className="meta-grid">
          <div>
            <span className="meta-label">ACTIVE</span>
            <span className="meta-value">
              {fmtSec(track.first_sec)}–{fmtSec(track.last_sec)}
            </span>
          </div>
          <div>
            <span className="meta-label">DIRECTION</span>
            <span className="meta-value">{track.direction ?? "—"}</span>
          </div>
          <div>
            <span className="meta-label">WEAVING</span>
            <span className="meta-value">
              {track.weaving_score?.toFixed(2) ?? "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">PLATE</span>
            <span className="meta-value">
              {track.plate_norm ? (
                <Link to={`/plates/${encodeURIComponent(track.plate_norm)}`}>
                  {track.plate_norm}
                </Link>
              ) : (
                "—"
              )}
            </span>
          </div>
          <div>
            <span className="meta-label">EVENTS</span>
            <span className="meta-value">
              {track.event_ids.length > 0
                ? track.event_ids.map((eid) => (
                    <Link
                      className="event-id"
                      key={eid}
                      style={{ color: toneColor("info" as Tone) }}
                      to={`/jobs/${jobId}/events/${eid}`}
                    >
                      #{eid}{" "}
                    </Link>
                  ))
                : "—"}
            </span>
          </div>
        </div>
      </section>
      {frames.length > 0 ? (
        <section className="panel">
          <h2>Strip</h2>
          <Filmstrip frames={frames} />
        </section>
      ) : (
        <TerminalNote>no strip frames persisted for this track</TerminalNote>
      )}
    </>
  )
}
