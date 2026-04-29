// M005-sqm8et/S05/T04 — Team run history list page (/runs)
// Fetches GET /api/v1/teams/{teamId}/runs (added in S05/T01).
// Filters round-trip via URL search params.

import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { History } from "lucide-react"
import { useState } from "react"
import { z } from "zod"

import { OpenAPI } from "@/client/core/OpenAPI"
import { request } from "@/client/core/request"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"

// ── Types ─────────────────────────────────────────────────────────────────────

type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "rejected"

type RunTriggerType =
  | "button"
  | "webhook"
  | "schedule"
  | "manual"
  | "admin_manual"

interface RunSummary {
  id: string
  workflow_id: string
  team_id: string
  trigger_type: RunTriggerType
  triggered_by_user_id: string | null
  status: RunStatus
  error_class: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string | null
}

interface RunsEnvelope {
  data: RunSummary[]
  count: number
}

// ── Filter schema (URL search params) ─────────────────────────────────────────

const ALL_STATUSES: RunStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "rejected",
]

const ALL_TRIGGER_TYPES: RunTriggerType[] = [
  "button",
  "webhook",
  "schedule",
  "manual",
  "admin_manual",
]

const searchSchema = z.object({
  teamId: z.string().catch(""),
  status: z.string().optional().catch(undefined),
  trigger_type: z.string().optional().catch(undefined),
  after: z.string().optional().catch(undefined),
  before: z.string().optional().catch(undefined),
  offset: z.number().catch(0),
})

type RunsSearch = z.infer<typeof searchSchema>

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/_layout/runs")({
  validateSearch: (s) => searchSchema.parse(s),
  component: RunsListPage,
  head: () => ({
    meta: [{ title: "Run history" }],
  }),
})

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_VARIANT: Record<
  RunStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  cancelled: "outline",
  rejected: "outline",
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—"
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatRelative(value: string | null | undefined): string {
  if (!value) return "—"
  const diffMs = Date.now() - new Date(value).getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return "just now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

function truncateId(id: string): string {
  return id.slice(0, 8) + "…"
}

// ── API call (SDK not yet regenerated to include listTeamRuns) ────────────────

function teamRunsQueryKey(
  teamId: string,
  filters: Omit<RunsSearch, "teamId">,
) {
  return ["team", teamId, "runs", filters] as const
}

function teamRunsQueryFn(teamId: string, params: Record<string, string>) {
  return request<RunsEnvelope>(OpenAPI, {
    method: "GET",
    url: "/api/v1/teams/{team_id}/runs",
    path: { team_id: teamId },
    query: params,
    errors: { 403: "Forbidden", 404: "Not Found", 422: "Validation Error" },
  }) as Promise<RunsEnvelope>
}

// ── Multi-select toggle chip ───────────────────────────────────────────────────

function ToggleChip({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-full border px-3 py-1 text-xs font-medium transition-colors " +
        (active
          ? "border-foreground bg-foreground text-background"
          : "border-border bg-background text-muted-foreground hover:border-foreground/50")
      }
    >
      {label}
    </button>
  )
}

// ── Run row ───────────────────────────────────────────────────────────────────

function RunRow({ run }: { run: RunSummary }) {
  return (
    <li
      className="flex flex-wrap items-center gap-3 p-4 hover:bg-muted/30 transition-colors"
      data-testid={`run-row-${run.id}`}
      data-run-id={run.id}
      data-run-status={run.status}
    >
      {/* Run id (truncated, links to detail) */}
      <Link
        to="/runs/$runId"
        params={{ runId: run.id }}
        className="font-mono text-xs text-primary hover:underline shrink-0"
        data-testid={`run-link-${run.id}`}
      >
        {truncateId(run.id)}
      </Link>

      {/* Workflow id (no live FK — snapshot semantics) */}
      <span
        className="font-mono text-xs text-muted-foreground shrink-0"
        title={run.workflow_id}
        data-testid={`run-workflow-id-${run.id}`}
      >
        wf:{truncateId(run.workflow_id)}
      </span>

      {/* Trigger type */}
      <Badge
        variant="outline"
        className="shrink-0"
        data-testid={`run-trigger-${run.id}`}
      >
        {run.trigger_type}
      </Badge>

      {/* Status */}
      <Badge
        variant={STATUS_VARIANT[run.status]}
        className="shrink-0"
        data-testid={`run-status-${run.id}`}
        data-status={run.status}
      >
        {run.status}
      </Badge>

      {/* Error class if present */}
      {run.error_class && (
        <span
          className="font-mono text-xs text-destructive shrink-0"
          data-testid={`run-error-class-${run.id}`}
        >
          {run.error_class}
        </span>
      )}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Duration */}
      <span
        className="font-mono text-xs text-muted-foreground shrink-0"
        data-testid={`run-duration-${run.id}`}
      >
        {formatDuration(run.duration_ms)}
      </span>

      {/* Created at relative */}
      <span
        className="text-xs text-muted-foreground shrink-0"
        data-testid={`run-created-at-${run.id}`}
        title={run.created_at ?? ""}
      >
        {formatRelative(run.created_at)}
      </span>
    </li>
  )
}

// ── Filter panel ──────────────────────────────────────────────────────────────

function FilterPanel({
  search,
  onUpdate,
}: {
  search: RunsSearch
  onUpdate: (patch: Partial<RunsSearch>) => void
}) {
  const selectedStatuses = search.status ? search.status.split(",") : []
  const selectedTriggers = search.trigger_type
    ? search.trigger_type.split(",")
    : []

  function toggleStatus(s: string) {
    const next = selectedStatuses.includes(s)
      ? selectedStatuses.filter((x) => x !== s)
      : [...selectedStatuses, s]
    onUpdate({ status: next.length ? next.join(",") : undefined, offset: 0 })
  }

  function toggleTrigger(t: string) {
    const next = selectedTriggers.includes(t)
      ? selectedTriggers.filter((x) => x !== t)
      : [...selectedTriggers, t]
    onUpdate({
      trigger_type: next.length ? next.join(",") : undefined,
      offset: 0,
    })
  }

  return (
    <div
      className="flex flex-col gap-3 rounded-lg border bg-card p-4"
      data-testid="runs-filter-panel"
    >
      {/* Status filter */}
      <div className="flex flex-col gap-2">
        <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Status
        </Label>
        <div className="flex flex-wrap gap-2">
          {ALL_STATUSES.map((s) => (
            <ToggleChip
              key={s}
              label={s}
              active={selectedStatuses.includes(s)}
              onClick={() => toggleStatus(s)}
            />
          ))}
        </div>
      </div>

      {/* Trigger type filter */}
      <div className="flex flex-col gap-2">
        <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Trigger
        </Label>
        <div className="flex flex-wrap gap-2">
          {ALL_TRIGGER_TYPES.map((t) => (
            <ToggleChip
              key={t}
              label={t}
              active={selectedTriggers.includes(t)}
              onClick={() => toggleTrigger(t)}
            />
          ))}
        </div>
      </div>

      {/* Date range */}
      <div className="flex flex-wrap gap-4">
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="runs-after"
            className="text-xs font-medium text-muted-foreground"
          >
            After
          </Label>
          <Input
            id="runs-after"
            type="datetime-local"
            className="h-8 text-xs w-48"
            value={search.after ?? ""}
            onChange={(e) =>
              onUpdate({ after: e.target.value || undefined, offset: 0 })
            }
            data-testid="runs-filter-after"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="runs-before"
            className="text-xs font-medium text-muted-foreground"
          >
            Before
          </Label>
          <Input
            id="runs-before"
            type="datetime-local"
            className="h-8 text-xs w-48"
            value={search.before ?? ""}
            onChange={(e) =>
              onUpdate({ before: e.target.value || undefined, offset: 0 })
            }
            data-testid="runs-filter-before"
          />
        </div>
      </div>
    </div>
  )
}

// ── Page content ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 50

function RunsListContent() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const { teamId, status, trigger_type, after, before, offset } = search

  function updateSearch(patch: Partial<RunsSearch>) {
    navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
  }

  // Build query params — only include defined values
  const queryParams: Record<string, string> = {
    limit: String(PAGE_SIZE),
    offset: String(offset ?? 0),
  }
  if (status) queryParams.status = status
  if (trigger_type) queryParams.trigger_type = trigger_type
  if (after) queryParams.after = after
  if (before) queryParams.before = before

  const [showFilters, setShowFilters] = useState(false)

  const query = useQuery({
    queryKey: teamRunsQueryKey(teamId, { status, trigger_type, after, before, offset }),
    queryFn: () => teamRunsQueryFn(teamId, queryParams),
    enabled: Boolean(teamId),
    staleTime: 15_000,
  })

  const runs = query.data?.data ?? []
  const total = query.data?.count ?? 0
  const currentOffset = offset ?? 0
  const hasMore = currentOffset + PAGE_SIZE < total

  return (
    <div className="flex flex-col gap-4" data-testid="runs-list-page">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Run history</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowFilters((v) => !v)}
          data-testid="runs-toggle-filters"
        >
          {showFilters ? "Hide filters" : "Filters"}
        </Button>
      </div>

      {/* Filter panel — shown when toggled or when any filter is active */}
      {(showFilters || status || trigger_type || after || before) && (
        <FilterPanel search={search} onUpdate={updateSearch} />
      )}

      {/* Results count */}
      {!query.isLoading && (
        <p className="text-sm text-muted-foreground" data-testid="runs-count">
          {total} run{total !== 1 ? "s" : ""}
          {currentOffset > 0 && ` — showing ${currentOffset + 1}–${Math.min(currentOffset + PAGE_SIZE, total)}`}
        </p>
      )}

      {/* Loading skeletons */}
      {query.isLoading && (
        <div className="flex flex-col gap-2" data-testid="runs-loading">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      )}

      {/* Error */}
      {query.isError && (
        <Card
          className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3"
          data-testid="runs-error"
        >
          <h2 className="text-lg font-semibold">Failed to load runs</h2>
          <p className="text-muted-foreground text-sm max-w-sm">
            Check that you are a member of the selected team.
          </p>
        </Card>
      )}

      {/* Empty state */}
      {!query.isLoading && !query.isError && runs.length === 0 && (
        <Card
          className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3"
          data-testid="runs-empty"
        >
          <History className="h-8 w-8 text-muted-foreground" />
          <h2 className="text-base font-semibold">No runs yet</h2>
          <p className="text-muted-foreground text-sm max-w-sm">
            Runs appear here once a workflow has been triggered.
          </p>
        </Card>
      )}

      {/* Run list */}
      {runs.length > 0 && (
        <ul
          className="flex flex-col divide-y rounded-lg border bg-card"
          data-testid="runs-list"
        >
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </ul>
      )}

      {/* Pagination */}
      <div className="flex gap-2">
        {currentOffset > 0 && (
          <Button
            variant="outline"
            size="sm"
            data-testid="runs-load-prev"
            onClick={() =>
              updateSearch({ offset: Math.max(0, currentOffset - PAGE_SIZE) })
            }
          >
            ← Previous
          </Button>
        )}
        {hasMore && (
          <Button
            variant="outline"
            size="sm"
            data-testid="runs-load-more"
            onClick={() => updateSearch({ offset: currentOffset + PAGE_SIZE })}
          >
            Load more →
          </Button>
        )}
      </div>
    </div>
  )
}

function RunsListPage() {
  return <RunsListContent />
}
