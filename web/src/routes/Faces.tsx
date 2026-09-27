import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { assignFace, fetchFaces, fetchPersons } from "../api"
import type { AssignBody, FacesPage, PersonsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"
import type { Tone } from "../theme"

const LIMIT = 48

export function AssignControl({
  faceId,
  currentPersonId,
}: {
  faceId: number
  currentPersonId: number | null
}) {
  const queryClient = useQueryClient()
  const [personId, setPersonId] = useState("")
  const [newName, setNewName] = useState("")

  const { data: persons } = useQuery<PersonsPage, Error>({
    queryKey: ["persons", "assign-options"],
    queryFn: () => fetchPersons(new URLSearchParams({ limit: "200" })),
  })

  const mutation = useMutation({
    mutationFn: (body: AssignBody) => assignFace(faceId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["faces"] })
      queryClient.invalidateQueries({ queryKey: ["persons"] })
      queryClient.invalidateQueries({ queryKey: ["person"] })
      queryClient.invalidateQueries({ queryKey: ["job-faces"] })
      setPersonId("")
      setNewName("")
    },
  })

  const body: AssignBody | null = newName.trim()
    ? { new_person_name: newName.trim() }
    : personId
      ? { person_id: Number(personId) }
      : null

  return (
    <div className="assign-row">
      <select
        aria-label="assign to person"
        disabled={mutation.isPending}
        onChange={(event) => setPersonId(event.target.value)}
        value={personId}
      >
        <option value="">person…</option>
        {persons?.items.map((p) => (
          <option key={p.person_id} value={p.person_id}>
            {p.name ?? `PERSON ${String(p.person_id).padStart(3, "0")}`}
          </option>
        ))}
      </select>
      <input
        aria-label="new person name"
        disabled={mutation.isPending}
        onChange={(event) => setNewName(event.target.value)}
        placeholder="new person"
        value={newName}
      />
      <button
        className="app-btn"
        disabled={mutation.isPending || body === null}
        onClick={() => body && mutation.mutate(body)}
        type="button"
      >
        {currentPersonId != null ? "MOVE" : "ASSIGN"}
      </button>
      {mutation.isError ? (
        <span className="assign-error">{mutation.error.message}</span>
      ) : null}
    </div>
  )
}

export function Faces() {
  const [params, setParams] = useSearchParams()
  const offset = Number(params.get("offset") ?? 0)
  const query = new URLSearchParams()
  query.set("limit", String(LIMIT))
  query.set("offset", String(offset))
  const jobId = params.get("job_id")
  if (jobId) {
    query.set("job_id", jobId)
  }

  const { data, isPending, isError, error } = useQuery<FacesPage, Error>({
    queryKey: ["faces", query.toString()],
    queryFn: () => fetchFaces(query),
  })

  const page = (delta: number) => {
    const next = new URLSearchParams(params)
    next.set("offset", String(Math.max(0, offset + delta)))
    setParams(next)
  }

  return (
    <section className="panel">
      <h2>Faces</h2>
      <TerminalNote>
        local detection only — assign faces to persons to name them
      </TerminalNote>
      {isPending ? (
        <TerminalNote>querying /api/faces ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">faces error: {error.message}</TerminalNote>
      ) : !data || data.items.length === 0 ? (
        <TerminalNote>no faces detected yet</TerminalNote>
      ) : (
        <>
          <p className="note">
            {data.total} face capture{data.total === 1 ? "" : "s"}
            {jobId ? ` · job ${jobId}` : ""} · newest first
          </p>
          <div className="face-gallery">
            {data.items.flatMap((item) =>
              item.crops.map((url, idx) => (
                <figure
                  className={"subject subject--" + item.tone + " face-card"}
                  key={url}
                >
                  <span
                    className="designation"
                    style={{ color: toneColor(item.tone as Tone) }}
                  >
                    {item.event_type} // {fmtSec(item.start_sec)}
                  </span>
                  <Link to={`/jobs/${item.job_id}/events/${item.event_id}`}>
                    <img
                      alt={`face job ${item.job_id} event ${item.event_id}`}
                      loading="lazy"
                      src={url}
                    />
                  </Link>
                  <figcaption>
                    {item.person_ids[idx] != null ? (
                      <>
                        <Link to={`/persons/${item.person_ids[idx]}`}>
                          PERSON {String(item.person_ids[idx]).padStart(3, "0")}
                        </Link>{" "}
                        ·{" "}
                      </>
                    ) : null}
                    {fmtDate(item.recorded_at)} ·{" "}
                    <Link className="job-link" to={`/jobs/${item.job_id}`}>
                      job {item.job_id}
                    </Link>
                  </figcaption>
                  {item.face_ids[idx] != null ? (
                    <AssignControl
                      currentPersonId={item.person_ids[idx]}
                      faceId={item.face_ids[idx] as number}
                    />
                  ) : null}
                </figure>
              ))
            )}
          </div>
          <div className="pager">
            <button
              className="app-btn"
              disabled={offset === 0}
              onClick={() => page(-LIMIT)}
              type="button"
            >
              ◀ PREV
            </button>
            <span className="pager-info">
              {data.total === 0
                ? "0"
                : `${offset + 1}–${Math.min(offset + LIMIT, data.total)}`}{" "}
              OF {data.total}
            </span>
            <button
              className="app-btn"
              disabled={offset + LIMIT >= data.total}
              onClick={() => page(LIMIT)}
              type="button"
            >
              NEXT ▶
            </button>
          </div>
        </>
      )}
    </section>
  )
}
