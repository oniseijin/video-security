import { useQuery } from "@tanstack/react-query"
import { fetchJobTranscript } from "../../api"
import type { JobTranscriptPage } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { fmtSec } from "../../format"

export function TranscriptTab({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<
    JobTranscriptPage,
    Error
  >({
    queryKey: ["job-transcript", jobId],
    queryFn: () => fetchJobTranscript(jobId),
  })

  if (isPending) {
    return (
      <TerminalNote>querying /api/jobs/{jobId}/transcript ...</TerminalNote>
    )
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">
        transcript error: {error.message}
      </TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return <TerminalNote>no transcript segments for job {jobId}</TerminalNote>
  }

  return (
    <section className="panel">
      <h2>Transcript</h2>
      <div className="terminal transcript-list">
        {data.items.map((seg, index) => (
          <p key={index}>
            <span className="ts">[{fmtSec(seg.start_time)}]</span> {seg.text}
          </p>
        ))}
      </div>
    </section>
  )
}
