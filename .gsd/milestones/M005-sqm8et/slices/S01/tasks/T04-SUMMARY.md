---
id: T04
parent: S01
milestone: M005-sqm8et
key_files:
  - frontend/src/api/teamSecrets.ts
  - frontend/src/components/team/TeamSecretsPanel.tsx
  - frontend/src/components/team/PasteSecretDialog.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
  - frontend/tests/components/TeamSecretsPanel.spec.ts
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
key_decisions:
  - Used PasswordInput (eye toggle) for the value field rather than the textarea/Input pair from M004's SetSecretDialog — both M005/S01 registered keys are single-line bearer-style API tokens, and the eye toggle gives the operator a way to verify they pasted the right thing without persisting plaintext anywhere except the dialog's local useState.
  - extractDetail unwraps BOTH flat `detail: string` and nested `detail: {detail, hint, key}` shapes — T03's HTTPException(detail=<dict>) wraps the operator-readable discriminator one level deep; without the nested branch the FE silently fell back to apiErr.message and the operator never saw `invalid_value_shape: bad_prefix`. Captured as MEM412.
  - Always render both registered keys (placeholder fallback if GET errors) — the panel never collapses to empty even on 503/decrypt failure or backend outage, so the operator always sees the panel shape and the error together. Mirrors the ConnectionsList error-card-plus-empty-state pattern.
  - Non-admin test stubs `GET /api/v1/teams/` to flip role=member rather than running a second signup/invite dance — the panel's role-aware UI is the only thing under test, and the GET secrets endpoint runs the team-MEMBER gate which the seeded superuser still passes. Avoids reproducing the entire teams.spec.ts invite handshake for a one-line UI assertion.
  - Panel renders for both admins and members (gated by `callerIsAdmin` for the buttons only) rather than being admin-only — the slice plan locks 'non-admin sees read-only badges' and a member needing to verify the team has keys configured shouldn't be blocked from seeing has_value status.
duration: 
verification_result: passed
completed_at: 2026-04-28T22:06:55.152Z
blocker_discovered: false
---

# T04: Added TeamSecretsPanel + paste-once dialog wired into the team detail route, with admin Replace/Delete and non-admin read-only badges backed by 5 Playwright cases.

**Added TeamSecretsPanel + paste-once dialog wired into the team detail route, with admin Replace/Delete and non-admin read-only badges backed by 5 Playwright cases.**

## What Happened

Built `frontend/src/components/team/TeamSecretsPanel.tsx` plus the `PasteSecretDialog` modal and a thin `frontend/src/api/teamSecrets.ts` query-options module, wired the panel into the existing `/_layout/teams_/$teamId` route as an "AI credentials" section that renders for both admins and members.

Component shape mirrors M004's `SystemSettingsList` + `SetSecretDialog` (locked Set/Replace/Delete affordances, "Set"/"Not set" badge with `data-has-value`, lock icon for sensitive rows) but with two adaptations for team-scoped secrets: (1) the value input is `PasswordInput` (eye toggle) instead of a `<textarea>` because both registered keys (`claude_api_key`, `openai_api_key`) are single-line bearer-style tokens not PEMs; (2) `extractDetail` unwraps both flat-string `detail` AND nested `{detail: {detail, hint, key}}` shapes — T03's `HTTPException(detail=<dict>)` 400 nests the discriminator one level deep, so a naive `typeof === "string"` check would silently drop `invalid_value_shape` on the floor.

The panel always renders both registered keys (placeholder fallback if GET errors). Admin sees Set/Replace + Delete; the Delete button is gated on `has_value=true`. Replace opens the paste-once dialog; on submit the React Query mutation calls `PUT /api/v1/teams/{teamId}/secrets/{key}` and on success invalidates `["team", teamId, "secrets"]`. DELETE is idempotent (404 → silent invalidate, mirroring `ConnectionsList.tsx`'s race-tolerance pattern).

Regenerated the OpenAPI client (`scripts/generate-client.sh`) so `TeamSecretsService.{listTeamSecrets, putTeamSecret, getTeamSecretStatus, deleteTeamSecretRoute}` and the `TeamSecretStatus`/`TeamSecretPut` types are available; ignored the pre-existing biome a11y lint failures inside `voice/VoiceInput.tsx` + `voice/VoiceTextarea.tsx` since they predate this task.

Wrote `frontend/tests/components/TeamSecretsPanel.spec.ts` with 5 Playwright cases: (a) admin sees both rows + Set buttons + not-set badges; (b) full paste-once → has_value=true → Delete → has_value=false round-trip against the live backend; (c) bad-prefix value surfaces `invalid_value_shape` toast and keeps the dialog open; (d) member sees read-only rows with no Set/Delete buttons (uses `page.route("**/api/v1/teams/")` to flip role on the real team without a second signup/invite dance); (e) the env-aware `API_BASE` constant uses absolute URLs in `page.request.{get,delete}` so cleanup hits the API not the Vite SPA fallback (gotcha captured as MEM413).

Two pre-execution hiccups: (1) the previous T03 verify gate left a `T03-VERIFY.json` showing `pytest tests/api/test_team_secrets_routes.py` exit 4 because it ran from repo root not `cd backend` — the test file actually exists and T03's PR was clean; surfaced this as the auto-fix-attempt-1 "verification failed" prompt for THIS task but it was a working-directory artifact, not a real T03 regression. (2) the live local backend ran against `perpetuity_app` on port 5432 (not the .env-default `app` on 55432) and was missing both seeded users and the s09 migration; ran `POSTGRES_DB=perpetuity_app POSTGRES_PORT=5432 uv run python -m app.initial_data` and `... uv run alembic upgrade head` to bring it current, then the test suite went green.

## Verification

Ran `cd frontend && npm test -- TeamSecretsPanel` per the task plan: 17 passed across chromium, mobile-chrome, iphone-13-mobile-safari, and desktop-firefox projects; 4 skipped on `mobile-chrome-no-auth` (intentional via `beforeEach` gate matching the existing `teams.spec.ts` pattern — the no-auth project ships no storageState). Per-project breakdown: 5 chromium pass, 4 mobile-chrome pass, 4 iphone-13 pass, 4 desktop-firefox pass. `bunx tsc -p tsconfig.build.json --noEmit` clean on all new files. Slice-level verification gates that this task's output makes possible: backend INFO `team_secret_set`/`team_secret_deleted` and ERROR `team_secret_decrypt_failed` log emission (already passing under T02/T03 unit tests); end-to-end e2e + redaction sweep is T05's contract.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && npm test -- TeamSecretsPanel` | 0 | pass | 24700ms |
| 2 | `cd frontend && bunx tsc -p tsconfig.build.json --noEmit` | 0 | pass | 3500ms |
| 3 | `bash scripts/generate-client.sh (regenerate OpenAPI client; ignored pre-existing biome a11y warnings in voice/* unrelated to this task)` | 1 | pass | 60000ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `frontend/src/api/teamSecrets.ts`
- `frontend/src/components/team/TeamSecretsPanel.tsx`
- `frontend/src/components/team/PasteSecretDialog.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
- `frontend/tests/components/TeamSecretsPanel.spec.ts`
- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
