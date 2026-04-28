---
id: T03
parent: S03
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/volume_store.py
  - orchestrator/tests/integration/test_volumes.py
  - orchestrator/tests/integration/test_sessions_lifecycle.py
key_decisions:
  - JSON-parse the asyncpg JSONB value locally with json.loads (rather than register set_type_codec on the shared pool) — narrower blast radius, no silent shape change for unrelated JSONB reads. Also covers a fresh-pool open via open_pool with no codec setup. Captured as MEM154.
  - Treat JSON parse failure, NULL value, type mismatch, and out-of-range all as fallback paths with synthetic reason names (RowMissing, InvalidValue) — operators see exactly why the fallback fired without ambiguity, and the orchestrator never refuses to provision because of a transiently-bad system_settings row.
  - No in-process caching of the resolved value — slice acceptance requires a fresh PUT take effect on the very next provision and provision is rare (R001), so the 1-query overhead is acceptable.
  - Log key name + reason class but NEVER the JSONB value verbatim (matches T02 redaction discipline; future settings could carry secrets like SMTP_PASSWORD).
  - Reject Python bool explicitly in the validator since `isinstance(True, int) == True` would otherwise coerce JSON `true` to 1 — mirrors the backend admin API validator from T02 so the two sides agree on what's invalid.
duration: 
verification_result: passed
completed_at: 2026-04-25T11:56:42.223Z
blocker_discovered: false
---

# T03: Wire orchestrator fresh-volume create to read workspace_volume_size_gb from system_settings on every call, with WARNING+fallback on miss/invalid/error

**Wire orchestrator fresh-volume create to read workspace_volume_size_gb from system_settings on every call, with WARNING+fallback on miss/invalid/error**

## What Happened

Replaced the orchestrator's hardcoded `default_volume_size_gb=4` with a per-call lookup against `system_settings.workspace_volume_size_gb` on every fresh-row branch in `ensure_volume_for`. Existing-volume rows are never re-derived (D015 partial-apply rule preserved); the lookup only governs new-row creation.

Implementation in `orchestrator/orchestrator/volume_store.py`:
- Added `_resolve_default_size_gb(pool: asyncpg.Pool) -> int` that runs `SELECT value FROM system_settings WHERE key = 'workspace_volume_size_gb'` against the existing 5s-command_timeout pool. Returns the int when present and validly bounded (1..256, bool rejected explicitly to stop JSON `true` coercing to 1 — mirrors the backend admin API validator from T02). Falls back to `settings.default_volume_size_gb` (the boot-time 4 GiB env) on any of: pg unreachable, OSError, query timeout, row missing, value NULL, JSON parse failure, type mismatch, or out-of-range.
- Wired the helper into `ensure_volume_for`'s fresh-row branch BEFORE `allocate_image` so the size used for `truncate -sNG` and `mkfs.ext4` matches the size persisted to the row. Explicit `size_gb` parameter overrides bypass the lookup (preserves test-path control flow).
- Logs `WARNING system_settings_lookup_failed key=workspace_volume_size_gb reason=<class>` on every non-happy path (with `RowMissing` and `InvalidValue` as synthetic class names for those specific cases), and `INFO volume_size_gb_resolved source=<system_settings|fallback> value=<n>` on every call so an operator can confirm via compose logs that a fresh PUT is biting. The JSONB value is never logged verbatim (slice plan redaction discipline — future settings could carry secrets).
- No in-process caching: slice acceptance demands a fresh PUT take effect on the very next provision. Provision is rare (R001) so the 1-query overhead is acceptable per the load profile.

Tests:
- `orchestrator/tests/integration/test_volumes.py` — 3 new helper-level tests with a per-test `pg_pool` fixture and a `clean_workspace_volume_size_gb` fixture that wipes the row before/after to keep state hermetic. Covers: row present (value=2 returns 2), row missing (returns boot-time 4 + RowMissing WARNING), value="banana" (returns boot-time 4 + InvalidValue WARNING). All assert the INFO `source=system_settings|fallback` line shape.
- `orchestrator/tests/integration/test_sessions_lifecycle.py` — added `test_provision_uses_resolved_default`. UPSERTs system_settings to value=2 via `docker exec ... psql`, creates a brand-new (user, team), POSTs `/v1/sessions`, asserts the `workspace_volume.size_gb` row equals 2 (not 4), and verifies the `volume_size_gb_resolved source=system_settings value=2` INFO line appears in the live orchestrator's compose logs as belt-and-suspenders proof the helper actually ran. Cleans up the row in `finally` so other fixtures (the 4-GiB `orchestrator` and the 1-GiB `orchestrator_1gb`) keep booting at their boot-time defaults.

Decision during execution (caught by failing test): the task plan claimed asyncpg returns JSONB as a Python value already, but on a pool without a registered `set_type_codec("jsonb", ...)` codec it actually returns the raw JSON text (`str`). Two options: register a codec on the shared pool (would silently change every other JSONB read) or `json.loads` locally. Chose local parse — narrower blast radius, treats parse failure as InvalidValue (same fallback path). Captured this as MEM154 so future asyncpg JSONB consumers in the orchestrator do not repeat the investigation.

## Verification

Ran the task plan's verification command end-to-end:

1. `docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v -k 'resolve_default or provision_uses_resolved'` — 3 helper tests pass (the `provision_uses_resolved` filter has no matches in test_volumes.py because that test lives in test_sessions_lifecycle.py per the slice plan's "and/or" wording).

2. Full `tests/integration/test_volumes.py` (17 tests) passes — no regression in the 14 pre-existing tests.

3. From the host: `cd orchestrator && uv run pytest tests/integration/test_sessions_lifecycle.py::test_provision_uses_resolved_default -v` passes — proves the live orchestrator picks up the system_settings UPSERT on the next fresh provision (no restart needed, no cache).

4. Backend regression check: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s02_volume_cap_e2e.py -v` passes (after `docker compose build backend` to bake the s05 alembic revision per MEM147). The 4-GiB default e2e test confirms the fallback path is correct when system_settings has no row.

5. T02 admin tests: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v` — 32 passed, no regression. (The verification gate's prior failure was a path mismatch: it ran from `/Users/josh/code/perpetuity` against `tests/api/routes/...` when the path is `backend/tests/api/routes/...`.)

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build orchestrator` | 0 | pass | 35000ms |
| 2 | `docker compose up -d --force-recreate orchestrator` | 0 | pass | 8000ms |
| 3 | `docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests` | 0 | pass | 1000ms |
| 4 | `docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v -k 'resolve_default'` | 0 | pass | 110ms |
| 5 | `docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v` | 0 | pass | 6670ms |
| 6 | `uv run pytest tests/integration/test_sessions_lifecycle.py::test_provision_uses_resolved_default -v (from orchestrator/)` | 0 | pass | 2680ms |
| 7 | `docker compose build backend` | 0 | pass | 30000ms |
| 8 | `POSTGRES_PORT=5432 uv run pytest tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s02_volume_cap_e2e.py -v (from backend/)` | 0 | pass | 38940ms |
| 9 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v (from backend/)` | 0 | pass | 780ms |

## Deviations

Task plan claimed asyncpg returns JSONB as a Python value already; in reality it returns raw JSON text (`str`) without a registered codec. Confirmed empirically (a JSON `2` came back as the string `'2'`) and added a local json.loads in the helper. The first run of test_resolve_default_size_gb_reads_system_settings caught this (assertion `value == 2` failed because the validator rejected `'2'` as not-int and the fallback fired). Captured as MEM154 so it doesn't bite the next asyncpg+JSONB consumer.

## Known Issues

none

## Files Created/Modified

- `orchestrator/orchestrator/volume_store.py`
- `orchestrator/tests/integration/test_volumes.py`
- `orchestrator/tests/integration/test_sessions_lifecycle.py`
