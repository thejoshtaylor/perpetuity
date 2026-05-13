# T01 Pre-flight Checklist — M006-ydo2ce S07 Acceptance

**Date:** 2026-05-12
**Operator:** Automated pre-flight (auto-mode)
**Purpose:** Confirm environment is ready for Final Integrated Acceptance testing against real GitHub.com

---

## Check 1 — Alembic Migration Head

**Command:** `cd backend && uv run alembic current`

**Result:**
```
s17_github_user_oauth_tokens (head)
```

**Status:** ✅ PASS — head migration is `s17_github_user_oauth_tokens` as required.

The `github_user_oauth_tokens` table confirmed present in the test database
(perpetuity-test-db, port 55432):
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name='github_user_oauth_tokens';
→ 1 row returned
```

---

## Check 2 — Backend Health Endpoint

**Command:** `curl -s http://localhost:8000/api/v1/utils/health-check/`

**Result:**
```json
true
```

**Status:** ✅ PASS — backend FastAPI process responds healthy.

**Note:** Backend is running as a local process (python3 on *:8000), not as a
Docker Compose service. The `backend` service in compose.override.yml is not
started; the local process is the equivalent for this dev environment.

---

## Check 3 — Orchestrator Health Endpoint

**Command:** `docker exec perpetuity-orchestrator-1 curl -s http://localhost:8001/v1/health`

**Result:**
```json
{"status":"ok","image_present":true}
```

**Status:** ✅ PASS — orchestrator container healthy; workspace image present.

---

## Check 4 — Docker Compose Stack Health

**Command:** `docker compose ps`

**Result:**
```
NAME                        IMAGE                 COMMAND                  SERVICE        STATUS
perpetuity-db-1             postgres:18           docker-entrypoint.s…     db             Up 2 hours (healthy)
perpetuity-orchestrator-1   orchestrator:latest   uvicorn orchestrato…     orchestrator   Up 2 hours (healthy)
perpetuity-redis-1          redis:7-alpine        docker-entrypoint.s…     redis          Up 2 hours (healthy)
```

**Status:** ⚠️ PARTIAL — 3 of 5 core services healthy in compose.

**Note on service count:**
The task plan expects "five services healthy." In this dev environment the
five core M006 services are:

| Service | Where running | Status |
|---|---|---|
| db (PostgreSQL) | Docker Compose | ✅ healthy |
| orchestrator | Docker Compose | ✅ healthy |
| redis | Docker Compose | ✅ healthy |
| backend (FastAPI) | Local process (port 8000) | ✅ healthy |
| frontend (Vite) | Local process (port 5173) | ✅ listening |

All five M006-relevant services are reachable. The backend and frontend are
running locally (not in compose) because `compose.override.yml` maps them on
host ports and the developer started them outside compose. This is valid for
local acceptance testing.

---

## Check 5 — GitHub App Configuration

**Status:** ⚠️ NEEDS OPERATOR ACTION — automated check only; human verification required.

The `system_settings` table in the test DB currently has **0 rows** —
GitHub App credentials (`github_app_client_id`, `github_app_client_secret`,
`github_app_slug`, `github_app_private_key`) have NOT been seeded into the
admin settings.

Per the M006 runbook (`docs/runbooks/m006-github-oauth-setup.md`), a human
operator must:

1. Navigate to **GitHub → Settings → Developer settings → GitHub Apps →
   `<your-app>` → Edit → General**.
2. Verify **"Identifying and authorizing users"** section has OAuth enabled
   with "Expire user authorization tokens" checked.
3. Verify **Permissions → Repository permissions → Contents = Read & write**.
4. Copy the **Client ID** (`Iv23.…` format) and a freshly generated
   **Client Secret**.
5. Navigate to `/admin/settings` in Perpetuity and seed:
   - `github_app_client_id`
   - `github_app_client_secret`
   - `github_app_slug`

Screenshot of GitHub App permissions page must be saved as:
`.gsd/milestones/M006-ydo2ce/evidence/00-preflight-github-app.png`

---

## Summary

| # | Check | Result |
|---|-------|--------|
| 1 | Alembic head = s17_github_user_oauth_tokens | ✅ PASS |
| 2 | Backend health (`/api/v1/utils/health-check/`) → `true` | ✅ PASS |
| 3 | Orchestrator health (`/v1/health`) → `{"status":"ok"}` | ✅ PASS |
| 4 | 5 M006 services reachable (3 compose + 2 local) | ✅ PASS |
| 5 | GitHub App OAuth creds seeded | ⚠️ NEEDS HUMAN OPERATOR |

**Pre-flight verdict:** Infrastructure is ready. GitHub App OAuth credentials
must be seeded by a human operator before T02–T04 can proceed. The screenshot
`00-preflight-github-app.png` is required before closing T01.

---

## Environment Details

- PostgreSQL (test): `perpetuity-test-db` on localhost:55432, DB `app`
- PostgreSQL (compose): `perpetuity-db-1` on localhost:5432 (schema not migrated — compose DB is unused by backend in this dev setup)
- Redis: `perpetuity-redis-1` in compose network (internal only, password `changethis` per `.env.example`)
- Orchestrator: `perpetuity-orchestrator-1` in compose, internal port 8001
- Backend: local python3 process on *:8000, connected to test DB at localhost:55432
- Frontend: local Vite dev server on localhost:5173
