---
id: T03
parent: S06
milestone: M004-guylpp
key_files:
  - frontend/src/components/Teams/GitHub/ConnectionsList.tsx
  - frontend/src/components/Teams/GitHub/UninstallConfirm.tsx
  - frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
key_decisions:
  - Probe install-url at mount via a separate React Query (retry:false) to drive the disabled-CTA-with-tooltip state on 404 `github_app_not_configured`. The probe never caches the URL itself — clicks always re-fetch because the URL embeds a 10-min JWT. Captured as MEM303.
  - Default `AlwaysOnToggle` initialAlwaysOn to `false` rather than fetching a separate GET /mirror — the team list response shape doesn't include mirror state, and PATCH-as-canonical is sufficient: the backend auto-creates the row with a placeholder volume_path on first toggle (MEM269), so even unstarted mirrors flip cleanly.
  - Section-level admin gating for connections-section satisfies the negative test (non-admin must not render install-CTA or uninstall actions) without needing per-element role checks — keeps the gating story uniform with invite-section and member-controls.
duration: 
verification_result: passed
completed_at: 2026-04-28T03:26:02.921Z
blocker_discovered: false
---

# T03: Add team-admin GitHub connections list with install CTA + uninstall confirm and per-team mirror always-on toggle to the team detail route

**Add team-admin GitHub connections list with install CTA + uninstall confirm and per-team mirror always-on toggle to the team detail route**

## What Happened

Extended `frontend/src/routes/_layout/teams_.$teamId.tsx` with two new admin-gated sections — `connections-section` (GitHub installations) and `mirror-section` (always-on toggle) — and shipped the three new components the slice plan specified.

`ConnectionsList.tsx` runs two React Query queries: the installations envelope under `['team', teamId, 'github', 'installations']`, and a separate install-url *probe* under `['team', teamId, 'github', 'install-url-probe']` (retry:false, refetchOnWindowFocus:false). The probe lets the CTA flip to a `disabled` button wrapped in a `TooltipProvider/Tooltip` reading "System admin must seed GitHub App credentials before installing" the moment GET install-url returns 404 `github_app_not_configured` — operator-debuggable without DevTools (closes the operator UX gap from S04). The CTA's click handler re-fetches the URL on each click because it embeds a 10-min JWT, then opens it via `window.open(resp.install_url, "_blank", "noopener,noreferrer")` (XSS hardening invariant). Captured this pattern as MEM303.

`UninstallConfirm.tsx` reuses the project's `Dialog`/`LoadingButton` primitives (mirroring `RemoveMemberConfirm` in shape but lighter — uninstall doesn't need a confirm-phrase challenge because the GitHub-side install must be revoked separately). DELETE 404 is treated as race-tolerant — silent invalidate of the installations cache, no toast. DELETE other errors surface the backend `detail` verbatim into a sonner toast.

`AlwaysOnToggle.tsx` wraps the T01 shadcn `Switch` primitive with an optimistic mutation against PATCH /api/v1/teams/{id}/mirror. Initial state defaults to `false` because `TeamWithRole` (the shape returned by GET /teams) does not yet expose a `mirror.always_on` field; the PATCH response is treated as canonical. The mutation's `onMutate` flips the local state and stashes the prior value as rollback context; `onError` rolls back and toasts the backend's detail; `onSuccess` re-anchors to the server-confirmed value and toasts "Mirror always-on enabled" / "Mirror always-on disabled". Mirror-section is suppressed for personal teams (`!team.is_personal`) per the plan — personal teams have no shared-mirror semantics. Both sections are admin-gated via the existing `team.role === 'admin'` check (consistent with invite + member-controls).

All `data-testid` keys T05 will bind are emitted: `connections-section`, `install-github-cta`, `installation-row-<installation_id>`, `installation-uninstall-<installation_id>`, `installation-uninstall-confirm`, `mirror-section`, `mirror-always-on-toggle`. Failure modes Q5 implemented: install-url 404 → disabled CTA with tooltip; install-url 403 → CTA hidden by section-level admin gate; DELETE 404 → silent invalidate; PATCH error → optimistic rollback + toast. Negative tests Q7 satisfied structurally: personal team won't render mirror-section (gated by `!team.is_personal`); non-admin won't render install-CTA or uninstall actions (gated by `callerIsAdmin` at section + row); cancel button in UninstallConfirm uses `DialogClose` and never invokes `onConfirm`. T05 will codify these in Playwright.

## Verification

Ran `cd frontend && bun run lint` (biome check --write --unsafe — checked 92 files in 46ms, fixed 2 files for formatting, zero errors). Ran `cd frontend && bun run build` (tsc -p tsconfig.build.json + vite build, 2267 modules, built in 1.96s, exit 0). Grep verification: `grep -rE 'mirror-always-on-toggle|install-github-cta' frontend/src/components/Teams/` returns 5 matches across the new components; `grep 'noopener,noreferrer' frontend/src/components/Teams/GitHub/ConnectionsList.tsx` matches the window.open call (XSS hardening invariant). Manual smoke (verification step 3) is deferred to T05's Playwright spec which the slice plan explicitly says "T05 codifies this".

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run lint` | 0 | ✅ pass | 46ms |
| 2 | `cd frontend && bun run build` | 0 | ✅ pass | 1960ms |
| 3 | `grep -rE 'mirror-always-on-toggle|install-github-cta' frontend/src/components/Teams/` | 0 | ✅ pass | 50ms |
| 4 | `grep 'noopener,noreferrer' frontend/src/components/Teams/GitHub/ConnectionsList.tsx` | 0 | ✅ pass | 20ms |

## Deviations

None of consequence. The plan listed `frontend/src/client/sdk.gen.ts` as an input but it was only read, not modified (the SDK was regenerated in T01). No SDK changes were needed — `GithubService.{listGithubInstallations, getGithubInstallUrl, deleteGithubInstallation}` and `TeamsService.updateTeamMirror` already exist with the expected shapes.

## Known Issues

None. Manual smoke verification (verification step 3 — clicking through personal vs non-personal teams, asserting URL shape from Install GitHub App) is deferred to T05's Playwright spec per the slice plan's explicit note ("T05 codifies this").

## Files Created/Modified

- `frontend/src/components/Teams/GitHub/ConnectionsList.tsx`
- `frontend/src/components/Teams/GitHub/UninstallConfirm.tsx`
- `frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
