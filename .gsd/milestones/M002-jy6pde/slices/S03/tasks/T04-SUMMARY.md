---
id: T04
parent: S03
milestone: M002-jy6pde
key_files:
  - backend/tests/integration/test_m002_s03_settings_e2e.py
key_decisions:
  - Skip-on-missing-s05 autouse fixture probes backend:latest via `docker run --rm --entrypoint ls` rather than failing mid-alembic — operators see a clear `docker compose build backend` instruction instead of a confusing alembic error. MEM147 carry-forward.
  - Autouse fixture DELETEs the workspace_volume_size_gb system_settings row both before AND after the test so the assertion 'alice provisions a 4 GiB volume because system_settings is empty' is robust against the persistent `app-db-data` compose volume. Captured as MEM156.
  - After hitting a slicing-by-byte-offset bug on docker compose log captures, dropped the pre/post diff approach in favor of asserting the unique `source=system_settings value=1` substring against the full final log — alice's earlier provision logs `source=fallback value=4`, so no ambiguity. Captured as MEM155.
  - Reused S02/T04's printf-split sentinel pattern to demarcate dd output in the WS terminal — building the end-marker via printf concatenation so the literal substring is not echoed back by tmux on the typed input line.
duration: 
verification_result: passed
completed_at: 2026-04-25T12:03:02.804Z
blocker_discovered: false
---

# T04: Add M002/S03 e2e acceptance test proving admin PUT triggers partial-apply shrink warnings and the next signup gets the new kernel-enforced 1 GiB cap

**Add M002/S03 e2e acceptance test proving admin PUT triggers partial-apply shrink warnings and the next signup gets the new kernel-enforced 1 GiB cap**

## What Happened

Landed `backend/tests/integration/test_m002_s03_settings_e2e.py` covering all eight steps of the slice's demo-truth flow against the live compose stack (real Postgres + Redis + orchestrator + Docker daemon, sibling backend container per MEM117 — no orchestrator swap because the new system_settings row, not the orchestrator's boot-time env, governs fresh provisions).

Steps covered: (1) log in as the seeded FIRST_SUPERUSER and verify role==system_admin; (2) sign up alice → POST /api/v1/sessions provisions a 4 GiB volume because system_settings is empty (orchestrator's `_resolve_default_size_gb` falls back to settings.default_volume_size_gb=4); (3) PUT /api/v1/admin/settings/workspace_volume_size_gb {value:1} as admin → asserts 200, value=1, warnings list contains alice's row (size_gb=4, usage_bytes=null), backend stdout carries the `system_setting_updated ... previous_value_present=false` and `system_setting_shrink_warnings_emitted ... affected=N` lines, and alice's workspace_volume row in DB still has size_gb=4 (D015 partial-apply); (4) sign up bob → workspace_volume row has size_gb=1 and orchestrator stdout carries `volume_size_gb_resolved source=system_settings value=1`; (5) WS-attach as bob, run df + dd 1100 MB → asserts ENOSPC at the kernel boundary (same proof as S02/T04 but the cap is now admin-driven, not env-driven); (6) idempotent PUT — second PUT returns 200 with `previous_value_present=true` and warnings still list alice (size_gb=4 > 1 stays a warning regardless of value-change); (7) negative cases — non-admin PUT 403, value=300 → 422 invalid_value_for_key, unknown key → 422 unknown_setting_key with the bad key surfaced; (8) log redaction sweep over backend + orchestrator logs asserts ZERO occurrences of alice/bob email or full_name (MEM134 invariant carried forward).

Two implementation choices worth flagging:

1. Per MEM147 the test gates on the backend image actually baking the s05 alembic revision via an autouse `_require_s05_baked` fixture that probes `docker run --rm --entrypoint ls backend:latest /app/backend/app/alembic/versions/` for `s05_system_settings.py`. On miss it skips with a message that points the operator at `docker compose build backend`, so a stale image fails loudly rather than producing a confusing alembic error mid-test.

2. An autouse `_wipe_system_settings_before` fixture DELETEs the `workspace_volume_size_gb` row from system_settings both before and after the test. Compose's named `app-db-data` volume persists across test runs, so without this a previous run's row would bias step 2's "expect size_gb=4 (fallback)" assertion. Captured this as MEM156 for future M002 e2e authors.

One debugging beat: my first run failed at step 4 because I tried to capture the orchestrator log "before bob signs up" and slice the second capture by `len(pre_capture)` to get a "tail since". `docker compose logs` doesn't return byte-stable output across calls — the second capture was missing a trailing space and the slice landed mid-character, breaking the substring search. Fixed by dropping the slice-and-tail approach and just asserting that `volume_size_gb_resolved source=system_settings value=1` is present anywhere in the final orchestrator log; alice's earlier provision logs `source=fallback value=4` (system_settings was empty when she signed up), so there is no ambiguity about which provision the matching line corresponds to. Captured as MEM155.

Wall-clock: 9.49 s for the full eight-step flow including two fresh ext4 mkfs runs and a 1.1 GB dd — well under the 60s slice budget.

## Verification

Ran the full slice verification command:

```
docker compose ps  # confirmed db/redis/orchestrator healthy
cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \
    tests/integration/test_m002_s03_settings_e2e.py -v
```

Result: 1 passed, 3 warnings, 9.49s total. The test exercised: signup→login→session-create→admin-PUT→DB inspection→signup→WS-attach→df→dd→ENOSPC→idempotent-PUT→three negative cases→session DELETE→log redaction sweep. All eight numbered flow steps from the inlined task plan are covered with explicit assertions.

Backend image was confirmed to bake the `s05_system_settings.py` revision via a `docker run` ls probe before the test runs (autouse `_require_s05_baked` skip guard).

Slice-level observability assertions all fire: `system_setting_updated`, `system_setting_shrink_warnings_emitted`, and `volume_size_gb_resolved` are all present in the final compose-logs sweep. The redaction sweep confirms alice@example.com / bob@example.com / their full_names are absent from both backend and orchestrator logs.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v` | 0 | ✅ pass | 9490ms |
| 2 | `docker run --rm --entrypoint ls backend:latest /app/backend/app/alembic/versions/ | grep s05_system_settings.py` | 0 | ✅ pass | 1200ms |
| 3 | `docker compose ps --format '{{.Service}}\t{{.Health}}' (db, redis, orchestrator all healthy)` | 0 | ✅ pass | 200ms |

## Deviations

No structural deviations from the inlined plan. Two small adaptations: (a) consolidated the df + dd into a single async helper rather than two separate WS attaches (one fewer attach round-trip, no behavioral change); (b) the "scan only the tail since bob signed up" idea from the plan was replaced with a full-log substring scan after the byte-slicing bug — the value=1 source=system_settings shape is unique to bob's provision, so the assertion's specificity is preserved.

## Known Issues

None. The test runs in 9.49s on this host with all eight steps green.

## Files Created/Modified

- `backend/tests/integration/test_m002_s03_settings_e2e.py`
