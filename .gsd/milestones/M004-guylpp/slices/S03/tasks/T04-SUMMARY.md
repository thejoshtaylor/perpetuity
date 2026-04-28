---
id: T04
parent: S03
milestone: M004-guylpp
key_files:
  - backend/tests/integration/test_m004_s03_team_mirror_e2e.py
key_decisions:
  - Match on the full `team_mirror_reap_skipped team_id=<uuid> reason=always_on` substring rather than the bare token — the reaper may fire a `reason=recent_activity` skip BEFORE the test back-dates last_idle_at, and a shortcut-on-first-hit log waiter would falsely accept that line (captured as MEM267)
  - Reused the MEM149/MEM188 live-orchestrator-swap pattern wholesale rather than introducing a docker-compose.override.yml for MIRROR_REAPER_INTERVAL_SECONDS — keeps the test surface narrow and matches the convention every other M002/M004 e2e already follows
  - Belt-and-suspenders cleanup also wipes `perpetuity-team-mirror-*` named volumes before and after — without this, a prior test crash leaves the volume around and the next ensure mounts stale `/repos` content (would not have failed the test today, but defends against future scenario-bleed)
  - Pre-pulled alpine/git locally rather than letting first-test-run pay the pull latency — keeps the slice's e2e wall-clock budget honest
duration: 
verification_result: passed
completed_at: 2026-04-26T03:17:31.280Z
blocker_discovered: false
---

# T04: Add M004/S03 e2e proving ensure-cold-start, ensure-idempotent, sibling git-clone-over-9418, always_on bypass, and idle reap against the live compose stack

**Add M004/S03 e2e proving ensure-cold-start, ensure-idempotent, sibling git-clone-over-9418, always_on bypass, and idle reap against the live compose stack**

## What Happened

Wrote `backend/tests/integration/test_m004_s03_team_mirror_e2e.py` — single `@pytest.mark.e2e @pytest.mark.serial` test that walks scenarios A–E end-to-end against the live compose db + an ephemeral orchestrator (MIRROR_REAPER_INTERVAL_SECONDS=1) + a sibling backend, then a sibling alpine/git container for the transport proof.

Reused the S02 reference shape (MEM197 module-local helpers, MEM149/MEM188 live-orchestrator-swap with `--network-alias orchestrator`, MEM194 `docker exec ... python3 urllib` readiness probe, MEM260 in-container HTTP for the no-host-port orchestrator). Image skip-guards probe backend:latest for `s06c_team_mirror_volumes.py` and orchestrator:latest for `team_mirror.py` (preempts MEM137 stale-image trap). Belt-and-suspenders cleanup wipes `team_mirror_volumes` rows + the `mirror_idle_timeout_seconds` setting + every `team-mirror-*` container + every `perpetuity-team-mirror-*` named volume before AND after each test.

Scenario A (cold-start): POST /v1/teams/{id}/mirror/ensure → 200 with {container_id, network_addr: 'team-mirror-<first8>:9418', reused: false}. Asserts the team_mirror_volumes row landed with non-NULL container_id, the container is running with `perpetuity.team_mirror=true` label, and the orchestrator log shows `team_mirror_started`.

Scenario B (idempotent): second ensure returns the same container_id with reused=true; `docker ps --filter label=team_id=<uuid>` shows exactly one container; orchestrator log shows `team_mirror_reused`.

Scenario C (transport): `docker exec <mirror> git init --bare /repos/test.git` drops a bare repo into the named volume; an alpine/git sibling on `perpetuity_default` clones `git://<container>:9418/test.git` (exit 0); a second sibling re-clones into a tmp path, asserts `/tmp/c/.git/HEAD` exists, and prints HEAD_OK. Proves D023 transport — git daemon binds 9418, `--export-all` is set, compose-DNS resolves the alias from siblings.

Scenario D (always_on bypass): backend PATCH /api/v1/teams/{id}/mirror with `{always_on: true}` (200); psql back-dates last_idle_at by 120s past the 60s deadline; the reaper's next tick logs `team_mirror_reap_skipped team_id=<uuid> reason=always_on` and the container stays running.

Scenario E (re-enable): backend PATCH with `{always_on: false}`; the back-dated last_idle_at is still in place (T03 only updates always_on); within 2× reaper_interval the reaper logs `team_mirror_reaped team_id=<uuid> ... reason=idle`; `docker inspect` of the team-mirror container exits non-zero (gone); the team_mirror_volumes row's container_id is NULL but volume_path persists.

Final structural sweep asserts the six required log markers (`team_mirror_started`, `team_mirror_reused`, `team_mirror_reaped`, `team_mirror_reap_skipped`, `mirror_idle_timeout_seconds_resolved value=60`, `team_mirror_always_on_toggled`) all appeared across the orchestrator + backend logs and that `team_mirror_reaper_tick_failed` did NOT appear.

One real bug found and fixed during verification: my first draft asserted on the bare `team_mirror_reap_skipped` token, but the reaper had already fired one tick BEFORE the test's back-date logging `reason=recent_activity` — `_wait_for_log_marker` shortcut on that line and accepted it as if it were the always_on skip. Captured MEM267 and tightened the assertion to match on the full `team_mirror_reap_skipped team_id=<uuid> reason=always_on` substring.

Wall-clock: 17.37s on a warm compose stack (orchestrator boot ~10s + backend boot ~30s budgeted, but most fixtures were cached); well under the 240s budget the test asserts.

## Verification

Ran the slice-plan verification command after `docker compose build backend orchestrator` and `docker compose up -d db redis` were already in place: `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s03_team_mirror_e2e.py -v`. Exit code 0; 1 passed in 17.37s. The test asserts every must-have from the slice plan: cold-start ensure shape, idempotent reuse, sibling git clone over git daemon on 9418, always_on=true bypasses reap, always_on=false re-enables idle reap and persists volume_path. Final log-marker sweep verifies all six required observability signals appear in the captured backend + orchestrator logs.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build backend orchestrator` | 0 | ✅ pass | 8000ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s03_team_mirror_e2e.py -v` | 0 | ✅ pass | 17370ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/integration/test_m004_s03_team_mirror_e2e.py`
