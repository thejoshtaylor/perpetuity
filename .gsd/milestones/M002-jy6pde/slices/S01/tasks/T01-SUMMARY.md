---
id: T01
parent: S01
milestone: M002-jy6pde
key_files:
  - docker-compose.yml
  - .env.example
  - orchestrator/pyproject.toml
  - orchestrator/Dockerfile
  - orchestrator/orchestrator/__init__.py
  - orchestrator/orchestrator/main.py
  - orchestrator/orchestrator/config.py
  - orchestrator/workspace-image/Dockerfile
  - orchestrator/tests/fixtures/Dockerfile.test
  - orchestrator/tests/unit/test_health.py
key_decisions:
  - Used FastAPI lifespan handler instead of deprecated @app.on_event for startup logging — quieter test output and forward-compatible.
  - Wired backend service to depends_on: orchestrator (service_healthy) so backend boot waits for orchestrator readiness — avoids start-order races when T05 adds HTTP calls.
  - Workspace `:test` image drops nodejs (and the NodeSource setup) to bound integration-test image-build cost; S01 only exercises tmux+bash so this is safe. Both images keep python3 because T03's tmux exec command surface uses bash/python utilities.
  - Added Postgres dep to orchestrator even though T01's app code doesn't read DB — task plan calls for it and it costs nothing in T01 while T03+ will query workspace_volume from Postgres on session create.
  - Used renamed stale db container instead of removing it — preserved any dev data the user may have had on the anonymous volume.
duration: 
verification_result: passed
completed_at: 2026-04-25T08:55:06.696Z
blocker_discovered: false
---

# T01: Land orchestrator + redis compose services and workspace image scaffold with passing /v1/health

**Land orchestrator + redis compose services and workspace image scaffold with passing /v1/health**

## What Happened

Built the M002 deployment plumbing so every later task in S01 has a real stack to attach to. Created the top-level `orchestrator/` package: standalone `pyproject.toml` (FastAPI + aiodocker + redis-asyncio + uvicorn; pytest/pytest-asyncio/ruff/mypy in dev), an `orchestrator/Dockerfile` mirroring the backend's uv-based pattern (python:3.12-slim, curl + libcap2-bin for the compose healthcheck and capsh verification), `orchestrator/orchestrator/__init__.py`, a minimal `main.py` that boots a FastAPI app with a `/v1/health` endpoint and emits `orchestrator_starting` (with redacted Redis password prefix per CONTEXT) + `orchestrator_ready` INFO logs via a lifespan handler, and a `config.py` that holds every tunable T02–T06 will consume (workspace_image, mem_limit=2g, pids_limit=512, nano_cpus=1_000_000_000, workspace_root, idle_timeout_seconds=900, scrollback_max_bytes=100KB, redis settings, ORCHESTRATOR_API_KEY/_PREVIOUS for two-key rotation).

Added two compose services in `docker-compose.yml`: `redis:7-alpine` (internal-only, `command: redis-server --requirepass ${REDIS_PASSWORD}`, `redis-cli -a ... ping` healthcheck) and `orchestrator` (built from `orchestrator/Dockerfile`, mounts `/var/run/docker.sock` — the only non-traefik service that does, per D005 — `cap_add: [SYS_ADMIN]` per D014, `depends_on: db+redis service_healthy`, `curl http://localhost:8001/v1/health` healthcheck). Wired `backend` to `depends_on: orchestrator service_healthy` and pass `ORCHESTRATOR_BASE_URL`/`ORCHESTRATOR_API_KEY` env (T05 will consume; values just need to plumb through now).

Created the workspace base image at `orchestrator/workspace-image/Dockerfile` (Ubuntu 24.04 + tmux/bash/git/curl/python3 + Node.js 22 from NodeSource, `CMD ["sleep","infinity"]` so T03 can `docker exec tmux new-session -d`) and the smaller `:test` variant at `orchestrator/tests/fixtures/Dockerfile.test` (drops nodejs to bound CI image-build cost — S01 doesn't exercise node). Wrote the required unit test `orchestrator/tests/unit/test_health.py` using `httpx.AsyncClient` + ASGITransport against the FastAPI app object — passes in 0.2s.

Updated `.env.example` with `REDIS_PASSWORD`, `ORCHESTRATOR_API_KEY`, `ORCHESTRATOR_API_KEY_PREVIOUS`, `ORCHESTRATOR_BASE_URL=http://orchestrator:8001`, `WORKSPACE_IMAGE=perpetuity/workspace:latest`, and `DOCKER_IMAGE_ORCHESTRATOR=orchestrator`. Mirrored those keys into the local `.env` so the verification compose-up succeeds.

Hit one local-dev gotcha (now MEM100): a stale `perpetuity-db-1` container from a prior dev session held the name. Rather than `docker rm` it (potentially destructive — it had its own anonymous volume), I renamed it `perpetuity-db-1-stale-pre-m002` so compose could create a fresh container bound to the named `app-db-data` volume. The user can rename back later if they want that data.

Built `orchestrator:latest`, `perpetuity/workspace:latest` (612MB), and `perpetuity/workspace:test` (308MB) locally. `docker compose up -d db redis orchestrator` brings all three up healthy on the first attempt; `/v1/health` returns `{"status":"ok"}` from inside the compose network; `capsh --print` inside the orchestrator confirms `cap_sys_admin` is granted; `/var/run/docker.sock` is present in orchestrator and absent from backend per the D005 boundary.

## Verification

Ran every verification command from the task plan against the live stack. (1) `docker compose up -d db redis orchestrator` → all three reached `healthy` status. (2) `curl -fsS http://orchestrator:8001/v1/health` from inside the compose network returned `{"status":"ok"}`. (3) `docker images | grep perpetuity/workspace` shows both `:latest` and `:test` tags. (4) `docker compose exec orchestrator capsh --print` shows `cap_sys_admin` in Current and Bounding sets. (5) `docker compose exec orchestrator ls /var/run/docker.sock` succeeds (`srw-rw---- 1 root root`). (6) Compose config inspection confirms backend has no `/var/run/docker.sock` mount (only orchestrator and the dev-only traefik proxy from compose.override.yml have it). (7) `pytest tests/unit/test_health.py` passes. (8) Orchestrator startup logs show `orchestrator_starting port=8001 redis_password=chan...` and `orchestrator_ready` — slice observability requirements (`orchestrator_starting`, `orchestrator_ready`, redacted password prefix) confirmed in `docker compose logs orchestrator`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose up -d db redis orchestrator && docker compose ps` | 0 | ✅ pass | 17000ms |
| 2 | `docker run --rm --network perpetuity_default curlimages/curl:latest -fsS http://orchestrator:8001/v1/health` | 0 | ✅ pass — returned {"status":"ok"} | 1200ms |
| 3 | `docker images | grep perpetuity/workspace` | 0 | ✅ pass — both :latest and :test tags present | 200ms |
| 4 | `docker compose exec -T orchestrator capsh --print | grep cap_sys_admin` | 0 | ✅ pass — cap_sys_admin in Current+Bounding | 600ms |
| 5 | `docker compose exec -T orchestrator ls -l /var/run/docker.sock` | 0 | ✅ pass — socket present in orchestrator | 500ms |
| 6 | `compose config inspection: backend has docker.sock=False, orchestrator has docker.sock=True` | 0 | ✅ pass — D005 boundary held | 300ms |
| 7 | `cd orchestrator && .venv/bin/pytest tests/unit/test_health.py -v` | 0 | ✅ pass — 1 passed in 0.20s | 200ms |
| 8 | `docker compose logs orchestrator | grep -E 'orchestrator_starting|orchestrator_ready'` | 0 | ✅ pass — both INFO lines emitted, redis_password redacted to chan... | 200ms |

## Deviations

"Minor: added 1 dev dep (`pytest-asyncio`) and runtime curl/libcap2-bin to the orchestrator Dockerfile that the planner didn't enumerate. Both are required to satisfy the plan's own verification (httpx.AsyncClient async test needs pytest-asyncio; compose healthcheck needs curl; capsh verification needs libcap2-bin). Also wired backend.depends_on to include orchestrator (service_healthy) — not in the task plan but coherent with making backend wait for the orchestrator readiness it will need from T05 onward."

## Known Issues

"Local-dev only: a pre-existing stale `perpetuity-db-1` container had to be renamed (now `perpetuity-db-1-stale-pre-m002`) before `docker compose up` would succeed. Captured as MEM100 so other agents/dev machines hit the same case know not to `docker rm` blindly. Compose `proxy` service from `compose.override.yml` (Traefik, dev-only) also mounts the docker socket — that predates M002 and is unrelated to D005's backend-vs-orchestrator boundary, but worth noting for any future security review."

## Files Created/Modified

- `docker-compose.yml`
- `.env.example`
- `orchestrator/pyproject.toml`
- `orchestrator/Dockerfile`
- `orchestrator/orchestrator/__init__.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/orchestrator/config.py`
- `orchestrator/workspace-image/Dockerfile`
- `orchestrator/tests/fixtures/Dockerfile.test`
- `orchestrator/tests/unit/test_health.py`
