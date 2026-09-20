import type { ReactNode } from "react"
import type { Tone } from "../theme"

export function TerminalNote({
  tone,
  children,
}: {
  tone?: Tone
  children: ReactNode
}) {
  return (
    <div className={tone ? `terminal terminal--${tone}` : "terminal"}>
      <p>
        <span className="prompt">vs</span>{'>'} {children}
      </p>
    </div>
  )
}
