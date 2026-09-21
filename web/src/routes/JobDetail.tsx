import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import { fetchJobDetail } from "../api"
import type { JobDetail as JobDetailData } from "../api"
import { useCrumbLabel } from "../components/Breadcrumbs"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { CapturesTab } from "./tabs/CapturesTab"
import { EventsTab } from "./tabs/EventsTab"
import { FacesTab } from "./tabs/FacesTab"
import { MapTab } from "./tabs/MapTab"
import { PlaybackTab } from "./tabs/PlaybackTab"
import { PlatesTab } from "./tabs/PlatesTab"
import { ReportTab } from "./tabs/ReportTab"
import { TracksTab } from "./tabs/TracksTab"
import { TranscriptTab } from "./tabs/TranscriptTab"

const TABS = [
  "report",
  "events",
  "captures",
  "faces",
  "plates",
  "tracks",
  "map",
  "transcript",
  "playback",
] as const

type Tab = (typeof TABS)[number]

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="meta-item">
      <span className="meta-label">{label}</span>
      <span className="meta-value">{children}</span>
    </div>
  )
}

function channelOf(job: JobDetailData): string | null {
  if (job.channel) {
    return job.channel
  }
  const match = /\/(front|rear)\//.exec(job.video_path)
  return match ? match[1] : null
}

function Header({ job }: { job: JobDetailData }) {
  return (
    <section className="panel">
      <h2>Job {job.id}</h2>
      <div className="meta-grid">
        <Meta label="recorded">
          <span className="meta-primary">{fmtDate(job.recorded_at)}</span>
        </Meta>
        <Meta label="status">{job.status}</Meta>
        <Meta label="mode">{job.mode}</Meta>
        <Meta label="channel">{channelOf(job) ?? "—"}</Meta>
        <Meta label="device">{job.device ? `${job.device.kind}${job.device.model ? ` · ${job.device.model}` : ""}` : "—"}</Meta>
        <Meta label="imported">{fmtDate(job.imported_at)}</Meta>
        <Meta label="duration">{fmtSec(job.duration_sec)}</Meta>
        <Meta label="pair">
          {job.pair ? (
            <Link className="job-link" to={`/jobs/${job.pair.job_id}`}>
              ⇄ {job.pair.channel ?? "pair"} #{job.pair.job_id}
            </Link>
          ) : (
            "no pair"
          )}
        </Meta>
        {job.archive ? (
          <Meta label="flag">
            <span className="chip">ARCHIVE</span>
          </Meta>
        ) : null}
      </div>
      <p className="path-note">{job.video_path}</p>
      <div className="count-row">
        <Link className="count-chip" to={`/jobs/${job.id}/events`}>
          events {job.counts.events}
        </Link>
        <Link className="count-chip" to={`/jobs/${job.id}/plates`}>
          plates {job.counts.plates}
        </Link>
        <Link className="count-chip" to={`/jobs/${job.id}/faces`}>
          faces {job.counts.faces}
        </Link>
        <Link className="count-chip" to={`/jobs/${job.id}/transcript`}>
          transcript {job.counts.transcript_segments}
        </Link>
        <span className="count-chip">frames {job.counts.frames_kept}</span>
      </div>
      {Object.keys(job.event_types).length > 0 ? (
        <div className="chip-row">
          {Object.entries(job.event_types).map(([type, count]) => (
            <span className="chip" key={type}>
              {type} ×{count}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  )
}

export function JobDetail() {
  const { id, tab } = useParams()
  const jobId = Number.parseInt(id ?? "", 10)
  const valid = Number.isInteger(jobId)
  const active: Tab = (TABS as readonly string[]).includes(tab ?? "")
    ? (tab as Tab)
    : "report"

  const { data, isPending, isError, error } = useQuery<JobDetailData, Error>({
    queryKey: ["job", jobId],
    queryFn: () => fetchJobDetail(jobId),
    enabled: valid,
  })

  const crumb = !valid
    ? null
    : data
      ? `Job ${jobId} (${fmtDate(data.recorded_at).slice(0, 10)} ${data.mode}${
          channelOf(data) ? ` ${channelOf(data)}` : ""
        })`
      : `Job ${jobId}`
  useCrumbLabel(crumb)

  if (!valid) {
    return (
      <section className="panel">
        <h2>Job Detail</h2>
        <TerminalNote tone="threat">invalid job id: {id}</TerminalNote>
      </section>
    )
  }
  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId} ...</TerminalNote>
  }
  if (isError || !data) {
    return (
      <section className="panel">
        <h2>Job {jobId}</h2>
        <TerminalNote tone="threat">
          job {jobId} unavailable: {error?.message ?? "not found"}
        </TerminalNote>
        <p className="note">
          <Link to="/jobs">back to jobs</Link>
        </p>
      </section>
    )
  }

  return (
    <>
      <Header job={data} />
      <nav className="tab-bar" aria-label="Job views">
        {TABS.map((t) => (
          <Link
            key={t}
            to={`/jobs/${jobId}/${t}`}
            className={active === t ? "tab-link active" : "tab-link"}
          >
            {t}
          </Link>
        ))}
      </nav>
      {active === "report" ? <ReportTab jobId={jobId} /> : null}
      {active === "events" ? <EventsTab jobId={jobId} /> : null}
      {active === "captures" ? <CapturesTab jobId={jobId} /> : null}
      {active === "faces" ? <FacesTab jobId={jobId} /> : null}
      {active === "plates" ? <PlatesTab jobId={jobId} /> : null}
      {active === "tracks" ? <TracksTab jobId={jobId} /> : null}
      {active === "map" ? <MapTab jobId={jobId} /> : null}
      {active === "transcript" ? <TranscriptTab jobId={jobId} /> : null}
      {active === "playback" ? <PlaybackTab job={data} /> : null}
    </>
  )
}
