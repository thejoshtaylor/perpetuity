---
id: T03
parent: S04
milestone: M001-6cqls8
key_files:
  - frontend/src/components/Teams/CreateTeamDialog.tsx
  - frontend/src/components/Teams/InviteButton.tsx
  - frontend/src/routes/_layout/teams.tsx
  - frontend/src/routes/_layout/teams.$teamId.tsx
  - frontend/src/routes/invite.$code.tsx
  - frontend/src/hooks/useAuth.ts
  - frontend/src/routeTree.gen.ts
key_decisions:
  - sanitizeNextPath uses /^\/[^/\\]/ — rejects protocol-relative (//evil.com), backslash variants, and absolute URLs. Exported from useAuth so future call sites (e.g. logout-with-next) can reuse the same gate.
  - InviteButton self-hides on 403 in addition to the role-gate at the parent level. Defense-in-depth against stale React Query cache showing the button to a demoted member.
  - /invite/$code is a TOP-LEVEL route (not under _layout) so the loading + error states render through AuthLayout and don't depend on the sidebar layout. Matches the existing /login,/signup placement.
  - On 409 (already-member) the backend detail body doesn't carry the team id, so we cannot navigate directly to that team. Toast + 2s redirect to /teams instead — accepted minor UX limitation, not a blocker.
  - useEffect+ref pattern on /invite/$code prevents StrictMode double-invoke firing joinTeam twice (which would cause a spurious 410-already-consumed on the second call).
  - Clipboard fallback uses document.execCommand('copy') on a hidden textarea so non-HTTPS dev/preview origins still work even though navigator.clipboard requires a secure context.
duration: 
verification_result: passed
completed_at: 2026-04-25T00:17:56.457Z
blocker_discovered: false
---

# T03: Wired Create-Team modal, invite-link UI with copy-to-clipboard, and /invite/{code} acceptance route — including sanitized ?next= login/signup redirect for invite-link bounces.

**Wired Create-Team modal, invite-link UI with copy-to-clipboard, and /invite/{code} acceptance route — including sanitized ?next= login/signup redirect for invite-link bounces.**

## What Happened

Built the team-creation and invite acceptance flow on top of the dashboard from T02.

`CreateTeamDialog` (new, `src/components/Teams/CreateTeamDialog.tsx`) wraps the existing shadcn `Dialog` primitive with a single trimmed/zod-validated `name` field (1–255 chars). On submit it calls `TeamsService.createTeam`, invalidates `['teams']`, toasts "Team created", and resets+closes. 4xx errors flow through the shared `handleError` util which surfaces `body.detail` via the existing error toast — so the backend's friendly messages (e.g. 409 slug-conflict text) appear inline. The component takes an optional `trigger` prop so the empty-state and the dashboard header can both open the same dialog without duplicating its state. The required `data-testid="create-team-button"` lives on the default trigger and `create-team-submit` on the LoadingButton.

`InviteButton` (new, `src/components/Teams/InviteButton.tsx`) is admin-only on non-personal teams. The team detail view enforces the gate (`role==='admin' && !is_personal`) before rendering it; the button itself defensively self-hides on a 403 so a stale React Query cache cannot leak it. On click it calls `TeamsService.inviteToTeam`, then renders `{ url, expires_at }` in a panel: read-only `Input` (auto-selects on focus), Copy `Button` with `useCopyToClipboard` hook + `document.execCommand('copy')` fallback for non-HTTPS origins, and a "Generate a new link" action. `expires_at` is formatted relative to now (e.g. "expires in 7 days"). Per the slice's redaction constraint and backend MEM028, **no console.log of the invite code or url** anywhere in this code path.

`teams.$teamId.tsx` was rebuilt from the T02 stub. It uses the same `['teams']` cache + `find(t => t.id === teamId)` lookup the slice plan prescribes (no per-team GET endpoint), 404s with a "Team not found" card if missing, and renders name, role badge, personal badge, and the InviteButton when admin && !is_personal. Members list is left as a "lands in T04" note as planned.

`/invite/$code` is a new top-level route at `src/routes/invite.$code.tsx` (NOT under `_layout` — it has its own auth-prompt UI). `beforeLoad` does `ensureQueryData(['currentUser'])` and on failure throws `redirect({ to: '/login', search: { next: location.href } })`. The component runs `joinTeam({ code })` exactly once on mount (guarded by a ref to defeat StrictMode double-invoke). On success it invalidates `['teams']`, toasts `Joined <team.name>`, and navigates to `/teams/$teamId`. 404 → "Invite not found" card. 410 → "Invite expired" card (the slice's "expired or already been used" copy). 409 → "Already a member" toast + 2s redirect to /teams (backend's 409 detail body doesn't carry the team id, so we cannot bounce directly to that team — documented as a known minor limitation). Other errors fall back to `body.detail`. Loading and error UIs reuse the existing `AuthLayout` so a logged-in user opening an invite still sees the standard chrome.

`useAuth` was extended to honor `?next=` on both login and signup success. New exported `sanitizeNextPath(raw)` accepts only paths matching `^/[^/\\]` — rejecting protocol-relative `//evil.com`, backslash variants `/\evil`, and absolute URLs to defeat open-redirect. Both `loginMutation.onSuccess` and `signUpMutation.onSuccess` now read `?next=` from `window.location.search`, sanitize it, and `navigate({ to: next })` instead of always going to `/`. The `_layout/index.tsx` `/` → `/teams` redirect from T02 still applies for the no-`next` default path.

`routeTree.gen.ts` was regenerated by the TanStack Router Vite plugin during `bun run build` — it now includes `InviteCodeRoute` at the root level with the `code` param wired through `FileRoutesByPath`.

Pre-existing T02 mitigation: T02's summary noted that `bun run build` from the **repo root** has no script (root package.json only has dev/lint/test/test:ui). The verification gate that flagged this run as failed appears to have invoked `bun run build` from the root again. The task plan's authoritative verification command is `cd frontend && bun run lint && bun run build && rg -n ...` and that passes cleanly here — the gate failure is an invocation-path artifact, not a real build break.

## Verification

Ran the slice-plan verification command from `frontend/`: `bun run lint && bun run build && rg -n 'data-testid="invite-button"|data-testid="copy-invite-url"|data-testid="create-team-submit"' src/components/Teams/ src/routes/`. Biome: 75 files checked, no fixes (after one auto-fix on first pass). TypeScript + Vite build: 2243 modules transformed, no type errors, route chunks emitted including `invite._code-Bb9FWZcD.js` confirming the new top-level route is registered. Ripgrep found all three required testids. Final exit code 0.

Did NOT run Playwright E2E in this environment (backend not running for execute-task; T05 is the integration testing pass for this slice).

The earlier verification gate failure (`error: Script not found "build"`) was the same root-dir invocation issue T02 documented — the repo-root `package.json` has no `build` script. The slice-plan-authoritative verification command runs in `frontend/` and passes.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run lint` | 0 | pass | 1100ms |
| 2 | `cd frontend && bun run build` | 0 | pass | 4500ms |
| 3 | `cd frontend && rg -n 'data-testid="invite-button"|data-testid="copy-invite-url"|data-testid="create-team-submit"' src/components/Teams/ src/routes/` | 0 | pass | 50ms |

## Deviations

Added a Create-Team button to the dashboard header (in addition to the empty-state button). The task plan specified wiring the existing empty-state button; users with at least one team also need an entry point, so I added one rather than forcing the empty-state to be the only path. Used the same CreateTeamDialog component so behavior is identical.

The /invite/$code route uses useEffect+ref instead of a plain effect to avoid React 19 StrictMode double-fire calling joinTeam twice. Not specified by the plan but necessary in StrictMode dev — without it, the second invocation hits a backend 410 because the first already consumed the code.

## Known Issues

409 (already-member) takes the user to /teams after 2s rather than directly to the team in question — backend's 409 detail body doesn't carry team id. T05 could surface that detail, or a future backend tweak could include it. Non-blocking.

Did not run Playwright E2E here (backend not running). T05 is the slice's E2E-integration task and is the right place to exercise the create→invite→join loop end-to-end. Build still emits the preexisting "some chunks > 500 kB" warning — not introduced by this task.

Build/lint always require working directory = frontend/. The verification gate failure on the previous attempt (`error: Script not found "build"`) reflects a gate-invocation issue T02 already documented, not a real build break — the slice-plan verification command (`cd frontend && bun run ...`) passes.

## Files Created/Modified

- `frontend/src/components/Teams/CreateTeamDialog.tsx`
- `frontend/src/components/Teams/InviteButton.tsx`
- `frontend/src/routes/_layout/teams.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/routes/invite.$code.tsx`
- `frontend/src/hooks/useAuth.ts`
- `frontend/src/routeTree.gen.ts`
