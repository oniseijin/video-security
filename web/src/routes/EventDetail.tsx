import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import {
  fetchEventDetail,
  fetchEventProvenance,
  restoreEvent,
  suppressEvent,
} from "../api"
import type { EventDetail, EventProvenance, ProvenanceVerdict } from "../api"
import { Filmstrip } from "../components/Filmstrip"
import type { FilmFrame } from "../components/Filmstrip"
import { PlateCrop } from "../components/PlateCrop"
import { TerminalNote } from "../components/TerminalNote"
import { fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

function SuppressionControl({ event }: { event: EventDetail }) {
  const queryClient = useQueryClient()
  const suppressed = event.status === "suppressed"
  const mutation = useMutation({
    mutationFn: () =>
      suppressed ? restoreEvent(event.event_id) : suppressEvent(event.event_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["event", event.event_id] })
      queryClient.invalidateQueries({ queryKey: ["events"] })
      queryClient.invalidateQueries({ queryKey: ["job-events"] })
    },
  })
  return (
    <div className="assign-row">
      <button
        className="app-btn"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate()}
        type="button"
      >
        {suppressed ? "RESTORE EVENT" : "SUPPRESS EVENT"}
      </button>
      {mutation.isError ? (
        <span className="assign-error">{mutation.error.message}</span>
      ) : null}
    </div>
  )
}

function verdictLine(stage: string, v: ProvenanceVerdict) {
  const bits: string[] = []
  if (v.relevant !== undefined) {
    bits.push(`relevant=${v.relevant}`)
  }
  if (v.confidence) {
    bits.push(`conf=${v.confidence}`)
  }
  if (v.event_type) {
    bits.push(v.event_type)
  }
  if (v.description) {
    bits.push(v.description)
  }
  if (v.recommended_action) {
    bits.push(`action=${v.recommended_action}`)
  }
  if (v.tiled) {
    bits.push("tiled")
  }
  bits.push(v.model || "model unknown")
  bits.push(`pv ${v.prompt_version || "?"}`)
  if (v.created_at) {
    bits.push(v.created_at.replace("T", " ").slice(0, 19))
  }
  return (
    <p key={v.result_id}>
      <span className="ts">[{stage}]</span> {bits.join(" · ")}
    </p>
  )
}

function Provenance({ eventId }: { eventId: number }) {
  const { data, isPending, isError } = useQuery<EventProvenance, Error>({
    queryKey: ["event-provenance", eventId],
    queryFn: () => fetchEventProvenance(eventId),
  })
  if (isPending) {
    return (
      <section className="panel">
        <h2>Provenance</h2>
        <TerminalNote>querying /api/events/{eventId}/provenance ...</TerminalNote>
      </section>
    )
  }
  if (isError || !data) {
    return null
  }
  const ev = data.prefilter as {
    frames_kept?: number
    vehicle_tracks?: unknown[]
    plates?: unknown[]
    faces?: number
    gps_samples?: number
  } | null
  const prefilterBits: string[] = []
  if (ev) {
    if (typeof ev.frames_kept === "number") {
      prefilterBits.push(`frames ${ev.frames_kept}`)
    }
    if (Array.isArray(ev.vehicle_tracks)) {
      prefilterBits.push(`tracks ${ev.vehicle_tracks.length}`)
    }
    if (Array.isArray(ev.plates)) {
      prefilterBits.push(`plates ${ev.plates.length}`)
    }
    if (typeof ev.faces === "number") {
      prefilterBits.push(`faces ${ev.faces}`)
    }
    if (typeof ev.gps_samples === "number") {
      prefilterBits.push(`gps ${ev.gps_samples}`)
    }
  }
  const d = data.detector
  const c = data.config
  return (
    <section className="panel">
      <h2>Provenance</h2>
      <div className="terminal">
        <p>
          <span className="ts">[detector]</span> {d.event_type} · score{" "}
          {d.detector_score.toFixed(2)} · priority {d.priority.toFixed(2)}
          {" · "}
          {c.yolo_model}@{c.yolo_conf} · threshold {c.score_threshold}
          {c.priority_for_type !== null
            ? ` · priority weight ${c.priority_for_type}`
            : ""}
        </p>
        <p>
          <span className="ts">[prefilter]</span>{" "}
          {prefilterBits.length > 0
            ? prefilterBits.join(" · ")
            : "no job prefilter evidence recorded"}
        </p>
        {data.triage.length === 0
          ? verdictMissing("triage")
          : data.triage.map((v) => verdictLine("triage", v))}
        {data.detail.length === 0
          ? verdictMissing("detail")
          : data.detail.map((v) => verdictLine("detail", v))}
        <p>
          <span className="ts">[status]</span> {data.status}
          {data.llm_result_id !== null
            ? ` · result #${data.llm_result_id}`
            : ""}
        </p>
      </div>
      <TerminalNote>
        verdict chain as recorded — detector thresholds shown are current
        config, not per-run values
      </TerminalNote>
    </section>
  )
}

function verdictMissing(stage: string) {
  return (
    <p>
      <span className="ts">[{stage}]</span> no {stage} result recorded
    </p>
  )
}

export function EventDetailRoute() {
  const { eid } = useParams()
  const eventId = Number(eid)
  const { data, isPending, isError, error } = useQuery<EventDetail, Error>({
    queryKey: ["event", eventId],
    queryFn: () => fetchEventDetail(eventId),
  })

  if (isPending) {
    return <TerminalNote>querying /api/events/{eventId} ...</TerminalNote>
  }
  if (isError) {
    return <TerminalNote tone="threat">event error: {error.message}</TerminalNote>
  }
  if (!data) {
    return <TerminalNote>event {eventId} not found</TerminalNote>
  }

  const frames: FilmFrame[] = data.keyframes.map((kf, i) => ({
    url: kf.url,
    raw_url: kf.raw_url,
    faces: kf.faces,
    boxes: kf.boxes,
    caption: `${data.event_type} // ${fmtSec(data.start_sec)} // frame ${i + 1}`,
  }))

  return (
    <>
      <section className="panel">
        <h2>
          Event {data.event_id}
          <span
            className="tone-tag"
            style={{ color: toneColor(data.tone as Tone) }}
          >
            {data.event_type}
          </span>
        </h2>
        <div className="meta-grid">
          <div>
            <span className="meta-label">RECORDED</span>
            <span className="meta-value">
              {data.recorded_at ? data.recorded_at.replace("T", " ").slice(0, 19) : "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">WINDOW</span>
            <span className="meta-value">
              {fmtSec(data.start_sec)}–{fmtSec(data.end_sec)}
            </span>
          </div>
          <div>
            <span className="meta-label">CATEGORY</span>
            <span className="meta-value">{data.category}</span>
          </div>
          <div>
            <span className="meta-label">PRIORITY</span>
            <span className="meta-value">{data.priority.toFixed(2)}</span>
          </div>
          <div>
            <span className="meta-label">STATUS</span>
            <span className="meta-value">{data.status}</span>
          </div>
          <div>
            <span className="meta-label">LOCATION</span>
            <span className="meta-value">
              {data.location
                ? data.location.label ??
                  `${data.location.lat.toFixed(5)}, ${data.location.lon.toFixed(5)}`
                : "—"}
            </span>
          </div>
          <div>
            <span className="meta-label">JOB</span>
            <span className="meta-value">
              <Link className="job-link" to={`/jobs/${data.job_id}`}>
                {data.job_id}
              </Link>
            </span>
          </div>
          <div>
            <span className="meta-label">REPORT</span>
            <span className="meta-value">
              <Link className="job-link" to={data.links.report}>
                ↗ view in report
              </Link>
            </span>
          </div>
        </div>
        <p className="desc-full">{data.description}</p>
        <SuppressionControl event={data} />
      </section>

      <Provenance eventId={data.event_id} />

      {frames.length > 0 ? (
        <section className="panel">
          <h2>Captures</h2>
          <Filmstrip allowRaw frames={frames} />
        </section>
      ) : null}

      {data.keyframes.some((kf) => kf.face_crops.length > 0) ? (
        <section className="panel">
          <h2>Faces</h2>
          <div className="face-block">
            {data.keyframes.flatMap((kf, i) =>
              kf.face_crops.map((url, j) => (
                <figure className="face-card" key={url}>
                  <img
                    alt={`face ${i + 1}-${j + 1}`}
                    src={url}
                  />
                  <figcaption>
                    face {i + 1}-{j + 1} · {fmtSec(data.start_sec)}
                  </figcaption>
                </figure>
              ))
            )}
          </div>
          <TerminalNote>
            face crops are local detection only — no recognition or embeddings
          </TerminalNote>
        </section>
      ) : null}

      {data.plates.length > 0 ? (
        <section className="panel">
          <h2>Plates</h2>
          <div className="plate-block">
            {data.plates.map((plate) => (
              <div className="plate-card" key={plate.track_id}>
                <PlateCrop
                  alt={`plate ${plate.norm_text ?? ""}`}
                  cropBox={plate.crop_box}
                  cropSrcUrl={plate.crop_src_url}
                  cropUrl={plate.crop_url}
                />
                <div className="chip-row">
                  <span className="chip chip--plate">{plate.norm_text ?? "—"}</span>
                  {plate.ken ? (
                    <span className="chip">
                      {plate.ken} / {plate.ken_en}
                    </span>
                  ) : null}
                </div>
                <p className="note">
                  conf {plate.confidence?.toFixed(2) ?? "—"}
                  {plate.event_id !== null ? (
                    <>
                      {" · "}
                      <Link
                        className="event-id"
                        to={`/jobs/${data.job_id}/events/${plate.event_id}`}
                      >
                        event #{plate.event_id}
                      </Link>
                    </>
                  ) : null}
                </p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {data.transcript_window.length > 0 ? (
        <section className="panel">
          <h2>Transcript</h2>
          <div className="terminal">
            {data.transcript_window.map((seg, i) => (
              <p key={i}>
                <span className="ts">[{fmtSec(seg.start_time)}]</span>{" "}
                {seg.text}
              </p>
            ))}
          </div>
        </section>
      ) : null}

      {data.track ? (
        <section className="panel">
          <h2>Vehicle Track</h2>
          <div className="meta-grid">
            <div>
              <span className="meta-label">TRACK</span>
              <span className="meta-value">
                <Link
                  className="job-link"
                  to={`/jobs/${data.job_id}/tracks/${data.track.track_id}`}
                >
                  #{data.track.track_id}
                </Link>
              </span>
            </div>
            <div>
              <span className="meta-label">ACTIVE</span>
              <span className="meta-value">
                {fmtSec(data.track.first_sec)}–{fmtSec(data.track.last_sec)}
              </span>
            </div>
            <div>
              <span className="meta-label">DIRECTION</span>
              <span className="meta-value">{data.track.direction ?? "—"}</span>
            </div>
            <div>
              <span className="meta-label">WEAVING</span>
              <span className="meta-value">
                {data.track.weaving_score?.toFixed(2) ?? "—"}
              </span>
            </div>
          </div>
          {data.track.strip.length > 0 ? (
            <div className="strip-row">
              {data.track.strip.map((url) => (
                <img
                  alt={`track ${data.track?.track_id} strip`}
                  key={url}
                  src={url}
                />
              ))}
            </div>
          ) : (
            <TerminalNote>no strip frames persisted for this track</TerminalNote>
          )}
        </section>
      ) : null}
    </>
  )
}
