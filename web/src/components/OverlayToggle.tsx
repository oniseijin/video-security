import { useEffect, useState } from "react"

function isHidden(storageKey: string): boolean {
  try {
    return localStorage.getItem(storageKey) === "off"
  } catch {
    return false
  }
}

export function OverlayToggle({
  bodyClass,
  label,
  storageKey,
}: {
  bodyClass: string
  label: string
  storageKey: string
}) {
  const [hidden, setHidden] = useState(() => isHidden(storageKey))
  useEffect(() => {
    document.body.classList.toggle(bodyClass, hidden)
    try {
      localStorage.setItem(storageKey, hidden ? "off" : "on")
    } catch {}
  }, [hidden, bodyClass, storageKey])
  return (
    <button
      type="button"
      className={hidden ? "face-toggle off" : "face-toggle"}
      aria-pressed={hidden ? "false" : "true"}
      onClick={() => setHidden(!hidden)}
    >
      {hidden ? `${label} Off` : `${label} On`}
    </button>
  )
}
