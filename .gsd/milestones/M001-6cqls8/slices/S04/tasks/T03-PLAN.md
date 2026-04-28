---
estimated_steps: 15
estimated_files: 7
skills_used: []
---

# T03: Create-Team modal + invite-link UI + /invite/{code} acceptance route

Wire the team-creation and invite flows. After this task the inviter can create a team, generate an invite URL, and the invitee can accept it from the browser. Personal teams must hide both Invite and (where applicable) Create-shaped controls; T03 must respect the backend's structural rules (D003) without re-implementing them.

**What changes:**

1. **Create Team modal in `src/components/Teams/CreateTeamDialog.tsx`** using `@radix-ui/react-dialog` (already installed) wrapped via `src/components/ui/dialog.tsx`. Form: single `name` field (zod min(1).max(255), trim). Submit calls `TeamsService.createTeam({ requestBody: { name } })`. On success: invalidate `['teams']` query, close dialog, toast 'Team created'. On 4xx: show `error.body.detail` inline. Wire the existing `data-testid="create-team-button"` (T02 stub) to open the dialog.

2. **Invite UI in `src/components/Teams/InviteButton.tsx`** — Button visible only on non-personal teams to admins (the backend will 403 otherwise; we hide for clarity). Click calls `TeamsService.inviteToTeam({ teamId })`. On success, the response `{ code, url, expires_at }` is rendered in a small panel: `url` in a read-only `Input` + a Copy button using the existing `useCopyToClipboard` hook. Toast 'Copied' on copy success. The panel shows `expires in 7 days` (computed from `expires_at`). Never call `console.log(invite.code)` or `console.log(invite.url)` (mirrors backend MEM028 and the slice's redaction constraint).

3. **Wire into team detail route `src/routes/_layout/teams.$teamId.tsx`** (T02 stubbed it). Fetch the team via `TeamsService.readTeams()` then `find(t => t.id === teamId)` (no per-team GET endpoint exists — keep the cache lookup simple). If not found / no membership: 404 component. Render team name, role badge, and `<InviteButton />` when role==='admin' and !is_personal.

4. **/invite/{code} acceptance route — `src/routes/invite.$code.tsx`** (top-level, NOT under `_layout`). `beforeLoad`: `try { await queryClient.ensureQueryData({ queryKey: ['currentUser'], ... }) } catch { throw redirect({ to: '/login', search: { next: location.href } }) }`. Component: on mount, runs `TeamsService.joinTeam({ code })` via a mutation. On success: invalidate `['teams']`, toast 'Joined <team.name>', redirect to `/teams/${team.id}`. On 404 → 'Invite not found' card with link back to `/teams`. On 410 → 'This invite has expired or already been used'. On 409 → 'You are already a member' + redirect to that team after 2s. Display loading spinner while the mutation runs.

5. **Login redirect handling.** `src/routes/login.tsx` `beforeLoad` already redirects logged-in users to `/`; extend the `loginMutation.onSuccess` in `useAuth` to honor `?next=` from the URL: `const next = new URLSearchParams(location.search).get('next') || '/'; navigate({ to: next })`. Same for signup. Sanitize `next` to start with `/` to prevent open-redirect.

**Threat surface (Q3):** Open-redirect via `?next=`: only honor relative paths matching `^/[^/]`. Invite codes never logged. Copy-to-clipboard uses `navigator.clipboard.writeText` which requires HTTPS or localhost — Vite dev meets this, document as known limitation if deployed to non-https origin.

**Failure modes (Q5):**
- POST /teams returns 409 (slug conflict) → toast 'Name conflict, try another' and keep modal open.
- POST /teams/{id}/invite returns 403 (caller not admin) → toast 'Only team admins can invite' and remove the button (defensive — the UI shouldn't surface it but stale React Query data could).
- POST /teams/join/{code} returns 404/410/409 → distinct toasts per status, never crash.
- Clipboard API unavailable → fallback to `document.execCommand('copy')` on a hidden textarea OR show the URL in a manually-selectable field with text 'Copy manually'.

**Negative tests (Q7):** invalid invite code path — see T05.

**Skill activation note:** caveman skill not available; skills_used: [].

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/routes/_layout/teams.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/components/ui/dialog.tsx`
- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/button.tsx`
- `frontend/src/components/ui/form.tsx`
- `frontend/src/hooks/useCopyToClipboard.ts`
- `frontend/src/hooks/useAuth.ts`

## Expected Output

- `frontend/src/components/Teams/CreateTeamDialog.tsx`
- `frontend/src/components/Teams/InviteButton.tsx`
- `frontend/src/routes/_layout/teams.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/routes/invite.$code.tsx`
- `frontend/src/hooks/useAuth.ts`
- `frontend/src/routeTree.gen.ts`

## Verification

cd frontend && bun run lint && bun run build && rg -n 'data-testid="invite-button"|data-testid="copy-invite-url"|data-testid="create-team-submit"' src/components/Teams/ src/routes/ ; test $? -eq 0

## Observability Impact

Toasts surface backend `detail` for every mutation result. No invite codes logged. React Query cache keys: ['teams'] for the list, ['team', teamId] for a single team derived from the list cache.
