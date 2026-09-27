import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { fetchPersonDetail, fetchPersons, mergePersons, renamePerson } from "../api"
import type { PersonDetail, PersonsPage } from "../api"
import { TerminalNote } from "../components/TerminalNote"
import { fmtDate, fmtSec } from "../format"
import { toneColor } from "../theme"

function PersonControls({ person }: { person: PersonDetail }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(person.name ?? "")
  const [mergePick, setMergePick] = useState("")
  const [confirmMerge, setConfirmMerge] = useState(false)

  const { data: persons } = useQuery<PersonsPage, Error>({
    queryKey: ["persons", "assign-options"],
    queryFn: () => fetchPersons(new URLSearchParams({ limit: "200" })),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["person", person.person_id] })
    queryClient.invalidateQueries({ queryKey: ["persons"] })
    queryClient.invalidateQueries({ queryKey: ["faces"] })
    queryClient.invalidateQueries({ queryKey: ["job-faces"] })
  }

  const rename = useMutation({
    mutationFn: () => renamePerson(person.person_id, name.trim()),
    onSuccess: invalidate,
  })
  const merge = useMutation({
    mutationFn: () =>
      mergePersons(Number(mergePick), person.person_id),
    onSuccess: () => {
      setConfirmMerge(false)
      setMergePick("")
      invalidate()
    },
  })

  const others = persons?.items.filter((p) => p.person_id !== person.person_id) ?? []

  return (
    <div className="person-controls">
      <div className="assign-row">
        <input
          aria-label="person name"
          disabled={rename.isPending}
          onChange={(event) => setName(event.target.value)}
          placeholder="name this person"
          value={name}
        />
        <button
          className="app-btn"
          disabled={rename.isPending || !name.trim() || name.trim() === person.name}
          onClick={() => rename.mutate()}
          type="button"
        >
          RENAME
        </button>
        {rename.isError ? (
          <span className="assign-error">{rename.error.message}</span>
        ) : null}
      </div>
      <div className="assign-row">
        <select
          aria-label="merge another person into this one"
          disabled={merge.isPending}
          onChange={(event) => {
            setMergePick(event.target.value)
            setConfirmMerge(false)
          }}
          value={mergePick}
        >
          <option value="">merge another person in…</option>
          {others.map((p) => (
            <option key={p.person_id} value={p.person_id}>
              {p.name ?? `PERSON ${String(p.person_id).padStart(3, "0")}`} (
              {p.sightings})
            </option>
          ))}
        </select>
        {mergePick && !confirmMerge ? (
          <button
            className="app-btn"
            onClick={() => setConfirmMerge(true)}
            type="button"
          >
            MERGE…
          </button>
        ) : null}
        {mergePick && confirmMerge ? (
          <>
            <button
              className="app-btn"
              disabled={merge.isPending}
              onClick={() => merge.mutate()}
              type="button"
            >
              CONFIRM MERGE
            </button>
            <button
              className="app-btn"
              onClick={() => setConfirmMerge(false)}
              type="button"
            >
              CANCEL
            </button>
          </>
        ) : null}
        {merge.isError ? (
          <span className="assign-error">{merge.error.message}</span>
        ) : null}
      </div>
    </div>
  )
}

export function PersonDetailRoute() {
  const { id } = useParams()
  const personId = Number(id)

  const { data, isPending, isError, error } = useQuery<PersonDetail, Error>({
    queryKey: ["person", personId],
    queryFn: () => fetchPersonDetail(personId),
  })

  return (
    <section className="panel">
      <h2>
        {data?.name ? `${data.name} · ` : ""}Person{" "}
        {String(personId).padStart(3, "0")}
      </h2>
      {isPending ? (
        <TerminalNote>querying /api/persons/{personId} ...</TerminalNote>
      ) : isError ? (
        <TerminalNote tone="threat">person error: {error.message}</TerminalNote>
      ) : !data ? (
        <TerminalNote>no sightings on record</TerminalNote>
      ) : (
        <>
          <PersonControls person={data} />
          {data.sightings.length === 0 ? (
            <TerminalNote>no sightings on record</TerminalNote>
          ) : (
            <>
              <p className="note">
                {data.total} sighting{data.total === 1 ? "" : "s"} across jobs ·
                newest first
              </p>
              <div className="face-gallery">
                {data.sightings.map((s) => (
                  <figure
                    className={"subject subject--" + s.tone + " face-card"}
                    key={`${s.event_id}-${s.crop_url}`}
                  >
                    <span
                      className="designation"
                      style={{ color: toneColor(s.tone) }}
                    >
                      {s.event_type} // {fmtSec(s.start_sec)} · face {s.face_id}
                    </span>
                    <Link to={`/jobs/${s.job_id}/events/${s.event_id}`}>
                      <img alt="face sighting" loading="lazy" src={s.crop_url} />
                    </Link>
                    <figcaption>
                      {fmtDate(s.recorded_at)} ·{" "}
                      <Link className="job-link" to={`/jobs/${s.job_id}`}>
                        job {s.job_id}
                      </Link>{" "}
                      <Link
                        className="job-link"
                        to={`/jobs/${s.job_id}/faces`}
                      >
                        faces
                      </Link>
                    </figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}
