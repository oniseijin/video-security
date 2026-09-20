import { useLocation } from "react-router-dom"
import { TerminalNote } from "../components/TerminalNote"

export function NotFound() {
  const { pathname } = useLocation()
  return (
    <section className="panel">
      <h2>Not Found</h2>
      <TerminalNote tone="threat">404 — no route for {pathname}</TerminalNote>
    </section>
  )
}
