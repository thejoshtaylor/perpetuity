# S04: Projects CRUD + two-hop clone + push-back rule storage + auto-push executor — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-28T00:35:54.074Z

# S04 UAT — Projects CRUD + Two-Hop Clone + Auto-Push

This UAT exercises the slice's user-facing contract end-to-end. It is the live-stack version of the e2e test (`backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py`) for human-driven verification. S07 covers the same scenarios against a real GitHub test org; this UAT is against the in-repo mock-github fixtures so a developer can run it on a laptop.

## Preconditions

- `docker compose up -d db redis` is running.
- Backend image `perpetuity/backend:latest` and orchestrator image `perpetuity/orchestrator:latest` are built (`docker compose build backend orchestrator`).
- Workspace image `perpetuity/workspace:test` is built and contains `git`, `curl`, `git-daemon` (verify: `docker run --rm --entrypoint sh perpetuity/workspace:test -c 'which git curl /usr/lib/git-core/git-daemon'`).
- A test admin account exists in the test team (the e2e signs one up; for manual UAT seed via `psql` or use the signup endpoint).
- `system_settings` is seeded with the Fernet key + a fake GitHub App `app_id` + private key (`test_m004_s04_two_hop_clone_e2e._install_fake_github_app_credentials_via_psql` is the canonical seed path).
- `mirror_idle_timeout_seconds=86400` is seeded so the mirror does not reap mid-test.
- `github_app_installations` has one row directly INSERT'd via psql (skipping the install handshake — S02 covers it): `(team_id=<test_team>, installation_id=99001, account_login='acme', account_type='Organization')`.
- `GITHUB_API_BASE_URL` and `GITHUB_CLONE_BASE_URL` env vars on the orchestrator are pointed at the two mock-github sidecars.

## Test Cases

### TC-01: Create project (admin, happy path)
**Steps**:
1. As team admin, `POST /api/v1/teams/{team_id}/projects` with body `{"installation_id": 99001, "github_repo_full_name": "acme/widgets", "name": "widgets"}`.
2. `psql -c "SELECT id, team_id, installation_id, github_repo_full_name, name, last_push_status FROM projects WHERE team_id='<id>'"`
3. `psql -c "SELECT project_id, mode, branch_pattern, workflow_id FROM project_push_rules"`

**Expected**:
- 200 OK with the new project row in the body.
- The project row exists with `last_push_status=NULL`.
- A push-rule row exists with `mode='manual_workflow'`, `branch_pattern=NULL`, `workflow_id=NULL` (default).

### TC-02: Reject unknown installation (admin)
**Steps**: Same as TC-01 but with `installation_id=99999` (not registered to the team).

**Expected**: 404 with `{"detail": "installation_not_in_team"}`.

### TC-03: Cross-team enumeration block (non-member)
**Steps**: Sign up a user who is NOT a member of the team. `GET /api/v1/projects/<project_id>` (the project from TC-01).

**Expected**: 404 (NOT 403). Response body does not reveal whether the project exists.

### TC-04: Push-rule PUT — all three modes
**Steps**:
1. `PUT /api/v1/projects/<id>/push-rule` body `{"mode": "auto"}` → 200.
2. `PUT .../push-rule` body `{"mode": "rule", "branch_pattern": "main"}` → 200.
3. `PUT .../push-rule` body `{"mode": "rule"}` (missing branch_pattern) → 422.
4. `PUT .../push-rule` body `{"mode": "manual_workflow", "workflow_id": "deploy.yml"}` → 200.
5. `PUT .../push-rule` body `{"mode": "garbage"}` → 422.

**Expected**: HTTP codes as listed; `psql ... WHERE project_id='<id>'` reflects each successful update; the 422s do not change row state. INFO log line `project_push_rule_updated project_id=<uuid> mode=<...> actor_id=<uuid>` fires on each successful PUT.

### TC-05: First open materializes — happy path
**Preconditions**: TC-01 done; push-rule = `auto`.

**Steps**:
1. As a team member, `POST /api/v1/projects/<id>/open`.
2. `docker ps --filter label=perpetuity.team_mirror=true --filter label=perpetuity.team_id=<team_id>`.
3. `docker exec <mirror> cat /repos/<project_id>.git/config`.
4. `docker exec <mirror> ls -l /repos/<project_id>.git/hooks/post-receive`.
5. `docker exec <mirror> cat /repos/<project_id>.git/hooks/post-receive`.
6. `docker inspect <user_ws_container> --format '{{.HostConfig.NetworkMode}}'`.
7. `docker exec <user_ws> cat /workspaces/<user_id>/<team_id>/widgets/.git/config`.
8. `docker logs <orch>` filtered to the four expected log markers.

**Expected**:
- 200 with `{"workspace_path": "/workspaces/<u>/<t>/widgets", "mirror_status": "created", "user_status": "created"}` (or `"reused"` on a re-run).
- Mirror container is running with `perpetuity.team_mirror=true`.
- Mirror's `.git/config` contains `https://github.com/acme/widgets.git` and ZERO matches for `x-access-token`, `gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_`.
- Post-receive hook is mode 0755, owned by the workspace user; its content uses `curl -fsS -X POST` (NOT wget) and references `$PERPETUITY_ORCH_KEY` and the orchestrator URL.
- `NetworkMode=perpetuity_default` (MEM264 closed).
- User-side `.git/config` contains `git://team-mirror-<first8>:9418/<project_id>.git` with ZERO matches for `x-access-token` and ZERO matches for `https://github.com`.
- Orchestrator log shows all four markers in order: `team_mirror_clone_started`, `team_mirror_clone_completed`, `user_clone_started`, `user_clone_completed`.

### TC-06: Idempotent re-open
**Steps**: Run TC-05's POST a second time without changing anything else.

**Expected**: 200 with `{"mirror_status": "reused", "user_status": "reused"}`. No new mirror or user-session container created. Both clone-completed log lines carry `result=reused duration_ms=0`. The token-mint code path is NOT invoked on the reused path (verifiable by no new entry in `gh:installtok:99001` redis cache TTL extension).

### TC-07: Auto-push round-trip (mode=auto)
**Preconditions**: TC-05 done; push-rule = `auto`.

**Steps**:
1. `docker exec <user_ws> bash -lc 'cd /workspaces/<u>/<t>/widgets && git config user.email t@example.com && git config user.name t && echo update > readme.md && git add . && git commit -m "test commit" && git push origin main'`
2. Within 10s: `docker logs <orch>` filtered for `auto_push_started` / `auto_push_completed`.
3. `docker exec <mock-github-git-daemon> git --git-dir=/srv/git/acme/widgets.git log --oneline main`.
4. `psql -c "SELECT last_push_status, last_push_error FROM projects WHERE id='<project_id>'"`.

**Expected**:
- User-side push exits 0.
- Within 10s, orchestrator logs `auto_push_started project_id=<uuid> rule_mode=auto trigger=post_receive` followed by `auto_push_completed project_id=<uuid> result=ok`.
- Mock-github upstream's `git log` shows the new commit's subject `test commit`.
- `psql` returns `(last_push_status='ok', last_push_error=NULL)`.

### TC-08: Auto-push failure path (rejected-by-remote)
**Preconditions**: a SECOND project on the same team pointing at `acme/missing` (push-rule=auto, mirror has cloned it, then the upstream's `acme/missing.git` is forcibly deleted from the mock-github sidecar to simulate a remote-side delete).

**Steps**:
1. As the user, commit + push from `widgets-2`'s workspace.
2. Watch orchestrator logs.
3. `psql -c "SELECT last_push_status, last_push_error FROM projects WHERE id='<missing_project_id>'"`.
4. `docker logs <orch> | grep -E 'gho_|ghs_|ghu_|ghr_|github_pat_|-----BEGIN'` — full text scan.

**Expected**:
- WARNING `auto_push_rejected_by_remote project_id=<uuid> exit_code=<n> stderr_short=<safe>` fires within 10s.
- `last_push_status='failed'`; `last_push_error` is non-empty AND contains zero matches against the GitHub token-prefix regex AND zero matches for `-----BEGIN` (defense-in-depth scrub from MEM278).
- Orchestrator log token-prefix scan returns ZERO matches outside the canonical `token_prefix=ghs_<4>…` shape (MEM262).
- The user's git push still exits 0 (auto-push is best-effort per D024 — the `|| true` in the hook script keeps the user's push from failing on backend trouble).

### TC-09: PUT push-rule transition reinstalls/uninstalls hook
**Preconditions**: TC-05 done; mirror is running.

**Steps**:
1. `PUT .../push-rule {"mode":"manual_workflow","workflow_id":"deploy.yml"}`.
2. `docker exec <mirror> ls /repos/<project_id>.git/hooks/post-receive` — should fail (hook removed).
3. `PUT .../push-rule {"mode":"auto"}`.
4. `docker exec <mirror> ls /repos/<project_id>.git/hooks/post-receive` — should succeed (hook reinstalled).
5. Stop the mirror container (`docker stop <mirror>`), then `PUT .../push-rule {"mode":"manual_workflow",...}`.

**Expected**:
- Step 2: `ls` returns non-zero (hook absent).
- Step 4: hook present, mode 0755.
- Step 5: PUT returns 200 (rule write succeeds even though uninstall-push-hook orchestrator call fails). WARNING `push_hook_orch_call_unreachable` is logged but does NOT fail the response. Next `/open` reconverges from the persisted rule.

### TC-10: Redaction sweep across the full session
**Steps**: After running TC-01 through TC-09, capture full container logs:
```
docker logs <orch> > /tmp/orch.log 2>&1
docker logs <backend> > /tmp/backend.log 2>&1
grep -cE 'gho_|ghu_|ghr_|github_pat_|-----BEGIN' /tmp/orch.log /tmp/backend.log
grep -cE 'ghs_[A-Za-z0-9_-]{8,}' /tmp/orch.log /tmp/backend.log   # full ghs_ tokens (not the canonical 4-char prefix)
```

**Expected**: BOTH grep counts return 0. The only allowed `ghs_` matches are the canonical `token_prefix=ghs_<4chars>` shape (≤8 chars after the prefix). Use the e2e's regex (`re.search(rb"ghs_[A-Za-z0-9_-]{8,}", log_bytes)`) to confirm zero substantive matches.

## Edge cases

- **EC-1**: A project whose installation row was deleted between project create and `/open` → 404 with `project_installation_missing` from the orchestrator's materialize-mirror, propagated as 502 by backend `/open`.
- **EC-2**: A project where the user ran `/open` before MEM264 was closed (i.e. against an old image where user containers were not on `perpetuity_default`) → recoverable by simply destroying the user container; the next `/open` provisions a fresh one with the correct NetworkMode.
- **EC-3**: A user pushes from a checkout that has an unrelated history (force-push without `--force`) → mirror's `git push --all` to GitHub may reject; surfaces as TC-08's `auto_push_rejected_by_remote` WARNING. User-side push still exits 0 (best-effort).
- **EC-4**: Two simultaneous `/open` requests from the same user for the same project → second request returns `mirror_status='reused', user_status='reused'`. No race because both clone helpers idempotency-short-circuit on `.git/HEAD` existence. (This is verified by TC-06; the simultaneous-request edge is left for S07 with real concurrency.)
- **EC-5**: Mirror container reaped between hook install and a user push → next `/open` re-materializes the bare repo from GitHub and reinstalls the hook from the persisted push-rule.
- **EC-6**: `volume_provision_failed` 502 between e2e runs (loopback exhaustion from leaked mounts of a prior crashed session) → recover with `docker run --rm --privileged --pid=host alpine:3 nsenter -t 1 -m -- sh -c '<unmount + losetup -d + rm -f .img>'` (MEM282). Not a slice defect; operational only.

## Sign-off criteria

Slice S04 is signed off when TC-01 through TC-10 all return their expected results AND the inspection surfaces show:
- Mirror's `.git/config` is the bare https URL with no credentials.
- User's `.git/config` is the bare git:// URL with no credentials.
- Post-receive hook is present + executable on auto projects and absent on non-auto projects.
- `last_push_status` reflects ground truth on both happy and failure paths.
- Redaction sweep returns zero substantive matches for any GitHub token-prefix or PEM header.
