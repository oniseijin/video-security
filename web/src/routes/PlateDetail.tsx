import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { fetchPlateDetail } from "../api"
import type { PlateDetail as PlateDetailData } from "../api"
import { PlateCrop } from "../components/PlateCrop"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"

export function PlateDetailRoute() {
  const { norm } = useParams()
  const normText = norm ?? ""
  const { data, isPending, isError, error } = useQuery<PlateDetailData, Error>(
    {
      queryKey: ["plate", normText],
      queryFn: () => fetchPlateDetail(normText),
      enabled: normText !== "",
    }
  )

  if (isPending) {
    return <TerminalNote>querying /api/plates/{normText} ...</TerminalNote>
  }
  if (isError) {
    return (
      <section className="panel">
        <h2>Plate</h2>
        <TerminalNote tone="threat">plate error: {error.message}</TerminalNote>
        <p className="note">
          <Link to="/plates">back to plates</Link>
        </p>
      </section>
    )
  }
  if (!data) {
    return (
      <section className="panel">
        <h2>Plate</h2>
        <TerminalNote>plate {normText} not found</TerminalNote>
        <p className="note">
          <Link to="/plates">back to plates</Link>
        </p>
      </section>
    )
  }

  return (
    <>
      <section className="panel">
        <h2>Plate</h2>
        <div className="meta-grid">
          <div>
            <span className="meta-label">PLATE</span>
            <span className="meta-value">
              <span className="chip chip--plate">{data.norm_text}</span>
            </span>
          </div>
          <div>
            <span className="meta-label">KEN</span>
            <span className="meta-value">
              {data.ken ? `${data.ken} / ${data.ken_en ?? "—"}` : "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">SIGHTINGS</span>
            <span className="meta-value">{data.sightings.length}</span>
          </div>
        </div>
      </section>
      <section className="panel">
        <h2>Sightings</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Recorded</th>
              <th>Job</th>
              <th>Read At</th>
              <th className="num">Conf</th>
              <th>Event</th>
              <th>Crop</th>
            </tr>
          </thead>
          <tbody>
            {data.sightings.map((sighting, index) => (
              <tr key={`${sighting.job_id}-${sighting.track_id}-${index}`}>
                <td>{fmtDate(sighting.recorded_at)}</td>
                <td>
                  <Link className="job-link" to={`/jobs/${sighting.job_id}`}>
                    {sighting.job_id}
                  </Link>
                </td>
                <td>{fmtSec(sighting.read_at_sec)}</td>
                <td className="num">
                  {sighting.confidence !== null
                    ? sighting.confidence.toFixed(2)
                    : "—"}
                </td>
                <td>
                  {sighting.event_id !== null ? (
                    <Link
                      className="event-id"
                      to={`/jobs/${sighting.job_id}/events/${sighting.event_id}`}
                    >
                      #{sighting.event_id}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {sighting.crop_url ? (
                    <PlateCrop
                      alt={`sighting ${index + 1} crop`}
                      cropBox={sighting.crop_box}
                      cropSrcUrl={sighting.crop_src_url}
                      cropUrl={sighting.crop_url}
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
    </>
  )
}
