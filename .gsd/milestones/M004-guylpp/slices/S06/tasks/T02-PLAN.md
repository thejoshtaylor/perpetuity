---
estimated_steps: 6
estimated_files: 6
skills_used: []
---

# T02: Admin SystemSettings UI: list, set/replace sensitive value, generate-secret modal with one-time display

Build the system_admin-only `/admin/settings` route. The page lists every key returned by GET /api/v1/admin/settings with: key name, sensitive lock icon (when sensitive), `has_value` badge (Set / Empty), and per-key actions. Non-sensitive keys: inline edit with PUT. Sensitive keys without a generator (github_app_private_key): Set / Replace dialog that accepts a multiline textarea (PEM input) and calls PUT — response value is always null per S01 redaction; success toast + cache invalidate. Sensitive keys with a generator (github_app_webhook_secret): Set / Replace dialog (operator-supplied), Generate button that opens a confirm modal warning `Re-generating breaks any existing GitHub webhook deliveries until you update the upstream secret on github.com — proceed?` (MEM232/D025). On confirm: POST /admin/settings/{key}/generate, then open a separate one-time-display modal showing the plaintext value with a Copy button and an explicit `This value will not be shown again. Save it now.` warning + `I've saved it` close button. Closing invalidates cache; the next list render shows has_value:true value:null.

Use `data-testid` on every actionable element: `system-settings-row-{key}`, `system-settings-set-button-{key}`, `system-settings-generate-button-{key}`, `system-settings-generate-confirm`, `system-settings-one-time-value`, `system-settings-one-time-acknowledge`, `system-settings-one-time-copy`. T05 binds against these.

Route: file `frontend/src/routes/_layout/admin.settings.tsx` gated by `requireSystemAdmin` from T01. Add a sidebar entry `System Settings` (icon: lucide `Settings`) for system_admin users in `AppSidebar.tsx`.

**Failure modes (Q5):** GET /admin/settings returns 401 if session expired — fall through to the existing public-route allowlist redirect (no new handler). Generate POST returns 422 `no_generator_for_key` for github_app_private_key (it has no generator); the GenerateConfirmDialog must NOT render the Generate button for sensitive keys without a generator. Generate POST returns 503 `system_settings_decrypt_failed` only when the underlying Fernet key is corrupt — toast the response body verbatim so the operator sees the key-name discriminator. PUT 422 on bad PEM (validator rejects) — surface the backend's `reason` field in the toast.

**Negative tests (Q7):** Operator clicks Set on github_app_private_key with empty textarea → submit blocked with inline `PEM cannot be empty`. Operator clicks Generate on github_app_webhook_secret, dismisses confirm modal → no POST fires. Operator opens the one-time-display modal, copies the value, clicks acknowledge → modal unmounts, the React component carrying the value is gone (verified by Playwright in T05 by asserting `body.innerText` no longer contains the plaintext substring).

**Load profile (Q6):** N/A — admin-only surface, single-digit-keys list, no pagination needed.

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/lib/auth-guards.ts`
- `frontend/src/routes/_layout/admin.tsx`
- `frontend/src/components/ui/dialog.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/components/Admin/AddUser.tsx`

## Expected Output

- `frontend/src/routes/_layout/admin.settings.tsx`
- `frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx`
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx`
- `frontend/src/components/Admin/SystemSettings/GenerateConfirmDialog.tsx`
- `frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`

## Verification

1) `cd frontend && bun run build` exits 0. 2) `cd frontend && bun run lint` exits 0. 3) Manual smoke against live compose stack: `docker compose up -d backend orchestrator db redis`, `cd frontend && VITE_API_URL=http://localhost:8001 bun run dev`, log in as superuser, navigate to /admin/settings, assert each of the four GitHub App keys is rendered with the expected lock icon and has_value badge, paste a synthetic PEM into github_app_private_key, assert toast + has_value flips to true, click Generate on github_app_webhook_secret, assert confirm modal copy contains the upstream-rotation warning, confirm, assert one-time-value modal renders the plaintext exactly once, click acknowledge, assert subsequent list render shows has_value:true with no plaintext anywhere. (T05 will codify this as a Playwright spec.) 4) `grep 'system-settings-one-time-value' frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` matches. 5) `grep 'noopener' frontend/src/components/Admin/SystemSettings/` returns no false-positives (the install-CTA lives in T03, not here). 6) `grep -E 'console\.log|localStorage' frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` returns NOTHING — invariant for one-shot plaintext discipline.

## Observability Impact

Generate-confirm modal carries upstream-rotation warning matching D025's destructive-by-design contract. One-time-display modal is the FE mirror of S01's one-shot plaintext discipline (plaintext never persists anywhere — no React state outliving the modal close; no console.log of the value; no localStorage). Toast on PUT/Generate success/failure surfaces the backend's response body to the operator including `system_settings_decrypt_failed key=<name>` 503 shape if Fernet decrypt regresses.
