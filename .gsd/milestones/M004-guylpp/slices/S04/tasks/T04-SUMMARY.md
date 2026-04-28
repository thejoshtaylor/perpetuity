---
id: T04
parent: S04
milestone: M004-guylpp
key_files:
  - orchestrator/orchestrator/auto_push.py
  - orchestrator/orchestrator/clone.py
  - orchestrator/orchestrator/team_mirror.py
  - orchestrator/orchestrator/routes_projects.py
  - backend/app/api/routes/projects.py
  - orchestrator/tests/unit/test_auto_push.py
  - orchestrator/tests/unit/test_post_receive_hook_install.py
  - orchestrator/tests/unit/test_routes_projects_auto_push_callback.py
  - orchestrator/tests/unit/test_clone_to_mirror.py
  - backend/tests/api/routes/test_projects.py
key_decisions:
  - Single shared post-receive hook script (PROJECT_ID derived from $GIT_DIR at runtime) keeps every auto-rule project on a team mirror running off one auditable file (MEM277).
  - PERPETUITY_ORCH_KEY is baked into the mirror container's env on `containers/create` rather than written to disk — auth carrier survives docker exec calls without leaking into any DB row or log line.
  - Stderr from a failed `git push` is scrubbed of all GitHub token-prefix families (gho_/ghs_/ghu_/ghr_/github_pat_) before EITHER persistence into projects.last_push_error OR the WARNING log line — defense in depth for the rare git-echoes-the-URL failure modes (MEM278).
  - PUT /push-rule's hook install/uninstall failure is logged WARNING and SWALLOWED — the rule is the source of truth, the hook is derived state, and the next clone-to-mirror reconverges (matches the slice plan's explicit contract).
duration: 
verification_result: passed
completed_at: 2026-04-27T22:43:57.490Z
blocker_discovered: false
---

# T04: Add auto-push executor: post-receive hook installed at clone-time + orchestrator callback that pushes mirror→GitHub with fresh installation token

**Add auto-push executor: post-receive hook installed at clone-time + orchestrator callback that pushes mirror→GitHub with fresh installation token**

## What Happened

Closed the user→mirror→GitHub push-back loop for projects with push_rule.mode=auto.

(a) **Hook install**: extended `clone.py` with `_install_post_receive_hook` and `_uninstall_post_receive_hook`. The hook is a single shared `sh` script written into `/repos/<project_id>.git/hooks/post-receive` (mode 0755) that derives PROJECT_ID from `$GIT_DIR` at runtime and POSTs to `http://orchestrator:8001/v1/projects/$PROJECT_ID/auto-push-callback` with the `X-Orchestrator-Key` header read from `$PERPETUITY_ORCH_KEY` (baked into the mirror container's env via `team_mirror._build_team_mirror_container_config`). The trailing `|| true` keeps the user's git push from failing on a brief orchestrator outage — auto-push is best-effort by D024. `clone_to_mirror` now reads the project's push-rule mode after a successful clone and conditionally installs the hook only when mode=auto.

(b) **Auto-push executor**: added `orchestrator/auto_push.py::run_auto_push(...)`. It loads the project's (team_id, installation_id, github_repo_full_name) from pg, defensively re-checks `project_push_rules.mode == 'auto'` (rule may have flipped between hook install and callback), mints a fresh installation token via the existing cache-first `get_installation_token`, finds the team's mirror container by labels, and docker-execs `git --git-dir=/repos/<id>.git push --all --prune <authed-url>` followed by `git push --tags <authed-url>` with the token in the env dict only (MEM274). On success: `UPDATE projects SET last_push_status='ok', last_push_error=NULL`. On non-zero: persist `last_push_status='failed'` with stderr scrubbed of all GitHub token-prefix substrings (gho_/ghs_/ghu_/ghr_/github_pat_) via `_scrub_token_substrings` — the same scrubbing applies to the WARNING `auto_push_rejected_by_remote` log line. Stderr is capped at 500 chars in the DB and 200 chars in the log.

(c) **Routes**: added `POST /v1/projects/{id}/install-push-hook`, `POST /v1/projects/{id}/uninstall-push-hook` (both no-op with `mirror_missing` when no team mirror is currently running — the next clone-to-mirror reconverges), and `POST /v1/projects/{id}/auto-push-callback` (gated by SharedSecretMiddleware; the hook script presents the env-baked key). All three routes return 200 with structured bodies; the callback always returns 200 with the run_auto_push result body since the post-receive hook ignores response codes.

(d) **Backend integration**: made `PUT /api/v1/projects/{id}/push-rule` async and added an `_orch_call_hook_endpoint` helper. On transitions `non-auto → auto` it fires install-push-hook; on `auto → non-auto` it fires uninstall-push-hook. Failures are logged WARNING (`push_hook_orch_call_unreachable` / `push_hook_orch_call_non_200`) and SWALLOWED — the rule write is the source of truth. Same-side transitions (rule ↔ manual_workflow) make zero orchestrator calls.

Test discipline matched the existing slice tests: hermetic fakes for Docker exec + asyncpg pool; the env-on-exec / token-prefix-only-in-logs assertions are the structural credential-discipline guard. Stderr scrubbing is exercised over all 5 GitHub token-prefix families.

Adjacent regressions: extended the existing `_FakeConn.fetchrow` in `test_clone_to_mirror.py` to recognize the new `SELECT mode FROM project_push_rules` query (returns None → no hook install path, which is the correct behavior for those tests).

## Verification

Ran the task plan's exact verification command (no deviation):

```
cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_auto_push.py tests/unit/test_post_receive_hook_install.py tests/unit/test_routes_projects_auto_push_callback.py -v && cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py -v
```

- Orchestrator: 38 tests passed in 0.25s (14 auto_push + 10 hook install + 14 routes/callback). Includes the structural credential-discipline assertions (token in env dict, never in cmd; logs only carry the 4-char prefix), the byte-for-byte hook-script content check, the rule-changed-skip path, the token-mint-failure path, and the stderr-scrubbing path over all 5 GitHub token-prefix families.

- Backend: 32 tests passed in 2.33s, including the 4 new transition tests:
  - `test_put_push_rule_transition_to_auto_installs_hook`
  - `test_put_push_rule_transition_from_auto_uninstalls_hook`
  - `test_put_push_rule_no_transition_does_not_call_orchestrator`
  - `test_put_push_rule_orch_unreachable_does_not_fail_put`

- Adjacent regression: re-ran `test_clone_to_mirror.py` (16) + `test_clone_to_user_workspace.py` (11) + `test_team_mirror.py` (16) + `test_routes_projects_materialize_mirror.py` (10) + `test_routes_projects_materialize_user.py` (9) + backend `test_projects_open.py` (10) — 72 / 72 passed. No drift in the rest of the slice.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_auto_push.py tests/unit/test_post_receive_hook_install.py tests/unit/test_routes_projects_auto_push_callback.py -v` | 0 | pass | 250ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py -v` | 0 | pass | 2330ms |
| 3 | `cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_team_mirror.py tests/unit/test_routes_projects_materialize_mirror.py tests/unit/test_routes_projects_materialize_user.py` | 0 | pass | 400ms |
| 4 | `cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects_open.py` | 0 | pass | 820ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `orchestrator/orchestrator/auto_push.py`
- `orchestrator/orchestrator/clone.py`
- `orchestrator/orchestrator/team_mirror.py`
- `orchestrator/orchestrator/routes_projects.py`
- `backend/app/api/routes/projects.py`
- `orchestrator/tests/unit/test_auto_push.py`
- `orchestrator/tests/unit/test_post_receive_hook_install.py`
- `orchestrator/tests/unit/test_routes_projects_auto_push_callback.py`
- `orchestrator/tests/unit/test_clone_to_mirror.py`
- `backend/tests/api/routes/test_projects.py`
