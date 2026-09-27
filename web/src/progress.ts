import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { fetchProgress } from "./api"
import type { ActiveJobProgress, ProgressPayload } from "./api"

const POLL_MS = 5000

export function useProgressStream(): {
  active: ActiveJobProgress[]
  live: boolean
} {
  const queryClient = useQueryClient()
  const [active, setActive] = useState<ActiveJobProgress[]>([])
  const [live, setLive] = useState(false)

  useEffect(() => {
    let poll: number | undefined
    let source: EventSource | null = null

    const apply = (payload: ProgressPayload) => {
      queryClient.setQueryData(["stats"], payload.stats)
      setActive(payload.active)
    }
    const stopPolling = () => {
      if (poll !== undefined) {
        window.clearInterval(poll)
        poll = undefined
      }
    }
    const startPolling = () => {
      if (poll === undefined) {
        poll = window.setInterval(() => {
          fetchProgress()
            .then(apply)
            .catch(() => {})
        }, POLL_MS)
      }
    }

    try {
      source = new EventSource("/api/progress/stream")
      source.addEventListener("progress", (evt) => {
        const payload = JSON.parse(
          (evt as MessageEvent<string>).data
        ) as ProgressPayload
        stopPolling()
        setLive(true)
        apply(payload)
      })
      source.onerror = () => {
        setLive(false)
        startPolling()
      }
    } catch {
      startPolling()
    }

    return () => {
      source?.close()
      stopPolling()
    }
  }, [queryClient])

  return { active, live }
}
