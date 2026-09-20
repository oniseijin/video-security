import { TerminalNote } from "../components/TerminalNote"

export function Placeholder({
  label,
  phase,
}: {
  label: string
  phase: number
}) {
  return (
    <section className="panel">
      <h2>{label}</h2>
      <TerminalNote>
        {label.toLowerCase()} module standby — phase {phase} pending
      </TerminalNote>
    </section>
  )
}
