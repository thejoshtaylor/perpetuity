---
estimated_steps: 5
estimated_files: 8
skills_used: []
---

# T03: Backend POST /open + orchestrator clone_to_user_workspace + attach user containers to perpetuity_default network (closes MEM264)

Wire the user-side hop and the backend's POST /open orchestration. Three coordinated changes:

(a) `orchestrator/orchestrator/sessions.py::_build_container_config` — add `"NetworkMode": "perpetuity_default"` to the HostConfig dict so user-session containers can resolve `team-mirror-<first8>:9418` by DNS. This closes MEM264 — without it the user-side `git clone git://...` fails with `Could not resolve host`. The change is one HostConfig key plus an INFO log line `network_mode_attached_to_user_container container_id=<12> network=perpetuity_default` on first provision so a future agent can verify the attach happened. Update `orchestrator/tests/unit/test_sessions.py` (or the closest existing test) to assert the NetworkMode key is present in the built config.

(b) `orchestrator/orchestrator/clone.py::clone_to_user_workspace(docker, *, user_id, team_id, project_id, project_name) -> {result, duration_ms}` — uses `provision_container` from sessions.py (idempotent — reuses if already running) to ensure the user-session container exists, then docker-execs `git clone git://team-mirror-<first8>:9418/<project_id>.git /workspaces/<user_id>/<team_id>/<project_name>` inside that container. The clone runs as the workspace user with no env vars (the transport is credential-free per D023). Verifies post-conditions: the directory exists, `.git/HEAD` exists, `git config remote.origin.url` returns the bare `git://...` URL (NOT containing `x-access-token`, NOT containing `https://github.com`). Handles idempotency: if the target directory already contains a `.git` dir for the same project_id, skip clone and return `{result:'reused', duration_ms:0}` — the user can re-open a project without losing local edits. Adds `POST /v1/projects/{project_id}/materialize-user` to `routes_projects.py` (Pydantic body: `{user_id, team_id, project_name}`) → returns `{result, duration_ms, workspace_path}`. Maps `DockerUnavailable` → 503; maps generic exec-non-zero from git-clone (network failure, mirror-down) → 502 `user_clone_failed`.

(c) `backend/app/api/routes/projects.py` — add `POST /api/v1/projects/{project_id}/open` (member-gated). Loads the project, asserts caller is a member of the project's team, looks up the team's mirror, then chains: `POST /v1/teams/{team_id}/mirror/ensure` (if needed — orchestrator's ensure is idempotent so calling unconditionally is fine and matches the documented contract), `POST /v1/projects/{project_id}/materialize-mirror` with body `{team_id, repo_full_name, installation_id}`, `POST /v1/projects/{project_id}/materialize-user` with body `{user_id, team_id, project_name}`. On orchestrator 502 from either materialize step, propagate as 502 to the user with the orchestrator's `{detail, reason}` payload preserved. On success returns `{workspace_path, mirror_status, user_status}`. Logs `project_opened project_id=<uuid> user_id=<uuid> duration_ms=<n>`. Adds 6+ unit tests (with `httpx.MockTransport` mocking the orchestrator) covering: happy path, mirror-step 502 propagation, user-step 502 propagation, project-not-found 404, non-member 403, idempotent second-open. Tests for the new orchestrator endpoint follow the same `_FakeDocker` exec-harness pattern as T02.

The NetworkMode change in (a) is ALSO carefully checked against existing user-session integration tests — `test_m002_s01_e2e.py` and `test_m002_s05_full_acceptance_e2e.py` — by re-running the relevant suite to confirm the attach-on-existing-network is backwards-compatible (Docker accepts NetworkMode on a network that already exists; the test harness already runs all e2e on `perpetuity_default`).

## Inputs

- ``orchestrator/orchestrator/clone.py` — extends with `clone_to_user_workspace`; reuses the docker-exec helper pattern from `clone_to_mirror``
- ``orchestrator/orchestrator/sessions.py` — modifies `_build_container_config` to add NetworkMode; reuses `provision_container` for idempotent user-container ensure`
- ``orchestrator/orchestrator/routes_projects.py` — extends T02's router with `POST /v1/projects/{project_id}/materialize-user``
- ``orchestrator/orchestrator/team_mirror.py` — `_team_mirror_container_name` and `_network_addr` helpers (the user-side clone target URL)`
- ``backend/app/api/routes/projects.py` — extends T01's router with `POST /api/v1/projects/{project_id}/open``
- ``backend/app/models.py` — Project + GitHubAppInstallation lookups for assembling the materialize calls`

## Expected Output

- ``orchestrator/orchestrator/sessions.py` — `_build_container_config` returns HostConfig with `NetworkMode='perpetuity_default'`; emits `network_mode_attached_to_user_container` INFO on first provision`
- ``orchestrator/orchestrator/clone.py` — adds `clone_to_user_workspace(...)`; emits `user_clone_started` and `user_clone_completed` INFO log lines`
- ``orchestrator/orchestrator/routes_projects.py` — adds `POST /v1/projects/{project_id}/materialize-user` returning `{result, duration_ms, workspace_path}``
- ``backend/app/api/routes/projects.py` — adds `POST /api/v1/projects/{project_id}/open` chaining ensure → materialize-mirror → materialize-user; emits `project_opened` INFO`
- ``orchestrator/tests/unit/test_sessions.py` — asserts NetworkMode is present on the built config (regression guard for MEM264)`
- ``orchestrator/tests/unit/test_clone_to_user_workspace.py` — happy path + idempotent re-clone + provision-failure mapping + git-clone-exit mapping`
- ``orchestrator/tests/unit/test_routes_projects_materialize_user.py` — route-level happy path + 502 + 503 mappings`
- ``backend/tests/api/routes/test_projects_open.py` — backend POST /open chain happy path + step-502 propagation + non-member 403 + project-not-found 404`

## Verification

cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_sessions.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_routes_projects_materialize_user.py -v && cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects_open.py -v

## Observability Impact

INFO `network_mode_attached_to_user_container container_id=<12> network=perpetuity_default` (one-time on first provision per container); INFO `user_clone_started user_id=<uuid> team_id=<uuid> project_id=<uuid>` and `user_clone_completed ... result=<created|reused> duration_ms=<n>`; INFO `project_opened project_id=<uuid> user_id=<uuid> duration_ms=<n>`; ERROR `user_clone_failed user_id=<uuid> project_id=<uuid> reason=<short>` mapped from git-clone exit code (typical failure: `Could not resolve host` if MEM264 regresses, captured as `reason=resolve_failed`). Inspection: a future agent can `docker inspect <user_container> --format '{{json .HostConfig.NetworkMode}}'` to confirm the attach; `docker exec <user_container> cat /workspaces/<u>/<t>/<project_name>/.git/config` to confirm credential-free state. Failure visibility: backend `POST /open` propagates the orchestrator's `{detail, reason}` payload verbatim so the FE can branch on `reason='resolve_failed'` vs `reason='mirror_unreachable'` etc.
