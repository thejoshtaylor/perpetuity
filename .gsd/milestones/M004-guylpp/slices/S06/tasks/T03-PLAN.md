---
estimated_steps: 6
estimated_files: 4
skills_used: []
---

# T03: Team-admin GitHub connections list + install CTA + mirror always-on toggle

Extend the existing team-detail route `frontend/src/routes/_layout/teams_.$teamId.tsx` with two new sections, both team-admin-gated by checking `team.role === 'admin'` (consistent with how the existing invite + member-controls sections gate today).

**Connections section** (`ConnectionsList.tsx`): On mount, GET /api/v1/teams/{teamId}/github/installations and render rows of (account_login, account_type, created_at). An `Install GitHub App` CTA calls GET /api/v1/teams/{teamId}/github/install-url and opens the returned `install_url` in a new tab via `window.open(url, '_blank', 'noopener,noreferrer')` (T05's spec will intercept this with a sibling helper). An empty state is rendered when no installations exist. Each row has a destructive Uninstall action (DELETE) wrapped in a confirm dialog reusing the same `Dialog` primitive used elsewhere; success invalidates `['team', teamId, 'github', 'installations']`. If GET /install-url returns 404 `github_app_not_configured`, the CTA renders disabled with tooltip `System admin must seed GitHub App credentials before installing` (operator-debuggable without DevTools).

**Mirror always-on toggle** (`AlwaysOnToggle.tsx`): A single `Switch` (T01's primitive) wired to PATCH /api/v1/teams/{teamId}/mirror — initial state from the team object's `mirror.always_on` if present, default false otherwise (the row may not exist yet on first load — PATCH auto-creates). Optimistic update on toggle with rollback on error, success toast `Mirror always-on enabled` / `disabled`. Suppress rendering when team.is_personal is true (no shared mirror semantics on personal teams).

`data-testid` keys T05 will bind: `connections-section`, `install-github-cta`, `installation-row-<installation_id>`, `installation-uninstall-<installation_id>`, `installation-uninstall-confirm`, `mirror-section`, `mirror-always-on-toggle`.

**Failure modes (Q5):** install-url 404 (not configured) → disabled CTA. install-url 403 (not admin) → CTA hidden (consistent with admin-gated rendering). DELETE 404 (already removed) → silent cache invalidate (race-tolerant). PATCH /mirror 503 orchestrator-unavailable → toast + rollback (the backend handler does not call the orchestrator on this path, but include for safety).

**Negative tests (Q7):** Personal team navigation must not render mirror-section. Non-admin team-member navigation must not render install-CTA, must not render uninstall actions on rows. Uninstall confirm dialog cancel button does NOT fire DELETE.

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
- `frontend/src/components/Teams/InviteButton.tsx`
- `frontend/src/components/ui/switch.tsx`
- `frontend/src/components/ui/dialog.tsx`
- `.gsd/milestones/M004-guylpp/slices/S02/S02-SUMMARY.md`

## Expected Output

- `frontend/src/components/Teams/GitHub/ConnectionsList.tsx`
- `frontend/src/components/Teams/GitHub/UninstallConfirm.tsx`
- `frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`

## Verification

1) `cd frontend && bun run build` exits 0. 2) `cd frontend && bun run lint` exits 0. 3) Manual smoke: navigate to /teams/<personal-team-id> as the team owner, assert mirror-section is NOT rendered (personal team), assert connections-section IS rendered with empty state. Navigate to /teams/<non-personal-team-id> as admin, assert both sections render. Click Install GitHub App, assert the new tab opens with a URL of shape `<base>/apps/<client_id>/installations/new?state=<jwt>` (state will be a JWT). Toggle always-on, assert switch flips and toast appears. (T05 codifies this.) 4) `grep -E 'mirror-always-on-toggle|install-github-cta' frontend/src/components/Teams/` returns matches. 5) `grep 'noopener,noreferrer' frontend/src/components/Teams/GitHub/ConnectionsList.tsx` matches (XSS hardening invariant).

## Observability Impact

Toast on install-CTA failure surfaces the backend's 404 `github_app_not_configured` shape if the admin hasn't seeded credentials yet — operator-debuggable without console inspection. Always-on PATCH failure rolls back the optimistic update and toasts the backend error verbatim. New tab opens with `noopener,noreferrer` to prevent the GitHub-redirected page from manipulating window.opener.
