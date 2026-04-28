---
estimated_steps: 20
estimated_files: 7
skills_used: []
---

# T02: Ship orchestrator team_mirror module + reaper loop + HTTP routes (POST /v1/teams/{id}/mirror/{ensure,reap})

The integration centerpiece. Adds `orchestrator/orchestrator/team_mirror.py` (idempotent ensure_team_mirror + reap_team_mirror), `orchestrator/orchestrator/team_mirror_reaper.py` (separate asyncio.Task — never killed by transient errors, respects always_on, resolves mirror_idle_timeout_seconds from system_settings on every tick), `orchestrator/orchestrator/routes_team_mirror.py` (POST /v1/teams/{id}/mirror/ensure + POST /v1/teams/{id}/mirror/reap, gated by the existing SharedSecretMiddleware), and lifespan wiring in `main.py` to start/stop the new reaper alongside the user-session reaper (teardown order: NEW reaper FIRST too, then user-session reaper, then registry/pg/docker — preserves the MEM190 ordering invariant for both reapers).

Container config: same workspace image as user containers (D022), labels `perpetuity.managed=true` + `perpetuity.team_mirror=true` + `team_id=<uuid>`, name `team-mirror-<first8-team>` (DNS alias on `perpetuity_default`), command `git daemon --base-path=/repos --export-all --reuseaddr --enable=receive-pack` on port 9418 (D023). Per-team Docker volume named `perpetuity-team-mirror-<first8-team>` mounted at `/repos`. Idempotent ensure: if a running container with matching team_id label exists, reuse it; otherwise create_or_replace. The mirror reaper is structurally separate from the user-session reaper because their failure modes differ (D022 — reaping mid-fetch breaks user clone, mid-push breaks auto-push); they share no in-process state.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Docker daemon | DockerUnavailable → 503 docker_unavailable (existing handler) | 30s subprocess timeout → DockerUnavailable | N/A |
| Postgres pg_pool | WorkspaceVolumeStoreUnavailable → 503 (existing handler) | 5s pool command_timeout | N/A |
| Reaper tick | swallow + WARNING `team_mirror_reaper_tick_failed reason=<class>`; loop continues | swallow + log; loop continues | N/A |

## Load Profile

- Shared resources: aiodocker.Docker handle (single per process), asyncpg pool (size 5, shared with user-session reaper), Redis pool (NOT used by this slice — team_mirror keeps no Redis state).
- Per-operation cost: ensure cold-start = 1 SELECT + 1 INSERT/UPDATE + 1 docker create + 1 docker start (~500 ms total); ensure warm = 1 SELECT + 1 docker filter list (~50 ms); reaper tick = 1 SELECT scan over team_mirror_volumes + per-row docker stop+rm on reap (low cardinality, low cost).
- 10x breakpoint: docker daemon serialization on container create — fine for 100 teams, would need batching at 1000+; out of scope for M004.

## Negative Tests

- Malformed inputs: ensure with invalid uuid in path → 422 (pydantic); reap with unknown team_id → 200 no-op (idempotent); ensure when docker is unreachable → 503 docker_unavailable.
- Error paths: reaper tick with pg unreachable → swallow + log + next tick retries; container missing during reap (already-gone) → benign no-op (mirrors user-session reaper's 404 race handling, MEM190 cousin).
- Boundary conditions: ensure twice in fast succession with the same team_id → both return same container_id (concurrent-create 409 falls back to filter-list lookup, mirroring the user-session pattern in `provision_container`); reap-then-ensure → ensure cold-starts a fresh container, the row's volume_path persists.

## Observability Impact

- Signals added/changed: INFO `team_mirror_started team_id=<uuid> container_id=<12> network_addr=team-mirror-<first8>:9418 trigger=ensure`; INFO `team_mirror_reused team_id=<uuid> container_id=<12>`; INFO `team_mirror_reaped team_id=<uuid> container_id=<12> reason=<idle|admin>`; INFO `team_mirror_reap_skipped team_id=<uuid> reason=<always_on|recent_activity>`; INFO `mirror_idle_timeout_seconds_resolved value=<n>` (per tick); WARNING `team_mirror_reaper_tick_failed reason=<class>`; INFO `team_mirror_reaper_started interval_seconds=<n>` on lifespan startup.
- How a future agent inspects this: `docker ps --filter label=perpetuity.team_mirror=true`; `psql -c 'SELECT team_id, container_id, last_idle_at, always_on FROM team_mirror_volumes'`; `docker logs <orch> | grep team_mirror_`.
- Failure state exposed: a stuck reaper surfaces as repeated `team_mirror_reaper_tick_failed` lines without a matching `team_mirror_reaped` — same shape as the user-session reaper's failure visibility (MEM176/MEM190).

## Inputs

- ``orchestrator/orchestrator/sessions.py` — pattern for `_find_container_by_labels`, `_parse_mem_limit`, `_build_container_config`, create_or_replace + 409-fallback to filter-list lookup`
- ``orchestrator/orchestrator/reaper.py` — pattern for asyncio.Task loop, try/except-swallow per tick, `_resolve_*_seconds` settings fallback, stop_reaper teardown order (MEM190)`
- ``orchestrator/orchestrator/volume_store.py` — asyncpg pool reuse (`get_pool()`), `_resolve_idle_timeout_seconds` shape to mirror`
- ``orchestrator/orchestrator/main.py` — lifespan/teardown ordering; new reaper start/stop calls go alongside the existing reaper_task`
- ``orchestrator/orchestrator/auth.py` — confirm `_PUBLIC_PATHS` allowlist excludes the new `/v1/teams/...` prefix (no change needed)`
- ``orchestrator/orchestrator/config.py` — extend with `mirror_reaper_interval_seconds` (default 30, env-overridable for tests)`
- ``backend/app/alembic/versions/s06c_team_mirror_volumes.py` — column shape the orchestrator queries`

## Expected Output

- ``orchestrator/orchestrator/team_mirror.py` — `ensure_team_mirror(pool, docker, team_id) -> {container_id, network_addr}` (idempotent: SELECT row, find container by label, create-or-replace if missing, INSERT/UPDATE row); `reap_team_mirror(pool, docker, team_id, *, reason)` (stop+remove container, NULL container_id, set last_idle_at); `_team_mirror_container_name(team_id) -> str`; `_build_team_mirror_container_config(team_id, volume_name) -> dict` matching D022/D023`
- ``orchestrator/orchestrator/team_mirror_reaper.py` — `start_team_mirror_reaper(app)` + `stop_team_mirror_reaper(task)` + `_reap_one_tick(pool, docker)`; SELECT all team_mirror_volumes, for each: skip if always_on, skip if container_id NULL, skip if last_idle_at + mirror_idle_timeout_seconds > now(), else call reap_team_mirror; `_resolve_mirror_idle_timeout_seconds(pool)` mirrors volume_store helper; logs `mirror_idle_timeout_seconds_resolved value=<n>` once per tick`
- ``orchestrator/orchestrator/routes_team_mirror.py` — APIRouter prefix=/v1/teams; POST /{team_id}/mirror/ensure → 200 {container_id, network_addr}; POST /{team_id}/mirror/reap → 200 {reaped: bool} (admin force-reap); pydantic UUID validation`
- ``orchestrator/orchestrator/main.py` — import + include `routes_team_mirror.router`; in lifespan, start `app.state.team_mirror_reaper_task = start_team_mirror_reaper(app)` AFTER the user-session reaper start; in teardown, stop it FIRST so neither tick races shutdown — preserves MEM190`
- ``orchestrator/orchestrator/config.py` — add `mirror_reaper_interval_seconds: int = 30` setting`
- ``orchestrator/tests/unit/test_team_mirror.py` — ensure cold-start path inserts row + creates container with the right labels/cmd/volume mount; ensure warm path reuses existing container (mocks `_find_container_by_labels`); reap stops container and NULLs container_id; concurrent-create 409 falls back to filter list`
- ``orchestrator/tests/unit/test_team_mirror_reaper.py` — single-tick `_reap_one_tick`: skip-on-always_on, skip-on-recent-activity, reap-on-idle (asserts `team_mirror_reaped` log line), tolerate pg unreachable (asserts WARNING + no crash), `_resolve_mirror_idle_timeout_seconds` falls back to settings default when system_settings row is missing`

## Verification

cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_team_mirror.py tests/unit/test_team_mirror_reaper.py -v

## Observability Impact

Introduces six new structured INFO log keys (team_mirror_started, team_mirror_reused, team_mirror_reaped, team_mirror_reap_skipped, mirror_idle_timeout_seconds_resolved, team_mirror_reaper_started) and one WARNING (team_mirror_reaper_tick_failed). All include team_id (uuid) for redaction-safe correlation; no GitHub tokens, no PEMs flow through this module.
