export interface SavedSearch {
  id: string
  label: string
  params: string
  created_at: string
}

export type SavedSearchScope = "events" | "plates" | "search"

const KEY = "vs-saved-searches-v1"

type Store = Partial<Record<SavedSearchScope, SavedSearch[]>>

function readStore(): Store {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === "object") {
      return parsed as Store
    }
  } catch {
    return {}
  }
  return {}
}

function writeStore(store: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(store))
  } catch {
    return
  }
}

export function loadSavedSearches(scope: SavedSearchScope): SavedSearch[] {
  return readStore()[scope] ?? []
}

function volatileKeys(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params)
  next.delete("offset")
  next.delete("limit")
  return next
}

export function saveSearch(
  scope: SavedSearchScope,
  label: string,
  params: URLSearchParams
): SavedSearch[] {
  const trimmed = label.trim()
  if (trimmed === "") {
    return loadSavedSearches(scope)
  }
  const entry: SavedSearch = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    label: trimmed,
    params: volatileKeys(params).toString(),
    created_at: new Date().toISOString(),
  }
  const store = readStore()
  const items = [...(store[scope] ?? []), entry].slice(-20)
  store[scope] = items
  writeStore(store)
  return items
}

export function deleteSavedSearch(
  scope: SavedSearchScope,
  id: string
): SavedSearch[] {
  const store = readStore()
  const items = (store[scope] ?? []).filter((s) => s.id !== id)
  store[scope] = items
  writeStore(store)
  return items
}

export function applySavedSearch(saved: SavedSearch): URLSearchParams {
  return new URLSearchParams(saved.params)
}
