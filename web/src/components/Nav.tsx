import { NavLink } from "react-router-dom"

const LINKS = [
  { to: "/", label: "Dashboard" },
  { to: "/jobs", label: "Jobs" },
  { to: "/events", label: "Events" },
  { to: "/plates", label: "Plates" },
]

export function Nav() {
  return (
    <nav className="app-nav">
      {LINKS.map((link) => (
        <NavLink
          key={link.to}
          to={link.to}
          end={link.to === "/"}
          className={({ isActive }) =>
            isActive ? "app-nav-link active" : "app-nav-link"
          }
        >
          {link.label}
        </NavLink>
      ))}
    </nav>
  )
}
