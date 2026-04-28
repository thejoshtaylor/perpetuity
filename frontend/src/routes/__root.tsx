import type { QueryClient } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import {
  createRootRouteWithContext,
  HeadContent,
  Outlet,
} from "@tanstack/react-router"
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools"
import ErrorComponent from "@/components/Common/ErrorComponent"
import NotFound from "@/components/Common/NotFound"

export interface RouterContext {
  queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
  // M005-oaptsz/S01: TanStack Router devtools and React Query devtools render
  // floating buttons (40x40 and 150x30) that the mobile-audit gate flags as
  // sub-44px touch targets. Even in dev mode they are noise on small viewports.
  // Gate them on `?devtools=1` so dev iteration is opt-in and the audit
  // harness — which runs against the dev server — never sees them.
  component: () => {
    const showDevtools =
      import.meta.env.DEV &&
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).has("devtools")
    return (
      <>
        <HeadContent />
        <Outlet />
        {showDevtools ? (
          <>
            <TanStackRouterDevtools position="bottom-right" />
            <ReactQueryDevtools initialIsOpen={false} />
          </>
        ) : null}
      </>
    )
  },
  notFoundComponent: () => <NotFound />,
  errorComponent: () => <ErrorComponent />,
})
