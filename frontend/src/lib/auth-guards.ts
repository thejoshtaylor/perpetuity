import type { QueryClient } from "@tanstack/react-query"
import { redirect } from "@tanstack/react-router"

import { UsersService } from "@/client"

type GuardContext = {
  context: { queryClient: QueryClient }
}

export async function requireSystemAdmin({ context }: GuardContext) {
  const user = await context.queryClient.ensureQueryData({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
  })
  if (user.role !== "system_admin") {
    throw redirect({ to: "/" })
  }
}
