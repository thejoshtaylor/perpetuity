---
estimated_steps: 1
estimated_files: 9
skills_used: []
---

# T01: Compose plumbing: redis + orchestrator services + workspace image scaffold

Land the deployment scaffolding so every later task has a real stack to talk to: add `redis:7-alpine` and `orchestrator` services to `docker-compose.yml`, create the `orchestrator/` top-level package with its own `pyproject.toml` and `Dockerfile`, create the workspace base image at `orchestrator/workspace-image/Dockerfile` with tmux + bash + git + node + python, and create the smaller `perpetuity/workspace:test` variant under `orchestrator/tests/fixtures/Dockerfile.test` for CI. Orchestrator service in compose mounts `/var/run/docker.sock` (orchestrator-only — backend never gets it, per D005), runs with `cap_add: [SYS_ADMIN]` (documented constraint from D014; required for losetup/mount in S02 but added now since changing compose privilege boundaries later is more disruptive than adding it once), and depends on db + redis being healthy. Redis is internal-network-only with `command: redis-server --requirepass ${REDIS_PASSWORD}` (env var added to `.env.example`). Add a healthcheck that pings the orchestrator on `:8001/v1/health` (the endpoint is created in T02 but the healthcheck shape is committed now). Do NOT yet implement orchestrator app code — this task is plumbing only; the placeholder `orchestrator/orchestrator/main.py` should be a minimal FastAPI app with a `/v1/health` endpoint returning `{status: 'ok'}` so the healthcheck passes. Keep the workspace base image small (Ubuntu 24.04 + apt-installed tmux/bash/git/curl/python3/nodejs from NodeSource) — a future milestone can split it. ASSUMPTION (auto-mode): orchestrator binds `0.0.0.0:8001`, exposed only on the compose internal network (no host port published); backend reaches it as `http://orchestrator:8001`. ASSUMPTION: nano_cpus default is 1_000_000_000 (1.0 vCPU) per container — not enforced in T01 but recorded in `orchestrator/orchestrator/config.py` for use by T03. Build both images locally with `docker compose build orchestrator` and `docker build -f orchestrator/workspace-image/Dockerfile -t perpetuity/workspace:latest orchestrator/workspace-image/`.

## Inputs

- ``docker-compose.yml``
- ``backend/Dockerfile``
- ``.gsd/milestones/M002-jy6pde/M002-jy6pde-CONTEXT.md``

## Expected Output

- ``docker-compose.yml` (modified — adds `redis` and `orchestrator` services)`
- ``orchestrator/pyproject.toml``
- ``orchestrator/Dockerfile``
- ``orchestrator/orchestrator/__init__.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/config.py``
- ``orchestrator/workspace-image/Dockerfile``
- ``orchestrator/tests/fixtures/Dockerfile.test``
- ``orchestrator/tests/unit/test_health.py``
- ``.env.example``

## Verification

Run `docker compose up -d db redis orchestrator` from repo root; assert `docker compose ps` shows all three healthy. Assert `curl -fsS http://localhost:<orchestrator-host-port-or-via-compose-exec>/v1/health` returns `{"status":"ok"}` — use `docker compose exec backend curl -fsS http://orchestrator:8001/v1/health` since orchestrator is internal-only. Assert `docker images | grep perpetuity/workspace` shows both `latest` and `test` tags after running the build commands above. Assert `docker compose exec orchestrator capsh --print | grep cap_sys_admin` shows the capability is granted. Assert `docker compose exec orchestrator ls /var/run/docker.sock` succeeds AND `docker compose exec backend ls /var/run/docker.sock` fails (D005 boundary). Add a unit test `orchestrator/tests/unit/test_health.py` that uses `httpx.AsyncClient` against the FastAPI app and asserts `/v1/health` returns 200.

## Observability Impact

Orchestrator boot logs `orchestrator_starting` (INFO) and `orchestrator_ready` (INFO) on healthcheck pass. Redis password is logged with redacted prefix only (first 4 chars + `...`). No PII in logs (no users in this task).
