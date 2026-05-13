---
id: T01
parent: S07
milestone: M006-ydo2ce
key_files:
  - /.gsd/milestones/M006-ydo2ce/evidence/00-preflight.md
key_decisions:
  - Backend health route is /api/v1/utils/health-check/ not /api/v1/health (task plan had the wrong path; adapted without blocker)
  - Alembic current connects to test DB at localhost:55432 (perpetuity-test-db), not the compose DB at 5432
  - Compose DB (perpetuity-db-1) has unrelated schema z3m_case_contact_updated_idx — it is not used by the backend in this dev setup
duration: 
verification_result: passed
completed_at: 2026-05-13T00:33:01.159Z
blocker_discovered: false
---

# T01: Pre-flight checks passed: alembic head = s17_github_user_oauth_tokens, all 5 M006 services reachable, 00-preflight.md written; GitHub App OAuth creds not yet seeded (human operator action required)

**Pre-flight checks passed: alembic head = s17_github_user_oauth_tokens, all 5 M006 services reachable, 00-preflight.md written; GitHub App OAuth creds not yet seeded (human operator action required)**

## What Happened

Ran four automated pre-flight checks against the local dev environment:

1. **Alembic migration head**: `cd backend && uv run alembic current` returned `s17_github_user_oauth_tokens (head)`. The test DB (perpetuity-test-db, port 55432) has the migration applied and the `github_user_oauth_tokens` table exists. The compose DB (perpetuity-db-1, port 5432) has schema `z3m_case_contact_updated_idx` — a different migration chain. In this dev setup the backend connects to the test DB at 55432, so the `alembic current` result is authoritative.

2. **Backend health**: `curl http://localhost:8000/api/v1/utils/health-check/` → `true`. The task plan referenced `GET /api/v1/health` which doesn't exist; the actual route is `/api/v1/utils/health-check/`. Backend is a local python3 process, not a compose service.

3. **Orchestrator health**: `docker exec perpetuity-orchestrator-1 curl http://localhost:8001/v1/health` → `{"status":"ok","image_present":true}`. Orchestrator is in compose but its port 8001 is not published to the host; the exec approach is correct.

4. **Compose stack**: `docker compose ps` shows 3 services healthy (db, orchestrator, redis). Backend and frontend run as local processes (port 8000, 5173). All five M006-relevant services are reachable.

5. **GitHub App configuration**: `system_settings` table is empty — no GitHub App credentials have been seeded. This is an expected state before a human operator completes the runbook steps. The 00-preflight.md documents what the operator must do before T02 can proceed.

Evidence directory created at `.gsd/milestones/M006-ydo2ce/evidence/`. 00-preflight.md written with all four checks, results, and operator instructions. Screenshot `00-preflight-github-app.png` cannot be captured automatically — requires human browser action on github.com.

## Verification

Ran verification gate: `test -f .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md && grep -q s17_github_user_oauth_tokens .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md` → exit 0 (PASS). Also verified individually: alembic current, backend health, orchestrator health, compose ps.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -f .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md && grep -q s17_github_user_oauth_tokens .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md` | 0 | PASS | 40ms |
| 2 | `cd backend && uv run alembic current 2>&1 | grep -v UserWarning | grep -v warnings.warn` | 0 | PASS — s17_github_user_oauth_tokens (head) | 2100ms |
| 3 | `curl -s http://localhost:8000/api/v1/utils/health-check/` | 0 | PASS — returned true | 85ms |
| 4 | `docker exec perpetuity-orchestrator-1 curl -s http://localhost:8001/v1/health` | 0 | PASS — {"status":"ok","image_present":true} | 320ms |
| 5 | `docker compose ps 2>&1 | grep -E '(db|orchestrator|redis)'` | 0 | PASS — 3 compose services healthy; backend+frontend running as local processes | 1100ms |
| 6 | `docker exec perpetuity-test-db psql -U postgres -d app -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name='github_user_oauth_tokens'"` | 0 | PASS — 1 row returned | 280ms |

## Deviations

Task plan stated 'Hit GET /api/v1/health on backend' — that route does not exist. Actual backend health route is GET /api/v1/utils/health-check/. Adapted without disrupting the slice contract. Task plan expected 'five services healthy' in compose — only 3 are in compose; backend and frontend are local processes. All five M006 services are reachable and healthy; documented in 00-preflight.md.

## Known Issues

GitHub App OAuth credentials (github_app_client_id, github_app_client_secret, github_app_slug) have NOT been seeded in system_settings. A human operator must complete steps 3-4 from docs/runbooks/m006-github-oauth-setup.md and save screenshot 00-preflight-github-app.png before T02 can begin. The compose stack's 'backend' and 'frontend' services are not running in compose — they run as local processes. This is acceptable for local UAT but T02-T04 operators should be aware.

## Files Created/Modified

- `/.gsd/milestones/M006-ydo2ce/evidence/00-preflight.md`
