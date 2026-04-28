# S03: Team-mirror container + lifecycle (spin-up, reap, always-on toggle) — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-26T03:23:41.060Z

# S03 UAT — Team-Mirror Container Lifecycle

**Goal:** Prove that per-team git-mirror containers can be spun up idempotently, reached via git daemon on 9418 from sibling containers, reaped after idle, and bypassed by an admin always_on toggle — entirely against the live compose stack.

## Preconditions

1. Compose stack running: `docker compose up -d db redis` (db healthy on 5432, redis healthy).
2. Images built: `docker compose build backend orchestrator` (backend includes alembic revision `s06c_team_mirror_volumes`; orchestrator includes `orchestrator/team_mirror.py`).
3. `alpine/git:latest` image pulled (`docker pull alpine/git:latest`).
4. `perpetuity_default` compose network exists.
5. `team_mirror_volumes` table empty and no `team-mirror-*` containers running before the run (autouse fixture wipes both before and after — belt-and-suspenders).
6. `mirror_idle_timeout_seconds` either unset or set to 60 (validator floor).
7. `MIRROR_REAPER_INTERVAL_SECONDS=1` exported for the ephemeral orchestrator boot so reap windows stay under 4s.

## Scenario A — Cold-start ensure spins up a mirror

1. Admin signs up, creates a team, gets the team UUID.
2. POST `/v1/teams/{team_id}/mirror/ensure` against the ephemeral orchestrator with the shared-secret header.
   - **Expected:** 200 with body `{container_id: "<12-hex>", network_addr: "team-mirror-<first8-team>:9418", reused: false}`.
3. Verify DB: `SELECT team_id, container_id, last_started_at, last_idle_at, volume_path, always_on FROM team_mirror_volumes WHERE team_id = '<team_id>'`.
   - **Expected:** exactly one row; container_id non-NULL and matches step 2; last_started_at and last_idle_at within the last 5s; volume_path = `perpetuity-team-mirror-<first8-team>`; always_on = false.
4. Verify Docker: `docker ps --filter label=team_id=<team_id> --filter label=perpetuity.team_mirror=true`.
   - **Expected:** exactly one running container named `team-mirror-<first8-team>` with the workspace image and labels `perpetuity.managed=true`, `perpetuity.team_mirror=true`, `team_id=<team_id>`.
5. Verify orchestrator log: search for `team_mirror_started team_id=<team_id> container_id=<12> network_addr=team-mirror-<first8>:9418 trigger=ensure`.
   - **Expected:** present.

## Scenario B — Second ensure is idempotent

1. POST the same `/v1/teams/{team_id}/mirror/ensure` again.
   - **Expected:** 200 with body `{container_id: "<same as A>", network_addr: "team-mirror-<first8>:9418", reused: true}`.
2. Verify Docker: `docker ps --filter label=team_id=<team_id>` shows exactly one container; no second container created.
3. Verify DB: `last_idle_at` was bumped by Scenario B's call (warm path also updates it — without this, an admin-triggered ensure against a running mirror could be reaped between the next tick and a user clone).
4. Verify orchestrator log: `team_mirror_reused team_id=<team_id> container_id=<12>` present.

## Scenario C — Sibling git clone over 9418

1. `docker exec <team-mirror-container> git init --bare /repos/test.git`.
2. Spawn an alpine/git sibling on `perpetuity_default`: `docker run --rm --network=perpetuity_default alpine/git clone git://team-mirror-<first8>:9418/test.git /tmp/c`.
   - **Expected:** exit 0.
3. Spawn a second alpine/git sibling that clones into `/tmp/c2` and runs `test -f /tmp/c2/.git/HEAD && echo HEAD_OK`.
   - **Expected:** exit 0; stdout contains `HEAD_OK`.
4. **Edge case:** Repeat the clone with a non-existent repo (`git clone git://team-mirror-<first8>:9418/nope.git /tmp/x`).
   - **Expected:** exit non-zero (git daemon refuses unknown paths, `--export-all` only applies to existing bare repos).

## Scenario D — Always_on bypasses reap

1. Backend PATCH `/api/v1/teams/{team_id}/mirror` with body `{always_on: true}` as the team admin.
   - **Expected:** 200 with body `{team_id, volume_path, container_id, last_started_at, last_idle_at, always_on: true}`; row updated.
2. Back-date `team_mirror_volumes.last_idle_at` by 120 seconds (well past the 60s `mirror_idle_timeout_seconds` floor): `UPDATE team_mirror_volumes SET last_idle_at = NOW() - INTERVAL '120 seconds' WHERE team_id = '<team_id>'`.
3. Sleep 2× `MIRROR_REAPER_INTERVAL_SECONDS` (= 2s) to give the reaper at least one full tick.
4. Verify Docker: container is STILL running (`docker ps --filter name=team-mirror-<first8>` non-empty).
5. Verify orchestrator log: `team_mirror_reap_skipped team_id=<team_id> reason=always_on` is present (full substring — bare token alone is insufficient because `reason=recent_activity` may also fire, MEM271).
6. Verify backend log: `team_mirror_always_on_toggled team_id=<team_id> actor_id=<actor> always_on=true created_row=false`.

## Scenario E — Re-enable reap by flipping always_on=false

1. Backend PATCH `/api/v1/teams/{team_id}/mirror` with body `{always_on: false}`.
   - **Expected:** 200; row updated; the back-dated `last_idle_at` is still in place (T03 only updates always_on).
2. Sleep 2× `MIRROR_REAPER_INTERVAL_SECONDS` (= 2s).
3. Verify Docker: container is GONE (`docker ps --filter name=team-mirror-<first8>` empty; `docker inspect <container_id>` exits non-zero).
4. Verify DB: `team_mirror_volumes.container_id IS NULL` AND `volume_path` persists (durability invariant — reap NEVER removes the volume).
5. Verify orchestrator log: both `team_mirror_reaped team_id=<team_id> container_id=<12> reason=idle` and `mirror_idle_timeout_seconds_resolved value=60` are present.

## Negative tests

| # | Action | Expected |
|---|--------|----------|
| 1 | POST `/v1/teams/not-a-uuid/mirror/ensure` | 422 (pydantic UUID coercion) |
| 2 | POST `/v1/teams/<unknown_team>/mirror/reap` | 200 with `{reaped: false}` (idempotent no-op; mirrors user-session reaper's 404 race handling) |
| 3 | PATCH `/api/v1/teams/<team_id>/mirror` with `{}` | 422 (missing `always_on`) |
| 4 | PATCH with `{always_on: 'maybe'}` | 422 (pydantic v2 lax-bool would coerce 'yes' silently — see MEM272 — so the test uses 'maybe' which is genuinely rejected) |
| 5 | PATCH `/api/v1/teams/not-a-uuid/mirror` with `{always_on: true}` | 422 (invalid path uuid) |
| 6 | PATCH as a non-admin team member | 403 |
| 7 | PATCH as a non-member of the team | 403 |
| 8 | PATCH against a team that doesn't exist | 404 (does NOT auto-create a row for a non-existent team — auto-create only runs after team-existence is asserted) |
| 9 | PUT `mirror_idle_timeout_seconds=59` against `/api/v1/admin/settings/mirror_idle_timeout_seconds` | 422 (below floor — sub-60s would weaponize the reaper into per-tick teardown) |
| 10 | PUT `mirror_idle_timeout_seconds=86401` | 422 (above cap) |
| 11 | PUT `mirror_idle_timeout_seconds=true` | 422 (bool rejected; despite pydantic lax-bool, the validator explicitly rejects bool before int coercion) |
| 12 | First PATCH for a team that has never spun up a mirror | 200 with auto-created row, `volume_path='pending:<team_id>'`, `created_row=true` in the log line |
| 13 | Reap a mirror, then ensure again | New cold-start container, `last_started_at` refreshed, `volume_path` is the SAME named volume (proves durability invariant) |
| 14 | Reaper tick when pg unreachable | Tick logs WARNING `team_mirror_reaper_tick_failed reason=<class>`, loop continues; next successful tick proceeds normally |
| 15 | Reaper tick when docker handle is None during boot (SKIP_IMAGE_PULL_ON_BOOT=1) | Tick skipped with structured WARNING; no exception escapes |
| 16 | `docker stop` the team-mirror container manually mid-test | Next reaper tick is a benign no-op (container_id stale; `containers.get` 404 race treated as already-gone, mirrors user-session reaper) |

## Cleanup

1. Stop ephemeral orchestrator container.
2. Wipe `team_mirror_volumes` rows.
3. Stop and remove all `team-mirror-*` containers.
4. Remove all `perpetuity-team-mirror-*` named volumes.
5. Reset `mirror_idle_timeout_seconds` to default (1800) or unset.

## Pass criteria

- All five scenarios (A–E) pass with the asserted state transitions and log markers.
- All sixteen negative tests pass with the documented expected outcomes.
- The six required log markers (`team_mirror_started`, `team_mirror_reused`, `team_mirror_reaped reason=idle`, `team_mirror_reap_skipped reason=always_on`, `mirror_idle_timeout_seconds_resolved value=60`, `team_mirror_always_on_toggled`) all appear across the orchestrator + backend logs in a single test run.
- WARNING `team_mirror_reaper_tick_failed` does NOT appear during the happy-path scenarios.
- Final wall-clock ≤30s on a warm compose stack (target 17s; budget 240s).
