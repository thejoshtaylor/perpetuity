---
id: T01
parent: S08
milestone: M006-ydo2ce
key_files:
  - frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx
key_decisions:
  - 502 and 503 error branches left unchanged — backend uses flat string detail for those status codes
  - Optional chaining (?.) used on body.detail access to safely handle unexpected response shapes
duration: 
verification_result: passed
completed_at: 2026-05-13T05:28:56.127Z
blocker_discovered: false
---

# T01: Fixed 409 response parsing in CreateGitHubRepoDialog.tsx to read nested body.detail.code/installation_id/reason instead of flat string comparison

**Fixed 409 response parsing in CreateGitHubRepoDialog.tsx to read nested body.detail.code/installation_id/reason instead of flat string comparison**

## What Happened

The backend `github.py:1237-1244` raises an HTTPException with `detail={"code": "github_user_token_required", "installation_id": N, "reason": "..."}` (a nested object), but the frontend was comparing `body.detail === "github_user_token_required"` (flat string equality). Since `body.detail` is an object, the condition always evaluated false and the reinstall CTA never rendered.\n\nThe fix changes three read sites in the 409 branch:\n- `body.detail === "github_user_token_required"` → `body.detail?.code === "github_user_token_required"`\n- `body.installation_id` → `body.detail?.installation_id`\n- `body.reason` → `body.detail?.reason`\n- `console.warn` arguments updated to log `body.detail?.installation_id` and `body.detail?.reason`\n\nThe 502 and 503 checks (`body.detail === "github_token_refresh_transient"` and `body.detail === "github_user_token_decrypt_failed"`) were left unchanged — those backend paths raise with a flat string detail, confirmed at `github.py:1245+`.\n\nTypeScript check confirmed zero errors in CreateGitHubRepoDialog.tsx. Pre-existing errors in `vapid.test.ts` and `m005-*.spec.ts` (missing `vitest` module and ungenerated `sdk.gen.ts`) are unrelated and pre-date this change.

## Verification

Ran `cd frontend && npx tsc --noEmit`. Grep confirmed no TS errors referencing CreateGitHubRepoDialog.tsx. Pre-existing errors are in unrelated test files (vapid.test.ts, m005 Playwright specs) with missing generated artifacts — none introduced by this change.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/frontend && npx tsc --noEmit 2>&1 | grep -i CreateGitHubRepoDialog` | 1 | pass — zero matches (no TS errors in changed file) | 12000ms |

## Deviations

none

## Known Issues

Pre-existing TS errors in frontend/src/lib/vapid.test.ts (missing vitest types) and tests/m005-*.spec.ts (missing generated sdk.gen.ts) are unrelated to this task.

## Files Created/Modified

- `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`
