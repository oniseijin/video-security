import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchJobPlates } from "../../api"
import type { JobPlatesPage } from "../../api"
import { PlateCrop } from "../../components/PlateCrop"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"

export function PlatesTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobPlatesPage, Error>({
    queryKey: ["job-plates", jobId],
    queryFn: () => fetchJobPlates(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/plates ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">plates error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return <TerminalNote>no plates read for job {jobId}</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Plates</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Plate</th>
            <th>Raw</th>
            <th>Ken</th>
            <th className="num">Conf</th>
            <th>Read At</th>
            <th>Crop</th>
            <th>Event</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((plate) => (
            <tr key={plate.track_id}>
              <td>{plate.norm_text ?? "—"}</td>
              <td>{plate.raw_text ?? "—"}</td>
              <td>
                {plate.ken ? (
                  <span className="chip chip--plate" title={plate.ken_en ?? undefined}>
                    {plate.ken}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="num">
                {plate.confidence !== null
                  ? plate.confidence.toFixed(2)
                  : "—"}
              </td>
              <td>{fmtSec(plate.read_at_sec)}</td>
              <td>
                {plate.crop_url ? (
                  <PlateCrop
                    alt={`plate ${plate.norm_text ?? plate.track_id} crop`}
                    cropBox={plate.crop_box}
                    cropSrcUrl={plate.crop_src_url}
                    cropUrl={plate.crop_url}
                  />
                ) : (
                  "—"
                )}
              </td>
              <td>
                {plate.event_id !== null ? (
                  <Link
                    className="job-link"
                    to={`/jobs/${jobId}/report?event=${plate.event_id}`}
                  >
                    #{plate.event_id}
                  </Link>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TerminalNote>plate gallery arrives in phase 9</TerminalNote>
    </section>
  )
}
