import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  applySavedSearch,
  deleteSavedSearch,
  loadSavedSearches,
  saveSearch,
} from "../savedSearches"
import type { SavedSearch, SavedSearchScope } from "../savedSearches"

export function SavedSearches({ scope }: { scope: SavedSearchScope }) {
  const [params, setParams] = useSearchParams()
  const [saved, setSaved] = useState<SavedSearch[]>(() =>
    loadSavedSearches(scope)
  )
  const [label, setLabel] = useState("")

  const onSave = () => {
    setSaved(saveSearch(scope, label, params))
    setLabel("")
  }
  const onApply = (preset: SavedSearch) => {
    setParams(applySavedSearch(preset))
  }
  const onDelete = (id: string) => {
    setSaved(deleteSavedSearch(scope, id))
  }

  return (
    <div className="saved-searches">
      <input
        aria-label="preset name"
        className="saved-search-input"
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            onSave()
          }
        }}
        placeholder="preset name"
        value={label}
      />
      <button className="app-btn" onClick={onSave} type="button">
        SAVE
      </button>
      {saved.map((preset) => (
        <span className="saved-search-chip" key={preset.id}>
          <button
            className="saved-search-apply"
            onClick={() => onApply(preset)}
            title={preset.params || "default view"}
            type="button"
          >
            {preset.label}
          </button>
          <button
            aria-label={`delete ${preset.label}`}
            className="saved-search-delete"
            onClick={() => onDelete(preset.id)}
            type="button"
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )
}
