import { createFileRoute, Outlet, redirect } from "@tanstack/react-router"

import { UsersService } from "@/client"
import { Footer } from "@/components/Common/Footer"
import { InstallBanner } from "@/components/Common/InstallBanner"
import { OfflineBanner } from "@/components/Common/OfflineBanner"
import { NotificationBell } from "@/components/notifications/NotificationBell"
import { PushPermissionPrompt } from "@/components/notifications/PushPermissionPrompt"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData({
        queryKey: ["currentUser"],
        queryFn: UsersService.readUserMe,
      })
    } catch {
      throw redirect({
        to: "/login",
        search: { next: location.pathname },
      })
    }
  },
})

function Layout() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <OfflineBanner />
        <InstallBanner />
        <PushPermissionPrompt />
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
          <div className="ml-auto flex items-center gap-2">
            <NotificationBell />
          </div>
        </header>
        <main className="flex-1 p-6 md:p-8">
          <div className="mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
        <Footer />
      </SidebarInset>
    </SidebarProvider>
  )
}

export default Layout
