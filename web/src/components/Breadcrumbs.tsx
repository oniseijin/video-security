import { createContext, useContext, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { Link, useLocation } from "react-router-dom"

interface CrumbOverride {
  label: string | null
  setLabel: (label: string | null) => void
}

const CrumbContext = createContext<CrumbOverride>({
  label: null,
  setLabel: () => {},
})

export function CrumbProvider({ children }: { children: ReactNode }) {
  const [label, setLabel] = useState<string | null>(null)
  return (
    <CrumbContext.Provider value={{ label, setLabel }}>
      {children}
    </CrumbContext.Provider>
  )
}

export function useCrumbLabel(label: string | null) {
  const { setLabel } = useContext(CrumbContext)
  useEffect(() => {
    setLabel(label)
    return () => setLabel(null)
  }, [label, setLabel])
}

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}

function labelFor(segment: string): string {
  const decoded = decodeSegment(segment)
  return /^\d+$/.test(decoded) ? `#${decoded}` : decoded
}

function PathCrumbs() {
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

export function Breadcrumbs() {
  const { label } = useContext(CrumbContext)
  if (label === null) {
    return <PathCrumbs />
  }
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumbs">
      <Link to="/">VS</Link>
      <span className="crumb">
        <span className="crumb-sep">/</span>
        <Link to="/jobs">Jobs</Link>
      </span>
      <span className="crumb">
        <span className="crumb-sep">/</span>
        <span className="crumb-current">{label}</span>
      </span>
    </nav>
  )
}
