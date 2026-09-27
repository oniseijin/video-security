import { useMutation, useQueryClient } from "@tanstack/react-query"
import { flagJob, unflagJob } from "../api"

export function FlagToggle({ jobId, note }: { jobId: number; note: string | null }) {
  const queryClient = useQueryClient()
  const flagged = note !== null
  const mutation = useMutation({
    mutationFn: () => {
      if (!flagged) {
        const entered = window.prompt("flag note (optional)")
        return flagJob(jobId, entered === null ? "" : entered)
      }
      return unflagJob(jobId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
      queryClient.invalidateQueries({ queryKey: ["job", jobId] })
    },
  })
  return (
    <>
      <button
        className="app-btn flag-btn"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate()}
        title={flagged ? "unflag job" : "flag job for review"}
        type="button"
      >
        ⚑{flagged ? " ✕" : ""}
      </button>
      {mutation.isError ? (
        <span className="assign-error">{mutation.error.message}</span>
      ) : null}
    </>
  )
}
