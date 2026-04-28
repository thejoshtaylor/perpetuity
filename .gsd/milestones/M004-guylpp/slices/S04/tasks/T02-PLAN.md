---
estimated_steps: 1
estimated_files: 6
skills_used: []
---

# T02: Orchestrator clone module + materialize-mirror endpoint (env-on-exec installation token + post-clone .git/config sanitize)

Implement the credential-discipline boundary for the GitHub→mirror hop. Adds `orchestrator/orchestrator/clone.py` exporting `clone_to_mirror(docker, pool, *, team_id, project_id, repo_full_name, installation_id) -> {result, duration_ms}` which: (1) calls `ensure_team_mirror` from S03 to spin up / reuse the team's mirror container; (2) calls `get_installation_token(installation_id, ...)` from S02 to mint or pull-from-cache a fresh installation token; (3) docker-execs `git clone --bare https://x-access-token:<TOKEN>@github.com/<repo_full_name>.git /repos/.tmp/<project_id>.git` inside the mirror container with the token passed via env vars on the exec invocation, NEVER as part of any persisted command line saved anywhere; (4) on success, runs `git --git-dir=/repos/.tmp/<project_id>.git remote set-url origin https://github.com/<repo_full_name>.git` to scrub the token from `.git/config`, then `mv /repos/.tmp/<project_id>.git /repos/<project_id>.git` for atomic rename; (5) verifies post-conditions inside the mirror — `cat /repos/<project_id>.git/config` MUST NOT contain `x-access-token` or any `gho_/ghs_/ghu_/ghr_/github_pat_` substring; if the verification fails, the half-clone is cleaned up (`rm -rf`) and `CloneCredentialLeakDetected` is raised (mapped to 500 `clone_credential_leak`, never reachable in production but the verification is the structural guarantee). Handles idempotency: if `/repos/<project_id>.git/HEAD` already exists, skip the clone and return `{result:'reused', duration_ms:0}` — the mirror's bare repo is the durable state. Adds `orchestrator/orchestrator/routes_projects.py` exposing `POST /v1/projects/{project_id}/materialize-mirror` (gated by SharedSecretMiddleware) — the backend POSTs body `{team_id, repo_full_name, installation_id}` and the route returns `{result, duration_ms}`. Maps `InstallationTokenMintFailed` → 502 `github_clone_failed` with the same `{detail, status, reason}` shape used by S02's token route; maps `DockerUnavailable` → 503 (existing handler); maps generic exec-non-zero from git-clone (auth failure, repo-not-found) → 502 with `reason='git_clone_exit_<code>'`. Wires the new router into `orchestrator/orchestrator/main.py` lifespan. Tests: 12+ unit tests using a `_FakeDocker` exec harness + `_FakePool` covering the happy path, the .git/config sanitize verification, the credential-leak-detection path, the idempotent-re-clone path, the InstallationTokenMintFailed → 502 mapping, the docker-unavailable → 503 mapping, the malformed token / repo-not-found / auth-fail exec exit codes, and the env-on-exec assertion (the token MUST appear in the env dict passed to docker.exec, NEVER in the cmd list). The unit tests use the same `_FakeDocker` exec-harness shape as `test_team_mirror.py` so the executor can crib that pattern.

## Inputs

- ``orchestrator/orchestrator/team_mirror.py` — `ensure_team_mirror` we call to spin up the mirror, plus `_team_mirror_container_name` for the docker-exec target`
- ``orchestrator/orchestrator/github_tokens.py` — `get_installation_token` (cache-first), `InstallationTokenMintFailed`, `_token_prefix` (the only sanctioned way a token may appear in logs)`
- ``orchestrator/orchestrator/sessions.py` — reference for the docker-exec helper shape (`_exec_collect`) and the DockerError → DockerUnavailable wrap pattern`
- ``orchestrator/orchestrator/routes_team_mirror.py` — reference router shape (Pydantic response_model, SharedSecretMiddleware coverage of /v1/* prefixes)`
- ``orchestrator/orchestrator/main.py` — lifespan we extend with the new router and (later in T04) a callback route`
- ``orchestrator/orchestrator/errors.py` — existing OrchestratorError / DockerUnavailable hierarchy we extend with CloneCredentialLeakDetected`

## Expected Output

- ``orchestrator/orchestrator/clone.py` — new module with `clone_to_mirror(...)` and `CloneCredentialLeakDetected`; emits the four required clone log markers`
- ``orchestrator/orchestrator/routes_projects.py` — new APIRouter at prefix=/v1/projects; POST /{project_id}/materialize-mirror returns {result, duration_ms}; maps InstallationTokenMintFailed → 502 and CloneCredentialLeakDetected → 500`
- ``orchestrator/orchestrator/main.py` — registers the new router alongside team_mirror_router`
- ``orchestrator/orchestrator/errors.py` — adds CloneCredentialLeakDetected exception class`
- ``orchestrator/tests/unit/test_clone_to_mirror.py` — unit tests covering happy-path, sanitize-verification, leak-detection, idempotent re-clone, token-mint failure mapping, docker-unavailable mapping, env-on-exec token discipline assertion`
- ``orchestrator/tests/unit/test_routes_projects_materialize_mirror.py` — route-level tests covering the response shape, error mappings, and shared-secret middleware coverage`

## Verification

cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_routes_projects_materialize_mirror.py -v

## Observability Impact

INFO `team_mirror_clone_started team_id=<uuid> project_id=<uuid> repo=<owner/repo> token_prefix=<4>...` (token_prefix uses `_token_prefix` so the full token NEVER appears); INFO `team_mirror_clone_completed team_id=<uuid> project_id=<uuid> result=<created|reused> duration_ms=<n>`; ERROR `team_mirror_clone_failed team_id=<uuid> project_id=<uuid> reason=<short>` (mapped from git-clone exit code or token mint failure); ERROR `clone_credential_leak_detected project_id=<uuid>` on the structural-guarantee path. Inspection: a future agent can `docker exec <mirror> cat /repos/<project_id>.git/config` and confirm the token isn't there; the orchestrator's logs report the full clone duration which is the failure-localization signal for slow GitHub responses. Failure visibility: 502 `github_clone_failed {status, reason}` reaches the backend's POST /open and is propagated to the user; 500 `clone_credential_leak` is the never-reached safety net.
