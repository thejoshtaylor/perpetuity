# S03: Team-mirror container + lifecycle (spin-up, reap, always-on toggle)

**Goal:** Stand up per-team mirror containers (one per team, same workspace image as user containers) running `git daemon --base-path=/repos --export-all --reuseaddr --enable=receive-pack` on port 9418, with an idempotent ensure-spinup path and a separate idle-reaper that respects a per-team `always_on` opt-out — the credential-free transport substrate every M004 git-op (S04 clone+push, S05 webhook-driven dispatch, S07 acceptance) sits on.
**Demo:** POST /v1/teams/{id}/mirror/ensure returns {network_addr: 'team-mirror-<id>:9418'}; idempotent on second call. A sibling test container can git clone git://team-mirror-<id>:9418/test.git after a fixture bare repo is dropped into /repos/test.git. Reaper kills the container after mirror_idle_timeout_seconds of inactivity (verified by log line team_mirror_reaped reason=idle). Team admin PATCH /api/v1/teams/{id}/mirror with always_on=true suppresses reap on the next reaper tick.

## Must-Haves

- POST /v1/teams/{team_id}/mirror/ensure on cold-start returns {container_id, network_addr: 'team-mirror-<first8-team>:9418'} and creates a `team_mirror_volumes` row; second call within idle window is idempotent (returns same network_addr, no new container).
- A sibling container on `perpetuity_default` can `git clone git://team-mirror-<first8-team>:9418/test.git` after a fixture bare repo is dropped into the mirror's `/repos/test.git` (proves git daemon is wired correctly).
- Reaper kills the mirror container after `mirror_idle_timeout_seconds` of inactivity (verified by `team_mirror_reaped reason=idle` INFO line); the `team_mirror_volumes` row's volume_path persists (only `container_id` is nulled).
- Team admin PATCH /api/v1/teams/{team_id}/mirror with `{always_on: true}` flips the row's `always_on` flag; the next reaper tick logs `team_mirror_reap_skipped reason=always_on` and leaves the container running past the idle deadline; flipping back to false re-enables idle reap.

## Proof Level

- This slice proves: - This slice proves: integration (real Docker daemon spawning a mirror container, real git daemon binding 9418 inside it, real reaper task ticking against state held in `team_mirror_volumes`).
- Real runtime required: yes (Docker daemon, Postgres for `team_mirror_volumes`, ephemeral orchestrator running the reaper loop, sibling container for the git-clone-over-9418 proof).
- Human/UAT required: no (S07 covers the real-GitHub UAT).

## Integration Closure

- Upstream surfaces consumed: M002 patterns from `orchestrator/orchestrator/sessions.py` (`_find_container_by_labels`, `_parse_mem_limit`, container-create-or-replace shape, `perpetuity.managed=true` umbrella label) and `orchestrator/orchestrator/reaper.py` (loop-with-try/except, asyncio.Task lifecycle, stop_reaper teardown order); `orchestrator/orchestrator/volume_store.py` asyncpg pool + `_resolve_*_seconds` settings-fallback pattern; `backend/app/api/team_access.py::assert_caller_is_team_admin`; `orchestrator/orchestrator/auth.py::SharedSecretMiddleware` (already covers the new `/v1/teams/...` prefix — no middleware change needed).
- New wiring introduced in this slice: `orchestrator/orchestrator/team_mirror.py`, `orchestrator/orchestrator/team_mirror_reaper.py`, `orchestrator/orchestrator/routes_team_mirror.py`, lifespan wiring in `orchestrator/orchestrator/main.py`, backend `app/api/routes/teams.py` PATCH endpoint, alembic revision `s06c_team_mirror_volumes`, registration of `mirror_idle_timeout_seconds` in admin `_VALIDATORS`.
- What remains before the milestone is truly usable end-to-end: S04 wires the orchestrator's GitHub-token-on-exec clone path into the mirror containers we ship here; S05 lights up the webhook receiver; S06 wires the always-on toggle into the team settings UI; S07 proves the loop end-to-end against a real GitHub test org.

## Verification

- Runtime signals: INFO `team_mirror_started team_id=<uuid> container_id=<12> network_addr=team-mirror-<first8>:9418 trigger=<ensure>`; INFO `team_mirror_reused team_id=<uuid> container_id=<12>`; INFO `team_mirror_reaped team_id=<uuid> container_id=<12> reason=<idle|admin>`; INFO `team_mirror_reap_skipped team_id=<uuid> reason=<always_on|recent_activity>`; INFO `mirror_idle_timeout_seconds_resolved value=<n>`; INFO `team_mirror_always_on_toggled team_id=<uuid> actor_id=<uuid> always_on=<bool>`; WARNING `team_mirror_reaper_tick_failed reason=<class>`.
- Inspection surfaces: `psql -c "SELECT team_id, volume_path, container_id, last_idle_at, always_on FROM team_mirror_volumes"`; `docker ps --filter label=perpetuity.team_mirror=true`.
- Failure visibility: 503 `docker_unavailable` / `workspace_volume_store_unavailable` from ensure (existing handlers); reaper failures swallowed and logged per-tick (matches user-session reaper); team_mirror row stays as the durable state of record.
- Redaction constraints: GitHub tokens DO NOT enter this slice — credentials land in S04's clone/push paths. Team UUIDs are log-safe. `volume_path` is uuid-keyed by construction.

## Tasks

- [x] **T01: Add team_mirror_volumes table + SQLModel + mirror_idle_timeout_seconds registered setting** `est:1h`
  Lay the schema and registry that the orchestrator's ensure/reap and the backend PATCH endpoint will both read from. Adds the `team_mirror_volumes` table (one row per team, durable through reap), the matching SQLModel + public projection, and registers a new `mirror_idle_timeout_seconds` system_settings key with bounds [60, 86400] and default 1800 in the admin _VALIDATORS registry. Mirrors the S06b migration shape (uuid PK, team FK ON DELETE CASCADE, idempotent upgrade + reversible downgrade) and the existing `_validate_idle_timeout_seconds` validator pattern in `backend/app/api/routes/admin.py`.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (alembic upgrade) | propagate (boot blocker — caught by prestart) | propagate | N/A |
| system_settings PUT validator | 422 invalid_value_for_key (must be int in 60..86400) | N/A | 422 |

## Load Profile

- Shared resources: none (one row per team, low cardinality; PK lookups only).
- Per-operation cost: 1 INSERT/UPDATE/SELECT per ensure or reap call.
- 10x breakpoint: N/A — table stays in the low hundreds of rows for the foreseeable scale.

## Negative Tests

- Malformed inputs: `mirror_idle_timeout_seconds` validator rejects bool, str, float, 0, 59, 86401 → 422.
- Boundary conditions: 60 and 86400 accepted; UNIQUE constraint on `team_id` rejects a second row for the same team; FK CASCADE on parent team delete drops the row.
- Migration: downgrade then re-upgrade leaves schema byte-identical (mirrors test_s06b round-trip).

## Observability Impact

- Signals added/changed: alembic logs `S06c migration: created team_mirror_volumes` on upgrade and `S06c downgrade: dropped team_mirror_volumes` on downgrade.
- How a future agent inspects this: `psql -c "\d team_mirror_volumes"`; `psql -c "SELECT key, value FROM system_settings WHERE key='mirror_idle_timeout_seconds'"`.
- Failure state exposed: schema absence shows as KeyError on any orchestrator ensure call surfacing 503 `workspace_volume_store_unavailable` (existing handler).
  - Files: `backend/app/alembic/versions/s06c_team_mirror_volumes.py`, `backend/app/models.py`, `backend/app/api/routes/admin.py`, `backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py`, `backend/tests/api/routes/test_admin_settings.py`
  - Verify: cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06c_team_mirror_volumes_migration.py tests/api/routes/test_admin_settings.py -v -k 'mirror_idle or s06c'

- [x] **T02: Ship orchestrator team_mirror module + reaper loop + HTTP routes (POST /v1/teams/{id}/mirror/{ensure,reap})** `est:3h`
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
  - Files: `orchestrator/orchestrator/team_mirror.py`, `orchestrator/orchestrator/team_mirror_reaper.py`, `orchestrator/orchestrator/routes_team_mirror.py`, `orchestrator/orchestrator/main.py`, `orchestrator/orchestrator/config.py`, `orchestrator/tests/unit/test_team_mirror.py`, `orchestrator/tests/unit/test_team_mirror_reaper.py`
  - Verify: cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_team_mirror.py tests/unit/test_team_mirror_reaper.py -v

- [x] **T03: Add backend PATCH /api/v1/teams/{team_id}/mirror always_on toggle (team-admin gated)** `est:1h`
  Thin team-admin endpoint that flips the `always_on` flag on the team's `team_mirror_volumes` row. Backend does NOT call the orchestrator — the toggle just biases the next reaper tick which reads the row directly. Auto-creates the row with always_on=<requested> on first PATCH if no row exists yet (so an admin can pre-toggle a team that has never spun up a mirror), using a placeholder volume_path='pending:<team_id>' that the orchestrator's ensure path replaces on first cold-start. Mirrors the team_access + admin-gated PATCH shape used by the existing teams routes (MEM047/MEM115). Returns the updated TeamMirrorVolumePublic.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres | propagate (FastAPI default 500) | propagate | N/A |
| assert_caller_is_team_admin | 404 team missing / 403 not admin | N/A | N/A |

## Load Profile

- Shared resources: backend SQLModel session (per-request).
- Per-operation cost: 1 SELECT + 1 INSERT/UPDATE.
- 10x breakpoint: N/A — admin-only mutation, low frequency.

## Negative Tests

- Malformed inputs: PATCH body missing `always_on` → 422 (pydantic); PATCH `{always_on: 'yes'}` → 422; PATCH path with invalid uuid → 422.
- Error paths: non-admin caller → 403; non-member caller → 403; missing team → 404 (does not auto-create row for a team that doesn't exist).
- Boundary conditions: PATCH twice with same value is idempotent (200, no warning); PATCH on team that has no mirror row yet auto-inserts with placeholder volume_path='pending:<team_id>'.

## Observability Impact

- Signals added/changed: INFO `team_mirror_always_on_toggled team_id=<uuid> actor_id=<uuid> always_on=<bool> created_row=<bool>`.
- How a future agent inspects this: `psql -c 'SELECT team_id, always_on FROM team_mirror_volumes WHERE team_id=...'`; backend access log shows the PATCH.
- Failure state exposed: 403 / 404 are the audit trail; no orphan-row state because the auto-insert is gated by team-existence.
  - Files: `backend/app/api/routes/teams.py`, `backend/app/models.py`, `backend/tests/api/routes/test_teams_mirror.py`
  - Verify: cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams_mirror.py -v

- [x] **T04: End-to-end proof: ensure idempotency + git-clone-over-9418 + reap + always_on toggle bypass against live compose stack** `est:3h`
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
  - Files: `backend/tests/integration/test_m004_s03_team_mirror_e2e.py`
  - Verify: cd /Users/josh/code/perpetuity && docker compose build backend orchestrator && docker compose up -d db redis && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s03_team_mirror_e2e.py -v

## Files Likely Touched

- backend/app/alembic/versions/s06c_team_mirror_volumes.py
- backend/app/models.py
- backend/app/api/routes/admin.py
- backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py
- backend/tests/api/routes/test_admin_settings.py
- orchestrator/orchestrator/team_mirror.py
- orchestrator/orchestrator/team_mirror_reaper.py
- orchestrator/orchestrator/routes_team_mirror.py
- orchestrator/orchestrator/main.py
- orchestrator/orchestrator/config.py
- orchestrator/tests/unit/test_team_mirror.py
- orchestrator/tests/unit/test_team_mirror_reaper.py
- backend/app/api/routes/teams.py
- backend/tests/api/routes/test_teams_mirror.py
- backend/tests/integration/test_m004_s03_team_mirror_e2e.py
