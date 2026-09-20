import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { fetchJobDetail, fetchJobEvents } from "../../api"
import type { JobDetail as JobDetailData, JobEventsPage } from "../../api"
import { DualPlayer } from "../../components/DualPlayer"
import type { PlayTick } from "../../components/DualPlayer"
import { TerminalNote } from "../../components/TerminalNote"
import { toneColor } from "../../theme"
import type { Tone } from "../../theme"

function isRear(job: JobDetailData): boolean {
  if (job.channel) {
    return job.channel === "rear"
  }
  return /\/rear\//.test(job.video_path)
}

export function PlaybackTab({ job }: { job: JobDetailData }) {
  const jobId = job.id
  const pair = job.pair
  const pairId = pair ? pair.job_id : null
  const rearSelf = isRear(job)

  const eventsQuery = useQuery<JobEventsPage, Error>({
    queryKey: ["job-events", jobId],
    queryFn: () => fetchJobEvents(jobId),
  })
  const pairQuery = useQuery<JobDetailData, Error>({
    queryKey: ["job", pairId],
    queryFn: () => fetchJobDetail(pairId ?? 0),
    enabled: pairId !== null,
  })

  const frontJobId = rearSelf && pairId !== null ? pairId : jobId
  const frontSrc = `/api/jobs/${frontJobId}/video`
  const rearSrc = pairId
    ? rearSelf
      ? `/api/jobs/${jobId}/video`
      : `/api/jobs/${jobId}/video?channel=rear`
    : null
  const frontPath = rearSelf && pairQuery.data ? pairQuery.data.video_path : job.video_path
  const rearPath = rearSelf
    ? job.video_path
    : pairQuery.data
      ? pairQuery.data.video_path
      : null

  const ticks: PlayTick[] = (eventsQuery.data?.items ?? []).map((ev) => ({
    event_id: ev.event_id,
    event_type: ev.event_type,
    tone: ev.tone,
    start_sec: ev.start_sec,
  }))
  const toneByType = new Map<string, Tone>()
  for (const ev of eventsQuery.data?.items ?? []) {
    if (!toneByType.has(ev.event_type)) {
      toneByType.set(ev.event_type, ev.tone)
    }
  }

  return (
    <section className="panel">
      <h2>Playback</h2>
      <p className="path-note">{frontPath}</p>
      {rearPath ? (
        <p className="path-note">{rearPath}</p>
      ) : (
        <p className="note">no rear pair for this recording</p>
      )}
      {pair ? (
        <p className="note">
          pair:{" "}
          <Link className="job-link" to={`/jobs/${pair.job_id}`}>
            ⇄ {pair.channel ?? "pair"} #{pair.job_id}
          </Link>
        </p>
      ) : null}
      {eventsQuery.isPending ? (
        <TerminalNote>querying /api/jobs/{jobId}/events ...</TerminalNote>
      ) : eventsQuery.isError ? (
        <TerminalNote tone="threat">
          events error: {eventsQuery.error.message}
        </TerminalNote>
      ) : (
        <DualPlayer events={ticks} frontSrc={frontSrc} jobId={jobId} rearSrc={rearSrc} />
      )}
      <div className="chip-row">
        {Object.entries(job.event_types).map(([type, count]) => (
          <span className="chip" key={type}>
            <span
              className="tone-swatch"
              style={{ background: toneColor(toneByType.get(type) ?? "info") }}
            />
            {type} ×{count}
          </span>
        ))}
      </div>
    </section>
  )
}
