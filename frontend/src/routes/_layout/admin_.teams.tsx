import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Suspense } from "react"

import { AdminService } from "@/client"
import {
  type AdminTeamRow,
  adminTeamsColumns,
} from "@/components/Admin/AdminTeamsColumns"
import { DataTable } from "@/components/Common/DataTable"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { requireSystemAdmin } from "@/lib/auth-guards"

const DEFAULT_LIMIT = 20

type AdminTeamsSearch = { skip: number; limit: number }

type AdminTeamsEnvelope = { data: AdminTeamRow[]; count: number }

function getAdminTeamsQueryOptions({ skip, limit }: AdminTeamsSearch) {
  return {
    queryKey: ["admin", "teams", { skip, limit }] as const,
    queryFn: async (): Promise<AdminTeamsEnvelope> => {
      const res = await AdminService.readAllTeams({ skip, limit })
      // Backend return type is `dict[str, Any]`, so the generated SDK types
      // the response as `{[k: string]: unknown}`. The runtime shape is the
      // documented `{ data: TeamPublic[], count: int }` envelope.
      return res as unknown as AdminTeamsEnvelope
    },
  }
}

export const Route = createFileRoute("/_layout/admin_/teams")({
  component: AdminTeams,
  beforeLoad: requireSystemAdmin,
  validateSearch: (search): AdminTeamsSearch => {
    const raw = search as Record<string, unknown>
    const skipNum = Number(raw.skip)
    const limitNum = Number(raw.limit)
    const skip =
      Number.isFinite(skipNum) && skipNum >= 0 ? Math.floor(skipNum) : 0
    const limit =
      Number.isFinite(limitNum) && limitNum > 0
        ? Math.floor(limitNum)
        : DEFAULT_LIMIT
    return { skip, limit }
  },
  head: () => ({
    meta: [
      {
        title: "All Teams - FastAPI Template",
      },
    ],
  }),
})

function PaginationControls({
  skip,
  limit,
  pageItemCount,
}: {
  skip: number
  limit: number
  pageItemCount: number
}) {
  const navigate = useNavigate({ from: Route.fullPath })
  const prevDisabled = skip <= 0
  const nextDisabled = pageItemCount < limit

  return (
    <div className="flex items-center justify-end gap-2 pt-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={prevDisabled}
        data-testid="admin-teams-prev"
        onClick={() => {
          const nextSkip = Math.max(0, skip - limit)
          navigate({ search: { skip: nextSkip, limit } })
        }}
      >
        <ChevronLeft className="h-4 w-4" />
        Prev
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={nextDisabled}
        data-testid="admin-teams-next"
        onClick={() => {
          navigate({ search: { skip: skip + limit, limit } })
        }}
      >
        Next
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  )
}

function AdminTeamsTableContent() {
  const { skip, limit } = Route.useSearch()
  const { data } = useSuspenseQuery(getAdminTeamsQueryOptions({ skip, limit }))

  if (data.data.length === 0 && skip === 0) {
    return (
      <Card className="flex flex-col items-center justify-center text-center py-12 px-6 gap-2">
        <h3 className="text-lg font-semibold">No teams in the system yet.</h3>
        <p className="text-muted-foreground max-w-sm">
          Teams created by users will appear here.
        </p>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <DataTable columns={adminTeamsColumns} data={data.data} />
      <PaginationControls
        skip={skip}
        limit={limit}
        pageItemCount={data.data.length}
      />
    </div>
  )
}

function AdminTeamsTablePending() {
  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Name</TableHead>
            <TableHead>Slug</TableHead>
            <TableHead>Personal?</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="sr-only">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {[0, 1, 2, 3, 4].map((i) => (
            <TableRow key={i}>
              <TableCell>
                <Skeleton className="h-5 w-40" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-24" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-5 w-16 rounded-full" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-20" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-20 ml-auto" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function AdminTeams() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight">All Teams</h1>
        <p className="text-muted-foreground">
          System admin: every team in the workspace.
        </p>
      </div>
      <Suspense fallback={<AdminTeamsTablePending />}>
        <AdminTeamsTableContent />
      </Suspense>
    </div>
  )
}
