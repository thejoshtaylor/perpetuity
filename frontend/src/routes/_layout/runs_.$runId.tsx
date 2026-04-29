import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { AlertCircle, ChevronLeft, Loader2, XCircle } from "lucide-react"
import { toast } from "sonner"
import { isRunInFlight, workflowRunQueryKey, workflowRunQueryOptions } from "@/api/workflows"
import type { StepRunPublic, WorkflowRunPublic } from "@/client"
import { WorkflowsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

export const Route = createFileRoute("/_layout/runs_/$runId")({
  component: RunDetailPage,
  head: () => ({
    meta: [
      {
        title: "Workflow run",
      },
    ],
  }),
})

type RunStatus = WorkflowRunPublic["status"]
type StepStatus = StepRunPublic["status"]

const RUN_STATUS_VARIANT: Record<
  RunStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  cancelled: "outline",
}

const STEP_STATUS_VARIANT: Record<
  StepStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  skipped: "outline",
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—"
  return new Date(value).toLocaleString()
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—"
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function actionLabel(snapshot: StepRunPublic["snapshot"]): string {
  const action = snapshot?.action
  if (typeof action === "string" && action.length > 0) return action
  return "step"
}

function StepRow({ step }: { step: StepRunPublic }) {
  const isRunning = step.status === "running"
  const isFailed = step.status === "failed"
  return (
    <li
      data-testid={`step-run-row-${step.step_index}`}
      data-step-index={step.step_index}
      data-status={step.status}
      className="flex flex-col gap-2 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          #{step.step_index + 1}
        </span>
        <span
          className="font-medium"
          data-testid={`step-run-action-${step.step_index}`}
        >
          {actionLabel(step.snapshot)}
        </span>
        <Badge
          variant={STEP_STATUS_VARIANT[step.status]}
          data-testid={`step-run-status-${step.step_index}`}
          data-status={step.status}
        >
          {step.status}
        </Badge>
        {isRunning && (
          <Loader2
            className="h-4 w-4 animate-spin text-muted-foreground"
            data-testid={`step-run-spinner-${step.step_index}`}
            aria-label="Running"
          />
        )}
        {step.exit_code != null && (
          <span
            className="font-mono text-xs text-muted-foreground"
            data-testid={`step-run-exit-${step.step_index}`}
          >
            exit {step.exit_code}
          </span>
        )}
        {step.duration_ms != null && (
          <span className="font-mono text-xs text-muted-foreground">
            {formatDuration(step.duration_ms)}
          </span>
        )}
      </div>

      {isFailed && step.error_class && (
        <div
          className="flex items-center gap-2 text-sm text-destructive"
          data-testid={`step-run-error-class-${step.step_index}`}
          data-error-class={step.error_class}
        >
          <AlertCircle className="h-4 w-4" />
          <span className="font-mono">{step.error_class}</span>
        </div>
      )}

      <details
        className="rounded border bg-muted/30 text-sm"
        data-testid={`step-run-stdout-details-${step.step_index}`}
      >
        <summary className="cursor-pointer px-3 py-2 font-medium select-none">
          stdout
        </summary>
        <pre
          className="overflow-x-auto px-3 py-2 font-mono text-xs whitespace-pre-wrap"
          data-testid={`step-run-stdout-${step.step_index}`}
        >
          {step.stdout && step.stdout.length > 0 ? (
            step.stdout
          ) : (
            <span className="italic text-muted-foreground">no output</span>
          )}
        </pre>
      </details>

      {step.stderr && step.stderr.length > 0 && (
        <details
          className="rounded border bg-destructive/5 text-sm"
          data-testid={`step-run-stderr-details-${step.step_index}`}
        >
          <summary className="cursor-pointer px-3 py-2 font-medium select-none">
            stderr
          </summary>
          <pre
            className="overflow-x-auto px-3 py-2 font-mono text-xs whitespace-pre-wrap"
            data-testid={`step-run-stderr-${step.step_index}`}
          >
            {step.stderr}
          </pre>
        </details>
      )}
    </li>
  )
}

function RunDetailContent({ runId }: { runId: string }) {
  const qc = useQueryClient()

  const cancelMutation = useMutation({
    mutationFn: () => WorkflowsService.cancelWorkflowRun({ runId }),
    onMutate: async () => {
      // Optimistically set the displayed status to 'cancelling' so the UI
      // reflects in-flight intent while the backend stamps 'cancelled'.
      await qc.cancelQueries({ queryKey: workflowRunQueryKey(runId) })
      const prev = qc.getQueryData<WorkflowRunPublic>(workflowRunQueryKey(runId))
      if (prev) {
        qc.setQueryData(workflowRunQueryKey(runId), {
          ...prev,
          status: "cancelled" as const,
        })
      }
      return { prev }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(workflowRunQueryKey(runId), ctx.prev)
      }
      toast.error("Cancel failed", {
        description: "The run could not be cancelled.",
      })
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: workflowRunQueryKey(runId) })
    },
  })

  const query = useQuery({
    ...workflowRunQueryOptions(runId),
    refetchInterval: (q) => {
      const data = q.state.data as WorkflowRunPublic | undefined
      if (!data) return 1500
      return isRunInFlight(data.status) ? 1500 : false
    },
    // Long-running runs should still refetch on remount even if cached.
    staleTime: 0,
    // Don't retry 4xx — `workflow_run_not_found` should land on the error
    // card immediately rather than burning three failed polls.
    retry: (failureCount, error) => {
      const status = (error as { status?: number } | undefined)?.status
      if (typeof status === "number" && status >= 400 && status < 500) {
        return false
      }
      return failureCount < 3
    },
  })

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-3" data-testid="run-detail-loading">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <Card
        data-testid="run-detail-error"
        className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3"
      >
        <h2 className="text-lg font-semibold">Run not found</h2>
        <p className="text-muted-foreground max-w-sm">
          This run doesn't exist or you're no longer a member of its team.
        </p>
        <Button asChild variant="outline">
          <Link to="/teams">Back to teams</Link>
        </Button>
      </Card>
    )
  }

  const run = query.data
  const steps = run.step_runs ?? []
  const inFlight = isRunInFlight(run.status)

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="run-detail"
      data-run-id={run.id}
      data-run-status={run.status}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="ghost" size="sm">
          <Link to="/teams">
            <ChevronLeft className="h-4 w-4" />
            Teams
          </Link>
        </Button>
        <h1
          className="text-2xl font-bold tracking-tight"
          data-testid="run-detail-title"
        >
          Workflow run
        </h1>
        <Badge
          data-testid="run-detail-status"
          data-status={run.status}
          variant={RUN_STATUS_VARIANT[run.status]}
        >
          {run.status}
        </Badge>
        {inFlight && (
          <Loader2
            className="h-4 w-4 animate-spin text-muted-foreground"
            data-testid="run-detail-polling"
            aria-label="Polling"
          />
        )}
        {inFlight && (
          <Button
            size="sm"
            variant="outline"
            data-testid="run-cancel-button"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
          >
            <XCircle className="h-4 w-4 text-destructive" />
            Cancel run
          </Button>
        )}
        {run.error_class && (
          <Badge
            variant="destructive"
            data-testid="run-detail-error-class"
            data-error-class={run.error_class}
          >
            {run.error_class}
          </Badge>
        )}
      </div>

      <Card className="grid gap-2 p-4 sm:grid-cols-2">
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Run id</span>
          <span className="font-mono text-xs truncate">{run.id}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Workflow id</span>
          <span className="font-mono text-xs truncate">{run.workflow_id}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Started</span>
          <span className="text-sm" data-testid="run-detail-started-at">
            {formatTimestamp(run.started_at)}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Finished</span>
          <span className="text-sm" data-testid="run-detail-finished-at">
            {formatTimestamp(run.finished_at)}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Duration</span>
          <span className="text-sm" data-testid="run-detail-duration">
            {formatDuration(run.duration_ms)}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted-foreground text-xs">Trigger</span>
          <span className="text-sm">{run.trigger_type}</span>
        </div>
      </Card>

      <section
        className="flex flex-col gap-2"
        aria-label="Run steps"
        data-testid="run-detail-steps-section"
      >
        <h2 className="text-sm font-medium">Steps</h2>
        {steps.length === 0 ? (
          <Card className="p-4 text-sm text-muted-foreground">
            This run has no steps yet.
          </Card>
        ) : (
          <ul
            className="flex flex-col divide-y rounded-lg border bg-card"
            data-testid="run-detail-steps-list"
          >
            {steps.map((step) => (
              <StepRow key={step.id} step={step} />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function RunDetailPage() {
  const { runId } = Route.useParams()
  return <RunDetailContent runId={runId} />
}
