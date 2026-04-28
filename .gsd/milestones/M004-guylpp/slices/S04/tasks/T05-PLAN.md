---
estimated_steps: 12
estimated_files: 1
skills_used: []
---

# T05: Live-stack e2e: full two-hop materialize + commit-and-push round-trip + auto-push to fixture upstream + redaction sweep

The slice's authoritative integration proof. Single test file `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py`, marked `@pytest.mark.e2e + @pytest.mark.serial`, against the live compose db + redis + an ephemeral orchestrator (parameterized with MIRROR_REAPER_INTERVAL_SECONDS=60 — high enough not to interfere with the test) + sibling backend container + a `mock-github` sibling container running `git daemon --base-path=/srv/git --export-all --reuseaddr --enable=receive-pack` on port 9418 that hosts a fixture bare repo `acme/widgets.git`. Reuses the MEM149/MEM188/MEM252 ephemeral-orchestrator-swap pattern from S02/S03 and the MEM194 readiness probe.

Scenarios A–G walk the slice contract end-to-end:

  A. Setup: drop a fixture bare repo `acme/widgets.git` (with one initial commit) into the mock-github container at `/srv/git/acme/widgets.git`. Seed `system_settings` with the test Fernet key, the fake GitHub App private key + app_id (the orchestrator monkey-patches `github_api_base_url` to point at the mock-github container's HTTP-API sibling for `/app/installations/{id}/access_tokens` — same trick as S02). Signup admin, create a personal team, INSERT a `github_app_installations` row directly via psql (skipping the install handshake — S02 covers it). PUT `mirror_idle_timeout_seconds=86400` so the mirror doesn't reap mid-test.

  B. POST /api/v1/teams/{team_id}/projects with `{installation_id, github_repo_full_name:'acme/widgets', name:'widgets'}` → 200 with the project row; assert `projects` has the row and `project_push_rules` has a default `mode='manual_workflow'` row.

  C. PUT /api/v1/projects/{project_id}/push-rule with `{mode:'auto'}` → 200; assert `project_push_rules.mode='auto'`.

  D. POST /api/v1/projects/{project_id}/open (as the team-member admin) → 200 with `{workspace_path, mirror_status, user_status}`. Assert: (1) the team's mirror container is running with the perpetuity.team_mirror=true label; (2) `docker exec <mirror> cat /repos/<project_id>.git/config` returns `https://github.com/acme/widgets.git` with NO `x-access-token` and NO `gho_/ghs_/ghu_/ghr_/github_pat_` substrings; (3) `docker exec <mirror> ls /repos/<project_id>.git/hooks/post-receive` returns 0 (hook present, executable); (4) the user-session container is running and attached to perpetuity_default (closes MEM264 — `docker inspect ... --format '{{.HostConfig.NetworkMode}}'` returns `perpetuity_default`); (5) `docker exec <user_ws> cat /workspaces/<user_id>/<team_id>/widgets/.git/config` returns `git://team-mirror-<first8>:9418/<project_id>.git` with NO `x-access-token` and NO `https://github.com` substrings; (6) the four expected log lines `team_mirror_clone_started`, `team_mirror_clone_completed`, `user_clone_started`, `user_clone_completed` appear in the captured orchestrator logs.

  E. Idempotency: POST /open a second time → 200; clone log lines on the second call show `result=reused` for both hops; no new container created.

  F. Auto-push round-trip: docker-exec into the user-session container — `cd /workspaces/<u>/<t>/widgets && git config user.email t@example.com && git config user.name t && echo update > readme.md && git add . && git commit -m 'test commit' && git push origin main`. Assert: (1) push exits 0 from the user side; (2) within 10s the orchestrator logs `auto_push_started project_id=<uuid> rule_mode=auto trigger=post_receive`; (3) within 10s the orchestrator logs `auto_push_completed project_id=<uuid> result=ok`; (4) `docker exec <mock-github> git --git-dir=/srv/git/acme/widgets.git log --oneline main` shows the new commit's subject `test commit`; (5) `psql -c 'SELECT last_push_status, last_push_error FROM projects WHERE id=...'` returns `('ok', NULL)`.

  G. Failure path: PUT push-rule with mode=auto on a SECOND project pointing at a non-existent upstream `acme/missing` → POST /open succeeds (clone-to-mirror against the missing repo would NOT succeed in real GitHub but the mock-github fixture lets us seed an empty bare repo and then forcibly delete it after clone to simulate a remote-side delete) → the user pushes a commit → assert `auto_push_rejected_by_remote` WARNING fires; `psql ... last_push_status='failed' AND last_push_error LIKE '%' (and not containing any token substring)`.

  H. Redaction sweep: `docker logs <ephemeral_orch>` and `docker logs <ephemeral_backend>` → assert the combined output contains ZERO matches for the regex `gho_|ghs_|ghu_|ghr_|github_pat_|-----BEGIN`. The full installation token written to env on docker-exec MUST NOT appear in either log.

The e2e amends the conftest with three new compose-network helpers: `_boot_mock_github_git_daemon()` (sibling running git daemon hosting `/srv/git/acme/widgets.git` and `/srv/git/acme/missing.git`), `_boot_mock_github_app_api()` (already exists for S02 — reuses), and `_install_fake_github_app_credentials_via_psql()` (seeds `system_settings` with a fresh Fernet-encrypted PEM + app_id). Cleanup fixture wipes `projects`, `project_push_rules`, `team_mirror_volumes`, `github_app_installations`, all `team-mirror-*` and `perpetuity-ws-*` containers, all `perpetuity-team-mirror-*` volumes, the mock-github sibling, and the ephemeral orchestrator + backend before AND after. Skip-guard probes `backend:latest` for `s06d_projects_and_push_rules.py` and `orchestrator:latest` for `clone.py` + `auto_push.py`.

Wall-clock target: under 90s (orch+backend boot ~25s, mirror cold-start ~5s, both clones ~3s, auto-push round-trip ~4s, failure-path ~4s).

## Inputs

- ``backend/tests/integration/test_m004_s03_team_mirror_e2e.py` — reference e2e bootstrap pattern (ephemeral-orchestrator swap, sibling-backend boot, MEM194 readiness probe, cleanup fixtures, image skip-guards)`
- ``backend/tests/integration/test_m004_s02_github_install_e2e.py` — reference pattern for the mock-github HTTP-API sibling that mints fake installation tokens (S02 already established this)`
- ``backend/tests/integration/conftest.py` — compose-fixture infrastructure (psql helpers, free-port allocation, .env reading)`
- ``orchestrator/orchestrator/clone.py` (T02 + T03) — module under test`
- ``orchestrator/orchestrator/auto_push.py` (T04) — module under test`
- ``backend/app/api/routes/projects.py` (T01 + T03 + T04) — backend endpoints under test`
- ``backend/app/alembic/versions/s06d_projects_and_push_rules.py` (T01) — schema the e2e queries directly via psql`

## Expected Output

- ``backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py` — single @pytest.mark.e2e + @pytest.mark.serial test walking scenarios A–H end-to-end against the live compose stack + ephemeral orchestrator + sibling backend + mock-github fixture sibling; includes the redaction sweep over both backend and orchestrator logs`

## Verification

cd /Users/josh/code/perpetuity && docker compose build backend orchestrator && docker compose up -d db redis && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s04_two_hop_clone_e2e.py -v

## Observability Impact

The test asserts the structural presence of every required log marker in the captured backend + orchestrator logs: `team_mirror_clone_started`, `team_mirror_clone_completed`, `user_clone_started`, `user_clone_completed`, `mirror_push_started`, `mirror_push_completed`, `auto_push_started`, `auto_push_completed`, `auto_push_rejected_by_remote`, `network_mode_attached_to_user_container`, `project_opened`, `post_receive_hook_installed`. On test failure the harness dumps the last 120 lines of orchestrator + backend logs + a `psql` snapshot of `projects` and `project_push_rules`. The redaction sweep (scenario H) is the structural observability contract: any future change that accidentally logs a token plaintext fails the test.
