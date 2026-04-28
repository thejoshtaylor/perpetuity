import { createFileRoute } from "@tanstack/react-router"
import { Suspense } from "react"

import {
  listSystemSettingsQueryOptions,
  SystemSettingsList,
} from "@/components/Admin/SystemSettings/SystemSettingsList"
import { Skeleton } from "@/components/ui/skeleton"
import { requireSystemAdmin } from "@/lib/auth-guards"

export const Route = createFileRoute("/_layout/admin/settings")({
  component: SystemSettingsPage,
  beforeLoad: requireSystemAdmin,
  loader: ({ context }) =>
    context.queryClient.ensureQueryData(listSystemSettingsQueryOptions()),
  head: () => ({
    meta: [{ title: "System Settings - FastAPI Template" }],
  }),
})

function SystemSettingsPending() {
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border p-3"
      data-testid="system-settings-loading"
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={`system-settings-skeleton-${i + 1}`}
          className="flex items-center gap-3"
        >
          <Skeleton className="h-4 w-4 rounded" />
          <Skeleton className="h-4 w-64" />
        </div>
      ))}
    </div>
  )
}

function SystemSettingsPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="system-settings-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">System Settings</h1>
        <p className="text-muted-foreground">
          Configure global, system-wide settings. Sensitive values are encrypted
          at rest and never returned in plaintext after save.
        </p>
      </div>
      <Suspense fallback={<SystemSettingsPending />}>
        <SystemSettingsList />
      </Suspense>
    </div>
  )
}
