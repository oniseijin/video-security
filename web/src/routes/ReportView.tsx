import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  fetchEventDetail,
  fetchJobEvents,
  fetchJobFaces,
  fetchJobGps,
  fetchJobPlates,
  fetchJobTracks,
  fetchJobTranscript,
} from "../api"
import type {
  EventDetail,
  EventSummary,
  JobDetail as JobDetailData,
  JobEventsPage,
  JobFaces,
  JobGps,
  JobPlatesPage,
  JobTracksPage,
  JobTranscriptPage,
} from "../api"
import { BoxOverlay } from "../components/BoxOverlay"
import { FaceBoxes, toFaceBoxes } from "../components/FaceBoxes"
import { Lightbox } from "../components/Lightbox"
import type { LightboxFrame } from "../components/Lightbox"
import { PlateCrop } from "../components/PlateCrop"
import { TerminalNote } from "../components/TerminalNote"
import { TrackMap } from "../components/TrackMap"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

interface PersonName {
  personId: number
  name: string
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="meta-item">
      <span className="meta-label">{label}</span>
      <span className="meta-value">{children}</span>
    </div>
  )
}

function archiveFlag(job: JobDetailData): string | null {
  if (!job.recorded_at || !job.imported_at) {
    return null
  }
  const recorded = Date.parse(job.recorded_at)
  const imported = Date.parse(job.imported_at)
  if (Number.isNaN(recorded) || Number.isNaN(imported)) {
    return null
  }
  const days = Math.floor((imported - recorded) / 86400000)
  if (days <= 1) {
    return null
  }
  return (
    `Archive import · recorded ${job.recorded_at.slice(0, 10)} · ` +
    `imported ${job.imported_at.slice(0, 10)} · ${days} days later`
  )
}

function ReportHeader({ job }: { job: JobDetailData }) {
  const flag = archiveFlag(job)
  return (
    <section className="panel">
      <h2>Report</h2>
      <div className="meta-grid">
        <Meta label="recorded">
          <span className="meta-primary">{fmtDate(job.recorded_at)}</span>
        </Meta>
        <Meta label="status">{job.status}</Meta>
        <Meta label="mode">{job.mode}</Meta>
        <Meta label="channel">{job.channel ?? "—"}</Meta>
        <Meta label="device">
          {job.device
            ? `${job.device.kind}${job.device.model ? ` · ${job.device.model}` : ""}`
            : "—"}
        </Meta>
        <Meta label="import id">{job.import_id ?? "—"}</Meta>
        <Meta label="imported">{fmtDate(job.imported_at)}</Meta>
        <Meta label="duration">{fmtSec(job.duration_sec)}</Meta>
      </div>
      {flag !== null ? <p className="flag">{flag}</p> : null}
      <p className="filepath">{job.video_path}</p>
      <p className="note">
        native report ·{" "}
        <a
          className="job-link"
          href={`/api/jobs/${job.id}/report.html`}
          rel="noreferrer"
          target="_blank"
        >
          classic report ↗
        </a>{" "}
        (print/export stay there)
      </p>
    </section>
  )
}

function SummaryPanel({ job }: { job: JobDetailData }) {
  return (
    <section className="panel">
      <h2>Summary</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="num">Count</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(job.event_types)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([type, count]) => (
              <tr key={type}>
                <td>Events: {type}</td>
                <td className="num">{count}</td>
              </tr>
            ))}
          <tr>
            <td>Plates</td>
            <td className="num">{job.counts.plates}</td>
          </tr>
          <tr>
            <td>Transcript segments</td>
            <td className="num">{job.counts.transcript_segments}</td>
          </tr>
          <tr>
            <td>Frames kept</td>
            <td className="num">{job.counts.frames_kept}</td>
          </tr>
          {Object.entries(job.status_counts)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([status, count]) => (
              <tr key={status}>
                <td>Status: {status}</td>
                <td className="num">{count}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </section>
  )
}

function TimelinePanel({
  jobId,
  events,
}: {
  jobId: number
  events: EventSummary[]
}) {
  return (
    <section className="panel">
      <h2>Timeline</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Start</th>
            <th>End</th>
            <th>Recorded</th>
            <th>Type</th>
            <th>Category</th>
            <th>Id</th>
            <th className="num">Priority</th>
            <th className="num">Score</th>
            <th>Status</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {events.map((evt) => (
            <tr
              className={
                evt.status === "suppressed" ? "row-suppressed" : `row-${evt.tone}`
              }
              key={evt.event_id}
            >
              <td>{fmtSec(evt.start_sec)}</td>
              <td>{fmtSec(evt.end_sec)}</td>
              <td>
                {evt.recorded_at
                  ? evt.recorded_at.replace("T", " ").slice(0, 19)
                  : "—"}
              </td>
              <td>{evt.event_type}</td>
              <td>{evt.category}</td>
              <td>
                <Link
                  className="event-id"
                  style={{ color: toneColor(evt.tone) }}
                  to={`/jobs/${jobId}/events/${evt.event_id}`}
                >
                  #{evt.event_id}
                </Link>
              </td>
              <td className="num">{evt.priority.toFixed(2)}</td>
              <td className="num">{evt.detector_score.toFixed(3)}</td>
              <td>{evt.status}</td>
              <td className="desc-cell" title={evt.description}>
                {evt.description}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function LocationPanel({ jobId }: { jobId: number }) {
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
    return null
  }
  const first = data.points[0]
  const last = data.points[data.points.length - 1]
  return (
    <section className="panel">
      <h2>Location Track</h2>
      <TrackMap events={data.events} height={340} jobId={jobId} points={data.points} />
      <p className="note">
        {data.points.length} points · {data.events.length} event markers · start:{" "}
        {first.lat.toFixed(5)}, {first.lon.toFixed(5)} · end: {last.lat.toFixed(5)},{" "}
        {last.lon.toFixed(5)}
      </p>
    </section>
  )
}

function ReportEventFigure({
  jobId,
  eventId,
  tone,
  personNames,
  focus,
  onOpen,
}: {
  jobId: number
  eventId: number
  tone: Tone
  personNames: PersonName[]
  focus: boolean
  onOpen: (frames: LightboxFrame[], index: number) => void
}) {
  const { data } = useQuery<EventDetail, Error>({
    queryKey: ["event", eventId],
    queryFn: () => fetchEventDetail(eventId),
  })
  const ref = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (focus && data !== undefined && ref.current !== null) {
      ref.current.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }, [focus, data])
  if (data === undefined || data.keyframes.length === 0) {
    return null
  }
  const faceCount = data.keyframes.reduce((n, kf) => n + kf.faces.length, 0)
  const designation =
    `${data.event_type} // ${fmtSec(data.start_sec)}` +
    (faceCount > 0 ? ` // ${faceCount} face${faceCount !== 1 ? "s" : ""}` : "")
  const caption =
    `Event ${eventId}: ${data.event_type}` +
    (data.description ? ` — ${data.description}` : "")
  const frames: LightboxFrame[] = data.keyframes.map((kf) => ({
    url: kf.url,
    faces: kf.faces,
    boxes: kf.boxes,
    caption,
  }))
  return (
    <figure
      className={`subject subject--${data.tone}`}
      id={`event-${eventId}`}
      ref={ref}
    >
      <span className="designation">
        {designation}
        {personNames.length > 0 ? (
          <>
            {" · "}
            {personNames.map((p) => (
              <Link className="chip" key={p.personId} to={`/persons/${p.personId}`}>
                {p.name}
              </Link>
            ))}
          </>
        ) : null}
      </span>
      <div className="kf-stack">
        {data.keyframes.map((kf, i) => (
          <div className="kf-wrap" key={kf.url}>
            <img
              alt={caption}
              loading="lazy"
              onClick={() => onOpen(frames, i)}
              src={kf.url}
            />
            <FaceBoxes boxes={toFaceBoxes(kf.faces)} />
            <BoxOverlay boxes={kf.boxes} />
          </div>
        ))}
      </div>
      <figcaption>
        <Link
          className="event-id"
          style={{ color: toneColor(tone) }}
          to={`/jobs/${jobId}/events/${eventId}`}
        >
          #{eventId}
        </Link>{" "}
        {data.description}
      </figcaption>
    </figure>
  )
}

function KeyframesPanel({
  job,
  events,
  focusEventId,
  namesByEvent,
}: {
  job: JobDetailData
  events: EventSummary[]
  focusEventId: number | null
  namesByEvent: Map<number, PersonName[]>
}) {
  const [lightboxFrames, setLightboxFrames] = useState<LightboxFrame[] | null>(null)
  const [lightboxIndex, setLightboxIndex] = useState(-1)
  const open = (frames: LightboxFrame[], index: number) => {
    setLightboxFrames(frames)
    setLightboxIndex(index)
  }
  const close = () => {
    setLightboxFrames(null)
    setLightboxIndex(-1)
  }
  if (events.length === 0) {
    return null
  }
  return (
    <section className="panel">
      <h2>Keyframes</h2>
      {events.map((evt) => (
        <ReportEventFigure
          eventId={evt.event_id}
          focus={evt.event_id === focusEventId}
          jobId={job.id}
          key={evt.event_id}
          onOpen={open}
          personNames={namesByEvent.get(evt.event_id) ?? []}
          tone={evt.tone}
        />
      ))}
      <Lightbox
        frames={lightboxFrames ?? []}
        index={lightboxIndex}
        onClose={close}
        onIndex={setLightboxIndex}
      />
      <p className="note">
        person/animal overlays follow the console toggles · click a frame for the
        shared lightbox (zoom/pan + boxes)
      </p>
    </section>
  )
}

function PlatesPanel({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobPlatesPage, Error>({
    queryKey: ["job-plates", jobId],
    queryFn: () => fetchJobPlates(jobId),
  })
  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/plates ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">plates error: {error.message}</TerminalNote>
  }
  if (!data || data.items.length === 0) {
    return (
      <section className="panel">
        <h2>Plates</h2>
        <p className="note">No plates detected.</p>
      </section>
    )
  }
  return (
    <section className="panel">
      <h2>Plates</h2>
      <div className="chip-row">
        {data.items.map((plate) => {
          const body =
            `${plate.norm_text ?? ""} · raw ${plate.raw_text ?? ""} · conf ` +
            `${plate.confidence?.toFixed(2) ?? "—"}${
              plate.ken ? ` · ${plate.ken} (${plate.ken_en})` : ""
            }`
          return plate.norm_text ? (
            <Link
              className="chip chip--plate"
              key={plate.track_id}
              title="plate detail"
              to={`/plates/${plate.norm_text}`}
            >
              {body}
            </Link>
          ) : (
            <span className="chip chip--plate" key={plate.track_id}>
              {body}
            </span>
          )
        })}
      </div>
      <div className="plate-crops">
        {data.items
          .filter((plate) => plate.crop_url !== null)
          .map((plate) => (
            <PlateCrop
              alt={`plate ${plate.norm_text ?? plate.track_id} crop`}
              cropBox={plate.crop_box}
              cropSrcUrl={plate.crop_src_url}
              cropUrl={plate.crop_url}
              key={plate.track_id}
            />
          ))}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Plate</th>
            <th>Raw</th>
            <th>Ken</th>
            <th className="num">Confidence</th>
            <th className="num">Read At</th>
            <th className="num">Vehicle</th>
            <th>Event</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((plate) => (
            <tr key={plate.track_id}>
              <td>
                {plate.norm_text ? (
                  <Link
                    className="job-link"
                    title="plate detail"
                    to={`/plates/${plate.norm_text}`}
                  >
                    {plate.norm_text}
                  </Link>
                ) : (
                  "—"
                )}
              </td>
              <td>{plate.raw_text ?? "—"}</td>
              <td>
                {plate.ken ? (
                  <span className="chip" title={plate.ken_en ?? undefined}>
                    {plate.ken}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="num">{plate.confidence?.toFixed(2) ?? "—"}</td>
              <td className="num">{fmtSec(plate.read_at_sec)}</td>
              <td className="num">
                <Link to={`/jobs/${jobId}/tracks/${plate.track_id}`}>
                  #{plate.track_id}
                </Link>
              </td>
              <td>
                {plate.event_id !== null ? (
                  <Link
                    className="job-link"
                    to={`/jobs/${jobId}/events/${plate.event_id}`}
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
      <p className="note">
        plate chips and names open plate detail · crops open the source frame with
        the plate region marked
      </p>
    </section>
  )
}

function TranscriptPanel({ jobId }: { jobId: number }) {
  const { data, isPending, isError, error } = useQuery<JobTranscriptPage, Error>({
    queryKey: ["job-transcript", jobId],
    queryFn: () => fetchJobTranscript(jobId),
  })
  if (isPending) {
    return <TerminalNote>querying /api/jobs/{jobId}/transcript ...</TerminalNote>
  }
  if (isError) {
    return (
      <TerminalNote tone="threat">transcript error: {error.message}</TerminalNote>
    )
  }
  if (!data || data.items.length === 0) {
    return null
  }
  return (
    <section className="panel">
      <h2>Transcript</h2>
      <div className="terminal transcript-list">
        {data.items.map((seg, i) => (
          <p key={i}>
            <span className="prompt">{">"}</span>{" "}
            <span className="ts">
              [{fmtSec(seg.start_time)} - {fmtSec(seg.end_time)}]
            </span>{" "}
            {seg.text}
          </p>
        ))}
      </div>
    </section>
  )
}

function DrivingLogPanel({ jobId }: { jobId: number }) {
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
  if (!data || data.items.length === 0) {
    return (
      <section className="panel">
        <h2>Driving Log</h2>
        <p className="note">No driving events.</p>
      </section>
    )
  }
  return (
    <section className="panel">
      <h2>Driving Log</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">Vehicle</th>
            <th>Active</th>
            <th>Direction</th>
            <th className="num">Weaving Score</th>
            <th className="num">Events</th>
            <th>Strip</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((track) => (
            <tr key={track.track_id}>
              <td className="num">
                <Link to={`/jobs/${jobId}/tracks/${track.track_id}`}>
                  #{track.track_id}
                </Link>
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
              <td className="num">{track.event_ids.length}</td>
              <td>
                {track.strip.length > 0 ? (
                  <div className="strip-row">
                    {track.strip.map((url) => (
                      <img
                        alt={`track ${track.track_id} strip`}
                        key={url}
                        loading="lazy"
                        src={url}
                      />
                    ))}
                  </div>
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

export function ReportView({ job }: { job: JobDetailData }) {
  const [searchParams] = useSearchParams()
  const focusParam = searchParams.get("event")
  const focusEventId =
    focusParam !== null && /^\d+$/.test(focusParam) ? Number(focusParam) : null

  const eventsQuery = useQuery<JobEventsPage, Error>({
    queryKey: ["job-events", job.id],
    queryFn: () => fetchJobEvents(job.id),
  })
  const facesQuery = useQuery<JobFaces, Error>({
    queryKey: ["job-faces", job.id],
    queryFn: () => fetchJobFaces(job.id),
    enabled: job.counts.faces > 0,
  })
  const namesByEvent = useMemo(() => {
    const map = new Map<number, PersonName[]>()
    for (const group of facesQuery.data?.groups ?? []) {
      for (const crop of group.crops) {
        if (crop.person_id === null || crop.person_name === null) {
          continue
        }
        const list = map.get(crop.event_id) ?? []
        if (!list.some((p) => p.personId === crop.person_id)) {
          list.push({ personId: crop.person_id, name: crop.person_name })
        }
        map.set(crop.event_id, list)
      }
    }
    return map
  }, [facesQuery.data])

  const events = eventsQuery.data?.items ?? []

  return (
    <>
      <ReportHeader job={job} />
      <SummaryPanel job={job} />
      {eventsQuery.isPending ? (
        <TerminalNote>querying /api/jobs/{job.id}/events ...</TerminalNote>
      ) : eventsQuery.isError ? (
        <TerminalNote tone="threat">
          events error: {eventsQuery.error.message}
        </TerminalNote>
      ) : (
        <TimelinePanel events={events} jobId={job.id} />
      )}
      {job.has_gps ? <LocationPanel jobId={job.id} /> : null}
      {eventsQuery.isError ? null : (
        <KeyframesPanel
          events={events}
          focusEventId={focusEventId}
          job={job}
          namesByEvent={namesByEvent}
        />
      )}
      <PlatesPanel jobId={job.id} />
      {job.has_transcript ? <TranscriptPanel jobId={job.id} /> : null}
      <DrivingLogPanel jobId={job.id} />
    </>
  )
}
