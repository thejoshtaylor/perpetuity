# S03: Idle reaper + container lifecycle (verification-only over M002/S04 + M002/S05) — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T14:50:01.893Z

# S03 UAT — Idle reaper + container lifecycle

**Slice:** M003-umluob / S03
**Verdict:** ✅ Pass (verification-only over already-shipped M002/S04 + M002/S05 code)
**Required infrastructure:** real Docker daemon, real Postgres (`perpetuity-db-1` port 55432), real Redis (`perpetuity-redis-1`), `perpetuity/workspace:latest` and `perpetuity/workspace:test` images locally available. Working tree clean at HEAD `b1afe70`.

## Preconditions

1. Repo at `/Users/josh/code/perpetuity`, branch `main`, `git status --porcelain` empty.
2. `docker compose ps` shows `db`, `redis`, `orchestrator`, `backend` running and healthy.
3. `.env` loaded with `POSTGRES_PASSWORD=changethis`, `REDIS_PASSWORD=changethis` (per MEM111).
4. `orchestrator/.venv` exists; `backend` resolves `uv run pytest` to project `.venv/bin/python`.

## Test cases

### UAT-S03-1 — Verification artifact structural invariants

**Goal:** confirm the slice's primary deliverable (T02-VERIFICATION.md) meets the slice plan's grep gate.

Steps:
1. `test -f .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` → exit 0.
2. `grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` → **expect ≥3** (got 3).
3. `grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` → exit 0.
4. `grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` → **expect ≥4** (got 10).

Expected: all four checks pass. ✅ Verified.

### UAT-S03-2 — Idle reap with volume persistence (Criterion 1)

**Goal:** reaper kills an idle session and the workspace volume persists.

Steps:
1. From `orchestrator/`, run `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach -v`.
2. Test provisions a session, sets `last_activity` to a time past `idle_timeout_seconds`, runs `_reap_one_tick`, asserts session-key gone from Redis and tmux session killed.
3. Then run `backend/` `uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` — step 9 dials `idle_timeout_seconds=3`, sleeps, asserts container removed AND `workspace_volume` row UUID still selectable, then re-provisions and asserts the same volume UUID is re-bound.

Expected: both PASSED. Volume `.img` survives reap; re-provision reuses the same volume.
Result: ✅ both PASSED (19.69s + 30.46s).

### UAT-S03-3 — Two-phase liveness check (Criterion 2; D018)

**Goal:** stale `last_activity` alone is NOT sufficient to reap; an active `AttachMap` entry must also be absent.

Steps:
1. From `orchestrator/`, run `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_skips_attached_session -v`.
2. Test provisions a session, sets `last_activity` past idle, **also registers an attach via `AttachMap.register`** (the second phase), runs `_reap_one_tick`, asserts session NOT killed.

Expected: PASSED. Reaper observes `is_attached(session_id) == True` and skips. Both conditions (stale Redis timestamp AND no live attach) must hold to reap.
Result: ✅ PASSED.

Edge case verified by the test: `attach_registered session_id=<sid>` log key fires before `_reap_one_tick`, used as a synchronization signal.

### UAT-S03-4 — Container lifecycle on last-session reap (Criterion 3a)

**Goal:** when the last tmux session in a container is reaped, the container itself is removed (not left as a zombie).

Steps:
1. From `orchestrator/`, run `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed -v`.
2. Test provisions a session in a fresh container, idles it, runs `_reap_one_tick`, asserts `docker ps --filter label=user_id=…` shows zero containers AND `reaper_reaped_container` log line emitted.

Expected: PASSED. Container removed; volume row in Postgres survives (verified by UAT-S03-2 step 3).
Result: ✅ PASSED.

### UAT-S03-5 — Sibling session blocks container reap (Criterion 3b)

**Goal:** a container with multiple tmux sessions is NOT removed when only one session goes idle.

Steps:
1. From `orchestrator/`, run `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v`.
2. Test provisions two sessions in the same (user, team) container, idles only one, runs `_reap_one_tick`, asserts the idle session is killed but the container is still running and the second session is intact.

Expected: PASSED. Container reap is gated on "no remaining tmux sessions in this container."
Result: ✅ PASSED.

### UAT-S03-6 — End-to-end demo path (slice-plan demo)

**Goal:** the slice plan's "demo" — provision → write marker → idle → reap → container gone → re-provision → marker readable — runs end-to-end.

Steps:
1. From `backend/`, run `uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v`.
2. Test exercises full M002/S05 acceptance, including step 8 (write to volume + restart orchestrator + assert volume row + container survive) and step 9 (idle reap with 3s timeout + assert container removed + assert workspace_volume row UUID re-selectable + re-provision + assert same UUID re-bound).

Expected: PASSED in ≤60s.
Result: ✅ PASSED in 30.46s.

## Edge cases & negative paths

- `reaper_tick_failed reason=<ExceptionClassName>` — observed and intentionally non-noisy (no traceback). Verified in `reaper.py` L… error keys are present per the inspection-surfaces section of the slice plan.
- `reaper_skipped_bad_record` — exercised by malformed-record tests in the broader `test_reaper.py` suite (12 tests total; 4 cited here are the success-criteria-anchored core).
- `reaper_stop_timeout` — surfaces if `stop_reaper` doesn't return within the lifespan teardown grace window; not exercised by these four tests but covered by `main.py` L246–L252 ordering.

## Outstanding human action

**M003-umluob ≡ M002-jy6pde duplication.** A human owner must close M003 as already-delivered or `gsd_replan_slice` it toward its true Projects/GitHub scope (R009–R012). This UAT does not unblock S04/S05/S06 — those slices should follow the same verification-only pattern unless and until that reconciliation flips M003 to net-new scope.

## Sign-off

All 6 UAT cases pass. The slice's stopping condition (verification artifact + grep gate + cited tests green) is met. Volume row persistence, two-phase liveness check, and container lifecycle are all proven against the real compose stack — no mocked Docker.
