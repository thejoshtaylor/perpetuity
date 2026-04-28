---
estimated_steps: 25
estimated_files: 1
skills_used: []
---

# T04: End-to-end proof: ensure idempotency + git-clone-over-9418 + reap + always_on toggle bypass against live compose stack

The slice's authoritative integration proof. Single test file, marked `@pytest.mark.e2e + @pytest.mark.serial`, against the live compose db + ephemeral orchestrator (parameterized with MIRROR_REAPER_INTERVAL_SECONDS=1 and `mirror_idle_timeout_seconds` set to 60 via system_settings — the validator floor — combined with manual back-dating of `last_idle_at` for the reap-bypass and reap windows so the test stays under 30s wall-clock) + sibling backend container. Reuses the MEM149/MEM117 swap pattern and the MEM194 docker-exec urllib readiness probe.

Scenarios A–E walk the slice contract end-to-end:

  A. Admin signup → create team → POST /v1/teams/{id}/mirror/ensure → 200 with {container_id, network_addr: 'team-mirror-<first8>:9418'}; assert `team_mirror_volumes` row inserted with non-NULL container_id; assert container is running with the expected labels; assert log line `team_mirror_started ... trigger=ensure`.

  B. Second POST /v1/teams/{id}/mirror/ensure → 200 same container_id (idempotent); assert no second container created; assert log line `team_mirror_reused`.

  C. Drop a fixture bare repo into the mirror's `/repos/test.git` via `docker exec <mirror> git init --bare /repos/test.git`; spawn a sibling alpine/git container on `perpetuity_default` and run `git clone git://team-mirror-<first8>:9418/test.git /tmp/clone`; assert exit 0 and `/tmp/clone/.git/HEAD` exists. (Proves D023 transport: git daemon binds 9418, --export-all is set, the compose-DNS alias resolves from siblings.)

  D. Backend PATCH /api/v1/teams/{id}/mirror with {always_on: true} as team-admin; back-date `team_mirror_volumes.last_idle_at` by 120 seconds (well past `mirror_idle_timeout_seconds=60`); sleep 2× reaper_interval seconds (= 2s); assert container is STILL running; assert log line `team_mirror_reap_skipped reason=always_on`.

  E. Backend PATCH again with {always_on: false}; the back-dated last_idle_at is still in place; sleep another 2× reaper_interval (= 2s); assert container is NOT running (`docker ps --filter name=team-mirror-...` empty); assert `team_mirror_volumes` row's container_id is NULL but volume_path persists; assert log lines `team_mirror_reaped reason=idle` and `mirror_idle_timeout_seconds_resolved value=60`.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Compose db / redis | pytest.skip with diagnostic | pytest.skip | N/A |
| Ephemeral orchestrator boot | pytest.fail with `docker logs` tail | pytest.fail | N/A |
| Sibling alpine/git boot | pytest.fail with logs tail | pytest.fail | N/A |
| Reaper tick | wait up to 2× reaper_interval before asserting | (subsumed) | N/A |

## Load Profile

- Shared resources: docker daemon, compose db, compose redis (untouched by this slice but reused for the orchestrator boot).
- Per-operation cost: ~25s wall-clock (orchestrator boot 8-12s, ensure 1s, sibling clone 3-5s, two reap windows ~4s with manual last_idle_at back-dating).
- 10x breakpoint: N/A — single-test e2e gated behind `@pytest.mark.e2e`.

## Negative Tests

- Cleanup robustness: autouse fixture wipes `team_mirror_volumes` rows + any `team-mirror-*` container before AND after the test (mirrors MEM246/S02 belt-and-suspenders pattern).
- Image skip-guards: probe backend:latest for `s06c_team_mirror_volumes.py` and orchestrator:latest for `team_mirror.py`; pytest.skip with a clear message if either is missing (preempts the MEM137 stale-image trap).

## Observability Impact

- Signals added/changed: the test asserts the structural presence of `team_mirror_started`, `team_mirror_reused`, `team_mirror_reaped reason=idle`, `team_mirror_reap_skipped reason=always_on`, `mirror_idle_timeout_seconds_resolved value=60`, `team_mirror_always_on_toggled` in the captured backend + orchestrator logs.
- How a future agent inspects this: pytest output shows the asserted log markers; on failure, the test dumps the last 80 lines of orchestrator + backend logs.
- Failure state exposed: any missing log marker or unexpected container state surfaces as a precise pytest assertion failure, not a generic timeout.

## Inputs

- ``backend/tests/integration/test_m004_s02_github_install_e2e.py` — pattern reference for ephemeral orchestrator + sibling backend + image skip-guards + autouse cleanup + log capture; copy the helper utilities (_docker, _compose, _free_port, _read_dotenv_value, _psql_one, _psql_exec) module-locally per MEM197`
- ``orchestrator/orchestrator/team_mirror.py` — module presence is the orchestrator skip-guard probe`
- ``backend/app/alembic/versions/s06c_team_mirror_volumes.py` — revision filename is the backend skip-guard probe`
- ``backend/app/api/routes/teams.py` — PATCH endpoint exercised in scenarios D + E`

## Expected Output

- ``backend/tests/integration/test_m004_s03_team_mirror_e2e.py` — single @pytest.mark.e2e @pytest.mark.serial test exercising scenarios A-E end-to-end against live compose db + ephemeral orchestrator (boot env: ORCHESTRATOR_API_KEY=<rand>, MIRROR_REAPER_INTERVAL_SECONDS=1, --network-alias orchestrator + cleanup of team-mirror-* containers before AND after); asserts: ensure-cold-start row + container, ensure-idempotent same container_id, sibling alpine/git clone exit 0 + .git/HEAD present, always_on=true bypasses reap, always_on=false re-enables reap and persists volume_path; final structural assertion of all six required log markers`

## Verification

cd /Users/josh/code/perpetuity && docker compose build backend orchestrator && docker compose up -d db redis && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s03_team_mirror_e2e.py -v

## Observability Impact

Test failure mode dumps the last 80 lines of orchestrator + backend logs so a future agent can diagnose without re-running. No new runtime signals introduced — this task only asserts on signals shipped by T01-T03.
