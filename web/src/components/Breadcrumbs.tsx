import { Link, useLocation } from "react-router-dom"

function labelFor(segment: string): string {
  return /^\d+$/.test(segment) ? `#${segment}` : segment
}

export function Breadcrumbs() {
  const { pathname } = useLocation()
  const segments = pathname.split("/").filter(Boolean)
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumbs">
      <Link to="/">VS</Link>
      {segments.map((segment, index) => {
        const to = `/${segments.slice(0, index + 1).join("/")}`
        const label = labelFor(segment)
        return (
          <span className="crumb" key={to}>
            <span className="crumb-sep">/</span>
            {index === segments.length - 1 ? (
              <span className="crumb-current">{label}</span>
            ) : (
              <Link to={to}>{label}</Link>
            )}
          </span>
        )
      })}
    </nav>
  )
}
