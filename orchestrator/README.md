# orchestrator

Per-(user, team) container provisioning + tmux-durable terminal sessions for
M002. The orchestrator owns the Docker socket (D005), runs FastAPI on
`:8001`, and is internal-only on the compose network — backend reaches it as
`http://orchestrator:8001`, never via the host (MEM101).

## Layout

- `orchestrator/` — FastAPI app, auth, sessions, WS bridge, Redis client.
- `workspace-image/` — Dockerfile for the per-user workspace image
  (`perpetuity/workspace:latest`).
- `tests/fixtures/Dockerfile.test` — slim variant tagged
  `perpetuity/workspace:test`, used by the integration suites.
- `tests/` — orchestrator-side integration tests (run inside a sibling
  container, see MEM104).

## Building

```bash
# Production-shape workspace image
docker build -f orchestrator/workspace-image/Dockerfile \
    -t perpetuity/workspace:latest orchestrator/workspace-image/

# Slim test variant (faster cold start; used by e2e)
docker build -f orchestrator/tests/fixtures/Dockerfile.test \
    -t perpetuity/workspace:test orchestrator/workspace-image/

# Orchestrator + backend service images
docker compose build orchestrator backend
```

## Running locally

```bash
docker compose up -d db redis orchestrator
curl -fsS http://orchestrator:8001/v1/health   # only reachable from inside the network
```

## End-to-end test (M002 / S01 / T06)

The acceptance test stitches signup → session create → echo hello →
orchestrator restart → reattach with same shell PID → log redaction sweep
together against the live compose stack.

```bash
# 1. Build the images this test needs
docker compose build orchestrator backend
docker build -f orchestrator/tests/fixtures/Dockerfile.test \
    -t perpetuity/workspace:test orchestrator/workspace-image/

# 2. Bring up the supporting services (the test boots its own backend)
docker compose up -d db redis orchestrator

# 3. Run the test (auto-skips if docker is unreachable)
cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v
```

Set `SKIP_INTEGRATION=1` to skip the suite without touching docker. The
test fixture spawns a fresh sibling backend container on
`perpetuity_default` with a published host port, so `docker compose
restart orchestrator` mid-test exercises the durability invariant
without disrupting the compose `backend` service (which the test does
not use).

## Observability taxonomy (S01)

INFO: `session_created`, `session_attached`, `session_detached`,
`image_pull_ok`, `container_provisioned`.
WARNING: `redis_unreachable`, `docker_unreachable`,
`tmux_session_orphaned`.
ERROR: `image_pull_failed` (boot blocker), `orchestrator_ws_unauthorized`.

All log lines that include user/team/session/container identifiers MUST
emit UUIDs only — never email, full_name, or team slug. The T06 e2e
test contains a regression sweep that fails the build if the seeded
test user's email or full_name appears in `docker compose logs orchestrator backend`.
