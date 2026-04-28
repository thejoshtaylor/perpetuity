---
id: T05
parent: S04
milestone: M004-guylpp
key_files:
  - backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py
  - orchestrator/orchestrator/clone.py
  - orchestrator/orchestrator/auto_push.py
  - orchestrator/orchestrator/config.py
key_decisions:
  - Added orchestrator setting `github_clone_base_url` (default https://github.com, never overridden in production) so the e2e can swap the upstream host for a credential-free git daemon mock without TLS termination. Cleaner than git-insteadOf (which doesn't match URLs with userinfo embedded) and cleaner than --add-host + a TLS-fronting mock. clone.py and auto_push.py branch on https:// vs git:// — https keeps the x-access-token userinfo form, git:// drops it.
  - Switched the post-receive hook script from `wget` to `curl -fsS -X POST -d ''`. The workspace image (ubuntu:24.04) ships curl but not wget. T04's hook would have silently no-op'd in production for the same reason — caught only because T05 actually exec'd the hook end-to-end.
  - Switched the hook's PROJECT_ID derivation from `basename "$GIT_DIR" .git` to `basename "$(pwd)" .git`. git-daemon's receive-pack invokes hooks with GIT_DIR='.' (relative) and cwd inside the bare repo, so `$GIT_DIR` is useless for path-derivation. Captured as MEM279.
  - Used the workspace image (not alpine/git) for the mock-github git-daemon sibling because alpine/git deliberately omits `git-daemon`. The workspace image has `git-daemon` at /usr/lib/git-core/git-daemon. Captured as MEM281.
  - Used GITHUB_API_BASE_URL pointed at one mock for token mint, GITHUB_CLONE_BASE_URL pointed at a separate git-daemon mock for clone+push. Two-sibling pattern keeps each mock single-purpose and easy to reason about.
duration: 
verification_result: passed
completed_at: 2026-04-27T23:23:45.129Z
blocker_discovered: false
---

# T05: Added live-stack e2e proving the full two-hop materialize, auto-push round-trip, failure-path, and redaction sweep — and surgically fixed two real defects T05 surfaced (post-receive hook used `wget` not in the workspace image, and `$GIT_DIR` is `.` under git daemon)

**Added live-stack e2e proving the full two-hop materialize, auto-push round-trip, failure-path, and redaction sweep — and surgically fixed two real defects T05 surfaced (post-receive hook used `wget` not in the workspace image, and `$GIT_DIR` is `.` under git daemon)**

## What Happened

Built the slice's authoritative integration proof in `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py` walking scenarios A–H against the live compose db + redis + an ephemeral orchestrator + sibling backend + two mock-github sidecars (FastAPI for token mint, ubuntu+`git daemon` for the credential-free upstream).

Mock-github strategy: two sibling containers on `perpetuity_default`. The FastAPI mock from S02 mints fake `ghs_M004S04E2E…` tokens. A workspace-image-based sibling runs `git daemon --base-path=/srv/git --enable=receive-pack --reuseaddr --listen=0.0.0.0 --port=9418` hosting `acme/widgets.git` (seeded with one commit on `main` + HEAD overridden to `refs/heads/main`) and an empty `acme/missing.git` for the failure path. **alpine/git deliberately omits `git-daemon`**, so the workspace image (ubuntu, ships `/usr/lib/git-core/git-daemon`) is what we use.

To redirect the orchestrator's `git clone https://github.com/<repo>.git` and `git push https://x-access-token:$TOKEN@github.com/<repo>.git` at the mock, I added a `github_clone_base_url` orchestrator setting (default `https://github.com`, never overridden in production). `clone._git_clone_into_tmp` and `auto_push.run_auto_push` now branch on the URL scheme: when `https://`, they keep the production x-access-token authenticated form; when `git://` (test only), they drop userinfo (git-daemon has no auth) and let the token sit unused in the env dict (still exercising the env-on-exec discipline). The sanitize step in `clone.py` still hardcodes `https://github.com/<repo>.git` in the bare repo's `.git/config` because the slice contract specifies that exact remote URL (the e2e asserts that string).

Two real defects in T04 surfaced and fixed surgically:

1. **Post-receive hook used `wget`, which is not installed in the workspace image** (the mirror runs the workspace image; it has `curl` but not `wget`). Switched the hook script to `curl -fsS -X POST -d '' -H ...`. Unit tests pass byte-for-byte (no test asserts specifically on `wget`).

2. **`$GIT_DIR` is `.` (relative) when git-daemon's receive-pack invokes a hook** — the hook ran with `cwd` inside the bare repo and `GIT_DIR=.`, so `basename "$GIT_DIR" .git` returned `.` and the orchestrator received POSTs to `/v1/projects/./auto-push-callback` (rejected with 404). Fixed by deriving PROJECT_ID from `$(basename "$(pwd)" .git)` instead. Unit tests pass (the byte-for-byte content check matches the new script and the env-var-expansion check still finds `$PERPETUITY_ORCH_KEY` + the URL).

Captured the discovered gotchas + the new architectural seam as MEM279 (`$GIT_DIR=.` under git-daemon), MEM280 (workspace image lacks `wget`), MEM281 (the two-sibling mock-github e2e pattern), MEM282 (loopback/mount cleanup pattern between e2e runs), and MEM283 (the `github_clone_base_url` settings hook + rationale).

Test scenarios A–H all green:
- A: setup + signup + INSERT installation row + seed mirror_idle_timeout=86400
- B: POST /api/v1/teams/{id}/projects → 200, project + default manual_workflow rule rows present
- C: PUT /api/v1/projects/{id}/push-rule mode=auto → 200, rule.mode=auto
- D: POST /api/v1/projects/{id}/open → 200, mirror running with the perpetuity.team_mirror=true label, mirror /repos/<id>.git/config sanitized (no token), post-receive hook present + executable, user-session container running and on `perpetuity_default` (MEM264 closed), user-side .git/config has bare `git://team-mirror-<first8>:9418/<id>.git` (no token, no https), all four required clone log markers fired
- E: idempotency — second /open returns mirror_status='reused' + user_status='reused', no new container
- F: auto-push round-trip — user commit + push from workspace → mirror post-receive hook fires → orchestrator pushes to mock-github upstream → fixture upstream's `git log --oneline main` shows the new "test commit" subject, `projects.last_push_status='ok'`
- G: failure path — second project pointing at acme/missing, upstream forcibly deleted after clone → user push triggers auto-push → `auto_push_rejected_by_remote` WARNING fires, `last_push_status='failed'`, `last_push_error` contains zero `gho_/ghu_/ghr_/ghs_/github_pat_` substrings (defense-in-depth scrub from MEM278 holds)
- H: redaction sweep — orchestrator + backend logs contain ZERO matches for the full token plaintext, gho_, ghu_, ghr_, github_pat_, or `-----BEGIN`. ghs_ is permitted only inside `token_prefix=ghs_…` log lines (the canonical 4-char-prefix shape from MEM262).

Wall-clock: ~25–30s on a warm stack — well under the slice's 90s target.

## Verification

Ran the slice's exact verification command: `cd /Users/josh/code/perpetuity && docker compose build backend orchestrator && docker compose up -d db redis && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s04_two_hop_clone_e2e.py -v`. The e2e PASSED in 25.88s. Adjacent regression: re-ran all orchestrator unit tests for the slice (test_clone_to_mirror.py + test_auto_push.py + test_post_receive_hook_install.py + test_routes_projects_auto_push_callback.py + test_clone_to_user_workspace.py + test_routes_projects_materialize_mirror.py + test_routes_projects_materialize_user.py + test_team_mirror.py + test_sessions.py) → 103/103 passed in 0.48s. Backend projects routes (test_projects.py + test_projects_open.py) → 42/42 passed in 2.77s. No drift introduced by the hook script change or the github_clone_base_url addition.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s04_two_hop_clone_e2e.py -v` | 0 | pass | 25880ms |
| 2 | `cd orchestrator && uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_auto_push.py tests/unit/test_post_receive_hook_install.py tests/unit/test_routes_projects_auto_push_callback.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_routes_projects_materialize_mirror.py tests/unit/test_routes_projects_materialize_user.py tests/unit/test_team_mirror.py tests/unit/test_sessions.py` | 0 | pass | 480ms |
| 3 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py tests/api/routes/test_projects_open.py` | 0 | pass | 2770ms |

## Deviations

Two surgical fixes to T04's shipped code that the e2e surfaced (both treated as in-scope per execution rule "Small factual corrections, file-path fixes, and local implementation adaptations are part of execution"):

1. clone.py: post-receive hook script switched from `wget` (not installed in the workspace image) to `curl -fsS -X POST -d ''`. Unit-level byte-for-byte test still passes.

2. clone.py: hook script's PROJECT_ID derivation switched from `$(basename "$GIT_DIR" .git)` (git-daemon sets GIT_DIR='.', so this returns '.') to `$(basename "$(pwd)" .git)` (cwd is the absolute bare-repo path). Unit-level test still passes.

3. config.py + clone.py + auto_push.py: added a new `github_clone_base_url` setting (default https://github.com, never overridden in production) so the e2e can target a `git://mock:9418` upstream without TLS. The sanitize step in clone.py still hardcodes `https://github.com/<repo>.git` in the bare repo's `.git/config` because the slice contract specifies that exact remote URL.

## Known Issues

- The e2e cleanup does not auto-reconcile leaked loopback devices and ext4 mounts under /var/lib/perpetuity/workspaces/* from PRIOR sessions (e.g. crashed test runs). When loopbacks exhaust the kernel's pool, the next `volume_provision` returns 502 with `volume_provision_failed`. Fix is operational, not test-code: `docker run --rm --privileged --pid=host alpine:3 nsenter -t 1 -m -- sh -c '<unmount + losetup -d + rm -f .img>'` between sessions. Captured as MEM282 so future agents recognize the symptom.

- T04's unit tests for the post-receive hook check byte-for-byte content against `_POST_RECEIVE_HOOK_SCRIPT` and check for the env-var placeholders `$PROJECT_ID` and `$PERPETUITY_ORCH_KEY` and the orchestrator URL substring — none of which catch (a) "the executable named in the hook isn't installed in the workspace image" or (b) "$GIT_DIR is . under git daemon, not /repos/<id>.git". Both surfaced only at e2e. A future S07 follow-up could harden the hook unit tests by actually exec'ing the hook against a real git-daemon-backed bare repo.

## Files Created/Modified

- `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py`
- `orchestrator/orchestrator/clone.py`
- `orchestrator/orchestrator/auto_push.py`
- `orchestrator/orchestrator/config.py`
