import type { ColumnDef } from "@tanstack/react-table"

import { Badge } from "@/components/ui/badge"

export type AdminTeamRow = {
  id: string
  name: string
  slug: string
  is_personal: boolean
  created_at?: string | null
}

function formatCreatedAt(value: string | null | undefined): string {
  if (!value) return "—"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

export const adminTeamsColumns: ColumnDef<AdminTeamRow>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <span
        className="font-medium"
        data-testid="admin-teams-row"
        data-team-id={row.original.id}
        title={row.original.name}
      >
        {row.original.name}
      </span>
    ),
  },
  {
    accessorKey: "slug",
    header: "Slug",
    cell: ({ row }) => (
      <span className="text-muted-foreground font-mono text-xs">
        {row.original.slug}
      </span>
    ),
  },
  {
    accessorKey: "is_personal",
    header: "Personal?",
    cell: ({ row }) =>
      row.original.is_personal ? (
        <Badge variant="outline">Personal</Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {formatCreatedAt(row.original.created_at)}
      </span>
    ),
  },
  {
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <a
          href={`/admin/teams/${row.original.id}`}
          data-testid="view-members-link"
          data-team-id={row.original.id}
          className="text-sm text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          View members
        </a>
      </div>
    ),
  },
]
