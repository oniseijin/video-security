import { useLocation } from "react-router-dom"
import { TerminalNote } from "../components/TerminalNote"

export function NotFound() {
  const { pathname } = useLocation()
  if (/^\/jobs\/\d+/.test(pathname)) {
    return (
      <section className="panel">
        <h2>Job Detail</h2>
        <TerminalNote>job detail module standby — phase 6 pending</TerminalNote>
      </section>
    )
  }
  return (
    <section className="panel">
      <h2>Not Found</h2>
      <TerminalNote tone="threat">404 — no route for {pathname}</TerminalNote>
    </section>
  )
}
