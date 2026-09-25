import { useCallback, useEffect, useRef } from "react"
import { useSearchParams } from "react-router-dom"
import { themeAttr } from "../../theme"

function syncFrame(frame: HTMLIFrameElement | null): void {
  let doc: Document | null = null
  try {
    doc = frame?.contentDocument ?? null
  } catch {
    doc = null
  }
  if (doc === null || doc.documentElement === null || doc.body === null) {
    return
  }
  doc.documentElement.setAttribute("data-theme", themeAttr())
  doc.body.classList.toggle(
    "hide-faces",
    document.body.classList.contains("hide-faces")
  )
  doc.body.classList.toggle(
    "hide-persons",
    document.body.classList.contains("hide-persons")
  )
  doc.body.classList.toggle(
    "hide-animals",
    document.body.classList.contains("hide-animals")
  )
}

export function ReportTab({ jobId }: { jobId: number }) {
  const [searchParams] = useSearchParams()
  const event = searchParams.get("event")
  const frameRef = useRef<HTMLIFrameElement>(null)

  const sync = useCallback(() => {
    syncFrame(frameRef.current)
  }, [])

  useEffect(() => {
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    })
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["class"],
    })
    sync()
    return () => observer.disconnect()
  }, [sync])

  const anchor = event !== null && /^\d+$/.test(event) ? `#event-${event}` : ""
  const src = `/api/jobs/${jobId}/report.html?embed=1${anchor}`

  return (
    <section className="panel">
      <h2>Report</h2>
      <iframe
        ref={frameRef}
        className="report-frame"
        src={src}
        title={`Report for job ${jobId}`}
        onLoad={sync}
      />
      <p className="note">
        rendered on demand from the DB · vs report {jobId} exports standalone
        HTML
      </p>
    </section>
  )
}
