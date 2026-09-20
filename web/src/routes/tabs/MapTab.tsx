import { useQuery } from "@tanstack/react-query"
import { fetchJobGps } from "../../api"
import type { JobGps } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { TrackMap } from "../../components/TrackMap"

export function MapTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobGps, Error>({
    queryKey: ["job-gps", jobId],
    queryFn: () => fetchJobGps(jobId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/gps ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">map error: {error.message}</TerminalNote>
  }
  if (!data || data.points.length === 0) {
    return <TerminalNote>no GPS data (adapter-dependent)</TerminalNote>
  }

  const first = data.points[0]
  const last = data.points[data.points.length - 1]

  return (
    <section className="panel">
      <h2>Map</h2>
      <TrackMap events={data.events} height={340} jobId={jobId} points={data.points} />
      <p className="note">
        {data.points.length} points · {data.events.length} event markers · start:{" "}
        {first.lat.toFixed(5)}, {first.lon.toFixed(5)} · end: {last.lat.toFixed(5)},{" "}
        {last.lon.toFixed(5)}
      </p>
    </section>
  )
}
