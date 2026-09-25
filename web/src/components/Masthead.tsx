import { FacesToggle } from "./FacesToggle"
import { OverlayToggle } from "./OverlayToggle"
import { SearchBox } from "./SearchBox"
import { ThemeToggle } from "./ThemeToggle"

export function Masthead() {
  return (
    <header className="masthead">
      <span className="rec-dot" aria-hidden="true" />
      <span className="masthead-title">VS // CONSOLE</span>
      <div className="masthead-meta app-controls">
        <SearchBox />
        <ThemeToggle />
        <FacesToggle />
        <OverlayToggle bodyClass="hide-persons" label="Persons" storageKey="vs-persons" />
        <OverlayToggle bodyClass="hide-animals" label="Animals" storageKey="vs-animals" />
      </div>
    </header>
  )
}
