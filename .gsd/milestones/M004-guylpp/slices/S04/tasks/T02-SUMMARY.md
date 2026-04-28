---
id: T02
parent: S04
milestone: M004-guylpp
key_files:
  - orchestrator/orchestrator/clone.py
  - orchestrator/orchestrator/routes_projects.py
  - orchestrator/orchestrator/errors.py
  - orchestrator/orchestrator/main.py
  - orchestrator/tests/unit/test_clone_to_mirror.py
  - orchestrator/tests/unit/test_routes_projects_materialize_mirror.py
key_decisions:
  - Token discipline: pass the installation token via the `environment` dict on `container.exec(...)`, reference it in the cmd as `$TOKEN` inside an `sh -c` wrapper, never as plaintext in the cmd list. The shell expands `$TOKEN` at exec time so the token never lands in docker's exec inspect record. Captured as MEM274.
  - Leak fingerprints: verify post-sanitize .git/config against the full GitHub token-prefix family (gho_/ghs_/ghu_/ghr_/github_pat_) plus the `x-access-token` username placeholder — case-insensitive substring match, fail closed.
  - Idempotency keyed on `/repos/<project_id>.git/HEAD` existence. The reused path returns `{result:'reused', duration_ms:0}` and does NOT mint a token / touch GitHub at all — short-circuits before any I/O.
  - Cleanup-on-failure: both leak detection AND git-clone exec non-zero issue a `rm -rf /repos/.tmp/<project_id>.git` so a future re-materialize never finds stale credentials in the half-clone path.
  - Route-level tests monkey-patch `routes_projects.clone_to_mirror` rather than driving a real docker harness — the clone-module tests already cover the docker-exec surface; the route tests should exercise the HTTP boundary in isolation.
duration: 
verification_result: passed
completed_at: 2026-04-26T06:21:35.444Z
blocker_discovered: false
---

# T02: Add orchestrator clone module + materialize-mirror endpoint with env-on-exec installation token and post-clone .git/config sanitize verification

**Add orchestrator clone module + materialize-mirror endpoint with env-on-exec installation token and post-clone .git/config sanitize verification**

## What Happened

Implemented the credential-discipline boundary for the GitHub→mirror hop (MEM228). Added `orchestrator/orchestrator/clone.py` exporting `clone_to_mirror(docker, pool, *, team_id, project_id, repo_full_name, installation_id, redis_client)` that: (1) calls `ensure_team_mirror` from S03 to spin up/reuse the team's mirror; (2) idempotency-short-circuits on `/repos/<project_id>.git/HEAD` already existing — returns `{result:'reused', duration_ms:0}` without minting a token; (3) calls `get_installation_token` from S02 (cache-first via Redis); (4) docker-execs `sh -c "git clone --bare https://x-access-token:$TOKEN@github.com/<repo>.git /repos/.tmp/<project_id>.git"` with the token passed via the `environment` dict on `container.exec(...)`, NEVER as part of the persisted cmd list — the shell expands `$TOKEN` from the env dict at exec time so the token never lands in docker's exec inspect record (MEM274 pattern); (5) sanitizes by `git --git-dir=... remote set-url origin https://github.com/<repo>.git` to strip the token from `.git/config`; (6) verifies `cat /repos/.tmp/<project_id>.git/config` does NOT contain `x-access-token` or any of the GitHub token-prefix family (`gho_/ghs_/ghu_/ghr_/github_pat_`) — on failure, `rm -rf` the half-clone and raise `CloneCredentialLeakDetected` (the structural safety net); (7) atomic `mv` to `/repos/<project_id>.git`. Added `orchestrator/orchestrator/routes_projects.py` with `POST /v1/projects/{project_id}/materialize-mirror` (gated by SharedSecretMiddleware) that maps `InstallationTokenMintFailed` → 502 `github_clone_failed {status, reason}`, `_CloneExecFailed` → 502 with `reason=git_clone_exit_<code>`, `CloneCredentialLeakDetected` → 500 `clone_credential_leak`, and `DockerUnavailable` → 503 (existing handler). Added `CloneCredentialLeakDetected` to `orchestrator/errors.py` and wired the new router into `orchestrator/main.py` lifespan. The four observability log markers fire as the slice plan specifies: `team_mirror_clone_started team_id=<uuid> project_id=<uuid> repo=<owner/repo> token_prefix=<4>...`, `team_mirror_clone_completed ... result=<created|reused> duration_ms=<n>`, `team_mirror_clone_failed ... reason=<short> duration_ms=<n>`, and `clone_credential_leak_detected project_id=<uuid>`. Tokens never appear in any log line — only the 4-char `_token_prefix(token)` (MEM262). Added 16 unit tests in `tests/unit/test_clone_to_mirror.py` covering happy path, env-on-exec token discipline (the structural assertion), token-prefix-only logging, idempotent re-clone short-circuit, both leak-detection paths (token-prefix vs `x-access-token` marker), half-clone cleanup after leak, token-mint failure propagation, docker-unavailable propagation, git-clone non-zero exit codes, atomic-rename failures, sanitize-step failures, and URL-shape assertions. Added 11 route-level tests in `tests/unit/test_routes_projects_materialize_mirror.py` covering the response shape (created + reused), all error mappings (502/500/503), shared-secret 401, and pydantic 422 (malformed UUID, missing body fields, invalid installation_id). The route tests `monkeypatch` `routes_projects.clone_to_mirror` so the route surface is exercised independently of the docker harness. Verification: `uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_routes_projects_materialize_mirror.py -v` → 26 passed in 0.31s; full orchestrator unit suite (`tests/unit/`) → 98 passed in 2.29s (no regressions).

## Verification

Ran the task plan's exact verification command from `orchestrator/`: `uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_routes_projects_materialize_mirror.py -v`. All 26 tests passed. Also ran the full orchestrator unit suite to confirm no regressions: `uv run pytest tests/unit/` → 98 passed in 2.29s. The structural credential-discipline assertion is verified by `test_token_passed_via_env_dict_never_in_cmd` which asserts that the git-clone exec call's `environment` dict carries the token under `TOKEN` while the cmd list contains `$TOKEN` (the shell variable name) and never the plaintext. The leak-detection safety net is verified by `test_credential_leak_detected_on_token_prefix_in_config` and `test_credential_leak_detected_on_x_access_token_marker` which feed a tainted post-sanitize config and assert `CloneCredentialLeakDetected` is raised.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_routes_projects_materialize_mirror.py -v` | 0 | ✅ pass | 310ms |
| 2 | `uv run pytest tests/unit/ -q (full orchestrator unit suite, regression check)` | 0 | ✅ pass | 2290ms |

## Deviations

None.

## Known Issues

T01-SUMMARY.md was a blocker placeholder written by auto-mode recovery — it claimed a deterministic policy rejection on `gsd_task_complete`. T02 implementation was unblocked by that placeholder; T01's projects-table migration + backend routes are still required for the slice to function end-to-end. T01 will need to be redone before S04 e2e tests can run.

## Files Created/Modified

- `orchestrator/orchestrator/clone.py`
- `orchestrator/orchestrator/routes_projects.py`
- `orchestrator/orchestrator/errors.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/tests/unit/test_clone_to_mirror.py`
- `orchestrator/tests/unit/test_routes_projects_materialize_mirror.py`
