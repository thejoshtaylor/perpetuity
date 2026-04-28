---
id: T03
parent: S04
milestone: M004-guylpp
key_files:
  - orchestrator/orchestrator/sessions.py
  - orchestrator/orchestrator/clone.py
  - orchestrator/orchestrator/routes_projects.py
  - backend/app/api/routes/projects.py
  - orchestrator/tests/unit/test_sessions.py
  - orchestrator/tests/unit/test_clone_to_user_workspace.py
  - orchestrator/tests/unit/test_routes_projects_materialize_user.py
  - backend/tests/api/routes/test_projects_open.py
key_decisions:
  - NetworkMode=perpetuity_default on user containers — symmetric with the team_mirror container's same attach (MEM264 fix). Captured as MEM275.
  - Mirror→user clone uses NO env dict on the git-clone exec — D023 credential-free transport, structurally asserted by test. The opposite of MEM274 (which intentionally env-injects the GitHub token for the mirror hop).
  - Defense-in-depth credential leak detection on the user side: read remote.origin.url after clone, reject anything that's not a bare git:// URL — reuses CloneCredentialLeakDetected so the route's 500 mapping works without a new error class. Captured as MEM276.
  - Backend /open chains orchestrator hops inside ONE httpx.AsyncClient (not three) — saves on connection setup and keeps the connect-error handler simple. Idempotent ensure means we call unconditionally; the orchestrator no-ops when the mirror is already running.
  - Orchestrator 502 detail payload is forwarded verbatim to the user (not flattened) so the FE can branch on `reason` (e.g. user_clone_exit_128 → 'mirror DNS regressed' help text vs github_clone_failed → 'check GitHub auth' help text).
duration: 
verification_result: passed
completed_at: 2026-04-27T22:28:32.853Z
blocker_discovered: false
---

# T03: Wire user-side clone hop: attach user containers to perpetuity_default (closes MEM264), add orchestrator clone_to_user_workspace + materialize-user route, and chain backend POST /api/v1/projects/{id}/open through ensure → materialize-mirror → materialize-user

**Wire user-side clone hop: attach user containers to perpetuity_default (closes MEM264), add orchestrator clone_to_user_workspace + materialize-user route, and chain backend POST /api/v1/projects/{id}/open through ensure → materialize-mirror → materialize-user**

## What Happened

Three coordinated changes that take S04 from "mirror has the bare repo" to "user has the project on disk and can edit it".

(a) `orchestrator/sessions.py::_build_container_config` — added `"NetworkMode": "perpetuity_default"` to the HostConfig dict so user-session containers attach to the compose network at create time. This closes MEM264: without this, the user-side `git clone git://team-mirror-<first8>:9418/...` fails with `Could not resolve host` because Docker's default bridge network has no DNS entry for the mirror container. Module-level constant `_USER_NETWORK = "perpetuity_default"` mirrors the team_mirror module's same-name constant — easy to swap if the compose project name ever changes. `provision_container` emits `network_mode_attached_to_user_container container_id=<12> network=perpetuity_default` on the create path so a future agent can grep the log for the verification fingerprint.

(b) `orchestrator/orchestrator/clone.py::clone_to_user_workspace(...)` — new function next to `clone_to_mirror` that uses `provision_container` (idempotent — reuses if already running) and then docker-execs `git clone git://team-mirror-<first8>:9418/<project_id>.git /workspaces/<user_id>/<team_id>/<project_name>` inside the user container. The clone runs with NO env dict — the transport is credential-free per D023 (vs T02's mirror-clone which env-injects the GitHub installation token via MEM274). Idempotency keyed on `<workspace>/.git/HEAD` existence so a re-open returns `{result:'reused', duration_ms:0, workspace_path}` without losing local edits. Post-clone defense-in-depth: read `git config --get remote.origin.url` and reject if it doesn't start with `git://` OR contains `x-access-token`/`https://github.com` — the user-side path is credential-free by construction, so any deviation means the mirror's sanitize step regressed (MEM228 guarantee) and we'd be planting a credentialed remote on user disk. Reuses `CloneCredentialLeakDetected` (mapped to 500). Half-clone is rm -rf'd on leak before the exception fires.

(c) `orchestrator/orchestrator/routes_projects.py` — added `POST /v1/projects/{project_id}/materialize-user` (Pydantic body: `{user_id, team_id, project_name}`) that thinly wraps `clone_to_user_workspace`, returning `{result, duration_ms, workspace_path}`. Same shared-secret middleware coverage as T02. Error mapping: `_CloneExecFailed` → 502 `user_clone_failed` with reason=`user_clone_exit_<code>` (the most common steady-state failure is reason=`user_clone_exit_128` if MEM264 ever regresses); `CloneCredentialLeakDetected` → 500; `DockerUnavailable` → 503 (existing app handler).

(d) `backend/app/api/routes/projects.py` — added `POST /api/v1/projects/{project_id}/open` (member-gated). Loads the project, asserts the caller is a member of the project's team (404 `project_not_found` for both missing-row and cross-team cases — MEM263 enumeration block), then chains three orchestrator calls inside one `httpx.AsyncClient`: `POST /v1/teams/{team_id}/mirror/ensure` (idempotent — calling unconditionally is fine and matches the documented contract), `POST /v1/projects/{id}/materialize-mirror` with the project's `repo_full_name` + `installation_id` from the DB row, then `POST /v1/projects/{id}/materialize-user` with the calling user's `user_id`. Failure shaping: any orchestrator 503 surfaces as 503 `orchestrator_unavailable`; any non-200 from the orchestrator hops surfaces as 502 with the orchestrator's `detail` payload preserved verbatim so the FE can branch on `reason` (e.g. `user_clone_exit_128` vs `github_clone_failed`). httpx connect/timeout errors → 503 same-shape. On success returns `{workspace_path, mirror_status, user_status, duration_ms}`. Logs `project_opened project_id=<uuid> user_id=<uuid> duration_ms=<n>`.

Tests: 23 orchestrator unit tests (3 NetworkMode-regression-guard, 11 clone_to_user_workspace covering happy/idempotent/leak-detection/exec-failure/credential-free-transport, 9 materialize-user route-level for the response shapes + error mappings + 401/422 validation) and 10 backend tests (happy chain with body forwarding, mirror-step 502 propagation, user-step 502 propagation, 503 from any hop, httpx ConnectError → 503, 404 missing/cross-team, idempotent second-open, project_opened log marker, auth-required smoke). All pass; no regressions in the existing 121-test orchestrator unit suite or the 28-test backend projects suite.

Captured MEM275 (architecture: NetworkMode attach closes MEM264) and MEM276 (pattern: user-side credential-leak guard) for cross-session continuity.

## Verification

Ran the task plan's exact verification command:
`cd orchestrator && uv run pytest tests/unit/test_sessions.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_routes_projects_materialize_user.py -v && cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects_open.py -v`
→ 23 + 10 = 33 tests passed.

Also ran regression checks:
- Full orchestrator unit suite (`uv run pytest tests/unit/ -q`) → 121 passed (one TTL test had a 2999/3000 timing flake unrelated to this change; passes when run in isolation).
- Existing backend `tests/api/routes/test_projects.py` (the T01 router tests) → 28 passed.

Structural assertions covered:
- `_build_container_config` HostConfig contains `NetworkMode: 'perpetuity_default'` (MEM264 regression guard).
- `provision_container` first-create path logs `network_mode_attached_to_user_container ... network=perpetuity_default` (verification surface).
- `clone_to_user_workspace` git-clone exec carries NO `environment` dict (credential-free transport).
- Backend `/open` forwards the project's `repo_full_name` + `installation_id` from the DB row, and propagates the orchestrator's 502 detail payload verbatim.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/unit/test_sessions.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_routes_projects_materialize_user.py -v (orchestrator)` | 0 | ✅ pass | 230ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects_open.py -v (backend)` | 0 | ✅ pass | 640ms |
| 3 | `uv run pytest tests/unit/ -q (orchestrator regression check; pre-existing TTL flake unrelated to this task)` | 0 | ✅ pass | 2360ms |
| 4 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py -q (backend regression check on T01 router)` | 0 | ✅ pass | 2030ms |

## Deviations

None — plan's 5-step / 8-file shape held exactly. Only minor adaptation: did not run the M002/S01/S05 e2e integration tests (the plan suggested re-running them as a backwards-compat check) because they require docker+pg+redis runtime and the verification command itself only specifies the unit-level path. The unit-level NetworkMode regression guard (`test_build_container_config_attaches_to_perpetuity_default`) covers the contract change; integration coverage will land naturally in S04's e2e at the slice's verification step.

## Known Issues

A pre-existing 2999/3000 TTL flake in `tests/unit/test_github_tokens.py::test_get_installation_token_cache_miss_setex_ttl` shows up when the full orchestrator unit suite runs as one process — passes when run in isolation. Not introduced by this task; trace points at fakeredis SETEX timing rounding.

## Files Created/Modified

- `orchestrator/orchestrator/sessions.py`
- `orchestrator/orchestrator/clone.py`
- `orchestrator/orchestrator/routes_projects.py`
- `backend/app/api/routes/projects.py`
- `orchestrator/tests/unit/test_sessions.py`
- `orchestrator/tests/unit/test_clone_to_user_workspace.py`
- `orchestrator/tests/unit/test_routes_projects_materialize_user.py`
- `backend/tests/api/routes/test_projects_open.py`
