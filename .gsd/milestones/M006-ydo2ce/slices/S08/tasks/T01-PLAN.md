---
estimated_steps: 19
estimated_files: 1
skills_used: []
---

# T01: Fix 409 response parsing in CreateGitHubRepoDialog.tsx to read nested detail object

## Why
The backend HTTPException at `backend/app/api/routes/github.py:1237-1244` sends `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}` but the frontend at `CreateGitHubRepoDialog.tsx:193-204` compares `body.detail === "github_user_token_required"` (flat string equality). Since `body.detail` is an object, this always fails and the reinstall CTA never renders against the real backend.

## Steps
1. Read `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`.
2. In the `mutationFn` error handling block (~line 193), change:
   - `body.detail === "github_user_token_required"` → `body.detail?.code === "github_user_token_required"`
   - `body.installation_id` → `body.detail?.installation_id`
   - `body.reason` → `body.detail?.reason`
   - Update console.warn to log `body.detail` object fields.
3. Verify the 502 check (`body.detail === "github_token_refresh_transient"`) is unaffected — the 502 backend uses a flat string detail, so no change needed there.
4. Verify the 503 check (`body.detail === "github_user_token_decrypt_failed"`) is also flat string — no change needed.
5. Run `cd frontend && npx tsc --noEmit` to confirm zero TS errors.

## Must-Haves
- `body.detail?.code === "github_user_token_required"` replaces the flat string check
- `installation_id` and `reason` read from `body.detail` sub-object
- 502 and 503 checks remain unchanged (they use flat string detail)
- Zero TypeScript errors

## Done when
`cd frontend && npx tsc --noEmit` exits 0. The 409 branch reads `.detail.code`, `.detail.installation_id`, `.detail.reason`.

## Inputs

- ``frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx` — current 409 parsing logic at lines 191-205`
- ``backend/app/api/routes/github.py` — lines 1237-1244 define the canonical 409 response shape (read-only reference)`

## Expected Output

- ``frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx` — updated 409 parsing to read nested body.detail.code/installation_id/reason`

## Verification

cd frontend && npx tsc --noEmit
