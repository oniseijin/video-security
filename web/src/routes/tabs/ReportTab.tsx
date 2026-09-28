import { useCallback, useEffect, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { fetchAppConfig } from "../../api"
import type { AppConfig, JobDetail as JobDetailData } from "../../api"
import { TerminalNote } from "../../components/TerminalNote"
import { ReportView } from "../ReportView"
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

function ClassicReportFrame({ jobId }: { jobId: number }) {
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

export function ReportTab({ jobId, job }: { jobId: number; job: JobDetailData }) {
  const { data: appConfig, isPending } = useQuery<AppConfig, Error>({
    queryKey: ["app-config"],
    queryFn: fetchAppConfig,
    staleTime: Infinity,
  })
  if (isPending) {
    return <TerminalNote>querying /api/config ...</TerminalNote>
  }
  if (appConfig?.native_report) {
    return <ReportView job={job} />
  }
  return <ClassicReportFrame jobId={jobId} />
}
