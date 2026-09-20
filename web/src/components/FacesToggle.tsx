import { useEffect, useState } from "react"

function facesHidden(): boolean {
  try {
    return localStorage.getItem("vs-faces") === "off"
  } catch {
    return false
  }
}

export function FacesToggle() {
  const [hidden, setHidden] = useState(facesHidden)
  useEffect(() => {
    document.body.classList.toggle("hide-faces", hidden)
    try {
      localStorage.setItem("vs-faces", hidden ? "off" : "on")
    } catch {}
  }, [hidden])
  return (
    <button
      type="button"
      className={hidden ? "face-toggle off" : "face-toggle"}
      aria-pressed={hidden ? "false" : "true"}
      onClick={() => setHidden(!hidden)}
    >
      {hidden ? "Faces Off" : "Faces On"}
    </button>
  )
}
