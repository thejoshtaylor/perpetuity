import { registerSW } from "virtual:pwa-register"
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { ApiError, OpenAPI } from "./client"
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import { routeTree } from "./routeTree.gen"

OpenAPI.BASE = import.meta.env.VITE_API_URL
// Auth lives in an httpOnly session cookie (D001 / MEM001 / MEM023). The browser
// attaches it automatically once WITH_CREDENTIALS is set; we never read it from JS.
OpenAPI.WITH_CREDENTIALS = true

// Auth-public routes where a 401 on probe queries (e.g. ['currentUser']) is
// the *expected* state and should not bounce the user back to /login — we are
// already there.
const PUBLIC_ROUTES = new Set([
  "/login",
  "/signup",
  "/recover-password",
  "/reset-password",
])

const handleApiError = (error: Error) => {
  if (!(error instanceof ApiError)) return
  if (error.status !== 401) return
  if (PUBLIC_ROUTES.has(window.location.pathname)) return
  // Cookies are httpOnly — there is no client-side token to clear. The
  // backend's Set-Cookie on logout / expiry is the source of truth, so the
  // redirect is the only action we take here.
  console.warn(`auth_redirect reason=${error.status}`)
  window.location.href = "/login"
}
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleApiError,
  }),
  mutationCache: new MutationCache({
    onError: handleApiError,
  }),
})

// PWA service-worker registration (M005-oaptsz/S01/T01). The SW handles its
// own /api/* + /ws/* bypass; here we surface lifecycle signals so a future
// agent debugging install/refresh issues can read them from DevTools console
// and so T03's install banner can listen for `pwa-update-available`.
const updateSW = registerSW({
  onRegisteredSW(scriptUrl) {
    console.info(`pwa.sw.registered script=${scriptUrl}`)
  },
  onRegisterError(error) {
    const reason = error instanceof Error ? error.message : String(error)
    console.info(`pwa.sw.register_failed reason=${reason}`)
  },
  onNeedRefresh() {
    console.info("pwa.sw.update_available")
    window.dispatchEvent(
      new CustomEvent("pwa-update-available", {
        detail: { acceptUpdate: () => updateSW(true) },
      }),
    )
  },
  onOfflineReady() {
    console.info("pwa.sw.offline_ready")
  },
})

const router = createRouter({ routeTree, context: { queryClient } })
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="dark" storageKey="vite-ui-theme">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
