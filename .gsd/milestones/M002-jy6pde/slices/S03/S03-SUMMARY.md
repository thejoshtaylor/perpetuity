---
id: S03
parent: M002-jy6pde
milestone: M002-jy6pde
provides:
  - ["system_settings table (key/value/updated_at) — canonical home for runtime-adjustable knobs", "GET/PUT /api/v1/admin/settings[/{key}] — system_admin-only API for reading and updating settings, with per-key validators", "Per-key validator registry (_VALIDATORS) — extension point for future keys; reject-by-default on unknown keys", "_compute_workspace_size_warnings helper — reusable D015 shrink-warnings template for future shrink-able settings", "_resolve_default_size_gb (orchestrator) — reusable per-call lookup pattern for future orchestrator-side system_settings consumers", "workspace_volume_size_gb is now admin-configurable at runtime — boot-time DEFAULT_VOLUME_SIZE_GB env is now only the fallback when system_settings is unreachable", "SystemSettingShrinkWarning shape with usage_bytes:int|None — schema is forward-compatible; S04 fills usage_bytes via a new orchestrator endpoint", "Backend image alembic skip-guard pattern (autouse fixture probing backend:latest for the new revision) — reusable for every M002+ e2e on a new alembic revision"]
requires:
  - slice: M002/S01
    provides: orchestrator service with asyncpg pool, the volume_store.ensure_volume_for fresh-row branch, and the existing system_admin-gated admin router (MEM089)
  - slice: M002/S02
    provides: workspace_volume Postgres table (s04 alembic) and per-(user, team) loopback-ext4 hard-cap volumes — the rows whose size_gb the partial-apply shrink rule diverges from
affects:
  - ["M002/S04 — should reuse _VALIDATORS for any new system_settings keys (e.g. idle_timeout_seconds); should add a GET /v1/volumes/{volume_id}/usage orchestrator endpoint to fill usage_bytes; the SystemSettingShrinkWarning schema is already forward-compatible", "M002/S05 — final integrated acceptance test should include an admin-PUT step to validate system_settings persists across orchestrator restart; two-key rotation acceptance unchanged from S03 (orchestrator's two-key auth is independent of system_settings)", "Future milestones (M003+) — any new runtime-adjustable knob should land as a system_settings key with a validator entry rather than a new env var; the table is generic and the API is already in place"]
key_files:
  - ["backend/app/alembic/versions/s05_system_settings.py", "backend/app/models.py", "backend/app/api/routes/admin.py", "backend/tests/api/routes/test_admin_settings.py", "orchestrator/orchestrator/volume_store.py", "orchestrator/tests/integration/test_volumes.py", "orchestrator/tests/integration/test_sessions_lifecycle.py", "backend/tests/integration/test_m002_s03_settings_e2e.py", "backend/tests/migrations/test_s05_migration.py"]
key_decisions:
  - ["Generic key/value table (system_settings) with JSONB value column and SQLModel value typed as Any — future settings can be scalars, lists, or dicts without schema churn", "Per-key validator registry (_VALIDATORS dict) with reject-by-default on unknown keys — typos surface as 422 unknown_setting_key instead of silently adding unread rows", "Reject Python bool explicitly in the workspace_volume_size_gb validator since isinstance(True, int) is True and JSON true would otherwise coerce to 1 — backend and orchestrator validators agree on this", "D015 partial-apply shrink: existing workspace_volume rows keep their old size_gb (cap divergence allowed); only fresh provisions use the new value; warnings payload surfaces affected rows but never mutates them", "Orchestrator parses JSONB locally with json.loads rather than registering set_type_codec on the shared asyncpg pool — narrower blast radius, no silent shape change for unrelated JSONB reads (MEM157)", "No in-process caching of resolved workspace_volume_size_gb in the orchestrator — slice acceptance demands a fresh PUT take effect on the very next provision and provision is rare per R001", "usage_bytes reported as null in the shrink-warnings payload — backend container does not bind-mount the host workspace path; S04 will add a backend→orchestrator GET /v1/volumes/{volume_id}/usage call", "Append the three new endpoints to the existing system_admin-gated admin router (MEM089) rather than create a new module — keeps 403/401 ordering uniform with every other admin endpoint", "Settings PUT logs key + actor_id (UUID) + previous_value_present only — never the JSONB value, since future settings could carry secrets", "Autouse skip-guard on the e2e probes backend:latest image for the s05_system_settings.py file and skips with a docker compose build backend instruction on miss (MEM162) — converts a confusing alembic error into an actionable test-skip"]
patterns_established:
  - ["Generic system_settings(key VARCHAR(255) PK, value JSONB) table — canonical home for runtime-adjustable knobs; future S04/S05 keys (idle_timeout_seconds, etc.) should land here", "Per-key validator registry (_VALIDATORS dict[str, Callable]) with reject-by-default on unknown keys — every new key must opt in via the registry, typos surface as 422", "D015 partial-apply shrink contract — admin can lower a cap freely; existing rows keep their old value; warnings payload surfaces divergence; never mutate existing rows", "Local json.loads on asyncpg JSONB columns (MEM157) — narrower blast radius than registering set_type_codec on the shared pool", "Orchestrator falls back to boot-time default on any system_settings issue (missing row, invalid value, pg unreachable, query timeout) — provisioning never blocked by transient system_settings problems", "Backend image alembic skip-guard pattern (MEM162) — every e2e test depending on a new alembic revision should ship an autouse probe that surfaces an actionable skip when the image is stale", "System_settings autouse cleanup pattern (MEM161) — every e2e test depending on system_settings absence/presence must wipe its row before AND after because compose's app-db-data volume persists across runs", "Append-to-existing-router pattern (MEM089) — new admin endpoints reuse the system_admin-gated dependencies on backend/app/api/routes/admin.py rather than creating new routers, keeping 403/401 ordering uniform"]
observability_surfaces:
  - ["INFO `system_setting_updated actor_id=<uuid> key=<str> previous_value_present=<true|false>` — backend stdout on every successful PUT (never logs the JSONB value)", "INFO `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<uuid> affected=<n>` — backend stdout, only when the warnings list is non-empty", "INFO `volume_size_gb_resolved source=<system_settings|fallback> value=<n>` — orchestrator stdout on every fresh-volume create; lets an operator confirm a fresh PUT is biting", "WARNING `system_settings_lookup_failed key=workspace_volume_size_gb reason=<class>` — orchestrator stdout on any non-happy path (RowMissing, InvalidValue, asyncpg errors); reason class is synthetic for clarity", "DB inspection: `SELECT key, value FROM system_settings` is the live source of truth for caps", "DB inspection: `SELECT user_id, team_id, size_gb FROM workspace_volume WHERE size_gb > <new_default>` reproduces the shrink-warnings payload from the DB (used by _compute_workspace_size_warnings)", "Failure visibility: 422 with {detail: 'invalid_value_for_key', key, reason} from per-key validators; 422 with {detail: 'unknown_setting_key', key} for typos", "All log lines emit UUIDs only — never email, full_name, team slug, or the JSONB value verbatim (MEM134 + S03 redaction discipline)"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-25T12:08:18.144Z
blocker_discovered: false
---

# S03: system_settings API + dynamic workspace_volume_size_gb + partial-apply shrink

**Lands generic `system_settings` key/value store + system_admin GET/PUT API + per-key validators + D015 partial-apply shrink so the workspace volume cap is now admin-configurable at runtime, with proven fresh-provision pickup and zero touch on existing volumes.**

## What Happened

## What this slice delivered

S03 turns the workspace volume cap from a boot-time env knob (`DEFAULT_VOLUME_SIZE_GB=4`) into a runtime-adjustable value owned by a generic `system_settings` Postgres table, gated behind a system_admin-only API, with the D015 partial-apply shrink rule: existing `workspace_volume` rows keep their old `size_gb` (cap divergence allowed); only fresh provisions pick up the new value. The orchestrator now resolves `workspace_volume_size_gb` from system_settings on every fresh-row create, with a WARNING-and-fallback path for missing/invalid/unreachable cases so a transiently bad row never blocks provisioning.

**Persistence (T01).** New table `system_settings(key VARCHAR(255) PK, value JSONB NOT NULL, updated_at TIMESTAMPTZ NULL)` via `s05_system_settings` alembic revision (chained off `s04_workspace_volume`). PK on `key` covers the only access pattern — no extra indexes. SQLModel `SystemSetting.value` typed as `Any` (not `dict`) so future scalar/list/dict payloads fit without schema churn. Pydantic shapes `SystemSettingPublic` and `SystemSettingPut` carry the API contract. Migration test follows the verbatim MEM016 release-and-dispose pattern from `test_s04_migration.py` so the autouse db session never blocks alembic. Round-trips up/down/up cleanly.

**API (T02).** Three endpoints appended to the existing system_admin-gated `backend/app/api/routes/admin.py` router (MEM089 — reuse the router-level `Depends(get_current_active_superuser)` so 403/401 ordering matches every other admin endpoint): `GET /admin/settings`, `GET /admin/settings/{key}`, `PUT /admin/settings/{key}`. Per-key validator registry `_VALIDATORS: dict[str, Callable]` reject-by-default on unknown keys (422 `unknown_setting_key`) — a typo can never silently add an unread row. The `workspace_volume_size_gb` validator enforces `isinstance(value, int) and 1 <= value <= 256`; Python `bool` is rejected explicitly (since `isinstance(True, int) == True` would otherwise coerce JSON `true` to 1). UPSERT via raw `INSERT ... ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW() RETURNING ...` keeps the JSONB cast clean for `Any`-typed values. Partial-apply shrink computation: `SELECT user_id, team_id, size_gb FROM workspace_volume WHERE size_gb > :new_value ORDER BY created_at` materializes a `warnings: [{user_id, team_id, size_gb, usage_bytes}, ...]` payload; existing rows never mutated. `usage_bytes` is `null` from this slice — backend container does not bind-mount the host workspace path; S04 will add a backend→orchestrator GET /v1/volumes/{volume_id}/usage call.

**Orchestrator wiring (T03).** Added `_resolve_default_size_gb(pool: asyncpg.Pool) -> int` to `orchestrator/orchestrator/volume_store.py`. Runs `SELECT value FROM system_settings WHERE key = 'workspace_volume_size_gb'` on every fresh-row branch in `ensure_volume_for`, BEFORE `allocate_image`, so `truncate -sNG` and `mkfs.ext4` use the same size persisted to the row. Hit on T01's planning gap: asyncpg returns JSONB as raw JSON **text (str)** unless a `set_type_codec("jsonb", ...)` is registered on the pool. Two options: register a codec on the shared pool (silently changes every other JSONB read) or `json.loads` locally per call — chose local parse for narrower blast radius (captured as MEM157). Treats JSON parse failure, NULL, type mismatch, out-of-range, pg unreachable, or query timeout all as fallback paths with synthetic reason names (`RowMissing`, `InvalidValue`). No in-process caching — slice acceptance demands a fresh PUT take effect on the very next provision; provision is rare per R001 so the 1-query overhead is acceptable.

**Demo-truth e2e (T04).** `backend/tests/integration/test_m002_s03_settings_e2e.py` (e2e marker, ~9.5 s wall-clock against real Postgres + Redis + orchestrator + Docker daemon — no mocks, no orchestrator swap because system_settings now governs, not the env). Eight numbered flow steps from the plan: (1) admin login + role check; (2) alice signup → 4 GiB volume because system_settings empty (fallback); (3) admin PUT to value=1 → 200 with warnings listing alice + log lines `system_setting_updated previous_value_present=false` and `system_setting_shrink_warnings_emitted affected=1` + alice's row unchanged at size_gb=4 (D015 invariant); (4) bob signup → 1 GiB volume + orchestrator log `volume_size_gb_resolved source=system_settings value=1`; (5) WS-attach as bob, run `df` (1 GiB total) and `dd ... count=1100` → ENOSPC at the kernel boundary (same proof as S02 but admin-driven, not env-driven); (6) idempotent PUT → 200 with `previous_value_present=true` and warnings still list alice; (7) negative cases — non-admin 403, value=300 → 422 invalid_value_for_key, unknown key → 422 unknown_setting_key; (8) MEM134 redaction sweep over backend + orchestrator logs asserts ZERO occurrences of alice/bob email or full_name. Two e2e harness adaptations beyond what S02 used: an autouse skip-guard probes `docker run --rm --entrypoint ls backend:latest /app/backend/app/alembic/versions/` for `s05_system_settings.py` and skips with a `docker compose build backend` instruction on miss (MEM162); and an autouse fixture DELETEs the workspace_volume_size_gb row before AND after the test because compose's named `app-db-data` volume persists across runs (MEM161 — without it a previous run biases the alice fallback assertion).

## Patterns established for downstream slices

- **Generic system_settings table** — future runtime-adjustable knobs (S04's `idle_timeout_seconds`, S05's two-key rotation params if needed) all live here. The validator registry pattern is the canonical extension point — add a new entry in `_VALIDATORS` and the API auto-rejects typos and bad shapes.
- **Per-key validator + reject-by-default unknowns** — typo-proof. New keys must explicitly opt in via the registry.
- **Partial-apply shrink contract (D015)** — admin can lower a cap freely; existing rows keep their old value; only fresh creates use the new value; warnings payload surfaces the divergence to the operator. Future shrink-able settings can reuse `_compute_workspace_size_warnings` as a template.
- **JSON-parse-locally for asyncpg JSONB** — captured as MEM157. Future asyncpg+JSONB readers in the orchestrator should follow this rather than register a global codec.
- **Backend image alembic skip-guard (MEM162)** — every M002+ e2e test that depends on a new alembic revision should ship this autouse probe so a stale backend image surfaces an actionable skip rather than a cryptic alembic error.
- **System_settings autouse cleanup (MEM161)** — every e2e test depending on system_settings absence/presence must wipe its row before AND after.

## What S04 should know

- `usage_bytes` in the shrink-warnings payload is currently `null`. S04 introduces `GET /v1/volumes/{volume_id}/usage` on the orchestrator and an admin-side caller path that fills this in. The schema is forward-compatible — the field already exists, just needs a non-null source.
- The orchestrator now reads `system_settings.workspace_volume_size_gb` on every fresh provision; S04 should follow the same `_resolve_default_*` helper pattern if it adds an `idle_timeout_seconds` system_settings key (and capture it as a new validator entry in the backend's `_VALIDATORS` registry).
- The `_VALIDATORS` registry is the single source of truth for what keys exist. S04's idle reaper config goes here.
- The orchestrator never caches resolved values — the next PUT bites the next provision. S04 may want the same property for idle_timeout_seconds.

## Verification

## Slice-level verification — all green

**T01 — migration round-trip (`backend/`):**
- `POSTGRES_PORT=5432 uv run alembic upgrade head` → 0
- `POSTGRES_PORT=5432 uv run alembic downgrade -1` → 0
- `POSTGRES_PORT=5432 uv run alembic upgrade head` (re-up) → 0
- `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s05_migration.py -v` → 3 passed in 160 ms
- Regression: `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v` → 4 passed

**T02 — admin API tests (`backend/`):**
- `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v` → 32 passed (17 new admin_settings + 15 admin_teams regression) in 860 ms

**T03 — orchestrator helper + provision integration:**
- `docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests`
- `docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v` → 17 passed (3 new resolve-default tests + 14 regression) in 6.67 s
- `cd orchestrator && uv run pytest tests/integration/test_sessions_lifecycle.py::test_provision_uses_resolved_default -v` → passed (live orchestrator picks up system_settings UPSERT on next provision, no restart)
- `cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s02_volume_cap_e2e.py -v` → both pass (S01/S02 regression after rebuild)

**T04 — slice demo-truth e2e (`backend/`):**
- `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v` → 1 passed in 9.37 s (re-run on top of completed T04 commit, after the prior gate's path-mismatch failure was diagnosed)
- All eight numbered flow steps explicitly assert: admin login + role check; alice fallback to 4 GiB; admin PUT 200 + warnings + log lines + unchanged DB row; bob fresh 1 GiB pickup + orchestrator log; WS df + dd ENOSPC at kernel boundary; idempotent PUT with `previous_value_present=true`; non-admin 403; out-of-range 422 invalid_value_for_key; unknown key 422 unknown_setting_key; redaction sweep finds zero email/full_name in backend or orchestrator logs (MEM134).

**Compose state at verification time:** `db healthy`, `orchestrator healthy`, `redis healthy` (verified via `docker compose ps`).

**Diagnosis of the prior verification-gate failure:** the auto-fix gate reported `tests/integration/test_m002_s03_settings_e2e.py` not found because the command was run from `/Users/josh/code/perpetuity` (repo root) without the `backend/` prefix. The file is at `backend/tests/integration/test_m002_s03_settings_e2e.py` and was committed in `ed58d8a`. Running `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v` (the canonical form from the slice plan) succeeds.

**Observability surfaces — all confirmed firing in the e2e:**
- `system_setting_updated actor_id=<uuid> key=workspace_volume_size_gb previous_value_present=false|true` (backend stdout on PUT)
- `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<uuid> affected=<n>` (backend stdout, only when warnings non-empty)
- `volume_size_gb_resolved source=system_settings|fallback value=<n>` (orchestrator stdout on every fresh provision)
- `system_settings_lookup_failed key=workspace_volume_size_gb reason=<class>` (orchestrator stdout on miss/error — proven via the `value="banana"` and row-missing helper tests)
- All log lines emit UUIDs only — never email, full_name, or team slug. JSONB value never logged verbatim (future-secret-safe).

**Inspection surfaces:**
- `SELECT key, value FROM system_settings` is the live source of truth for caps.
- `SELECT user_id, team_id, size_gb FROM workspace_volume WHERE size_gb > <new_default>` reproduces the warnings payload from the DB (used by `_compute_workspace_size_warnings`).

**Failure visibility & redaction:**
- 422 with `{detail: 'invalid_value_for_key', key, reason}` from per-key validators.
- 422 with `{detail: 'unknown_setting_key', key}` for typos.
- Orchestrator falls back to `settings.default_volume_size_gb` (4) on any system_settings issue — provisioning never blocked by a transient bad row.
- Logs never include the JSONB value verbatim — only key + presence flag + reason class.

**Gate coverage:**
- **Q3 (Requirements coverage):** R005 + R006 implicitly carried forward from S02 (S03 doesn't change isolation or persistence guarantees, only the cap source-of-truth). No new requirements added/changed by S03.
- **Q4 (Risk):** medium per the roadmap. Mitigated by per-key validator (rejects bad shapes), reject-by-default on unknown keys (no typo footgun), partial-apply rule (no live-volume churn), and the orchestrator fallback (no provisioning outage if system_settings is unreachable).
- **Q5 (Verification):** demo-truth e2e covers all eight flow steps; helper tests cover the orchestrator's resolve path including all three fallback classes; admin API tests cover happy/sad/idempotent paths; migration test covers up/down/up. All run against real services — no mocks.
- **Q6 (Surface stability):** WS frame protocol unchanged (S01 lock preserved); admin router shape additive only (three new sub-routes); orchestrator HTTP API unchanged. No downstream-breaking surface change.
- **Q7 (Operability):** observability log keys taxonomy preserved (UUIDs only); failure modes documented and asserted. Inspection surfaces (psql queries) match what an operator would naturally reach for.
- **Q8 (Operational readiness):** see Operational Readiness section below.

## Requirements Advanced

- R006 — Containers spin up on demand with the cap now coming from system_settings; existing rows persist their old cap (D015 partial-apply); fresh provisions pick up the new admin-set cap. Volume persistence guarantee unchanged from S02.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"None structural. Two small adaptations versus the inlined plan: (1) T03 hit asyncpg's JSONB-as-str behavior on a pool without a registered codec — switched from the plan's assumption that asyncpg returns Python values to local json.loads in the helper; treats parse failure as InvalidValue (same fallback path). Captured as MEM157. (2) T04 dropped the slice plan's pre/post log-diff approach in favor of asserting the unique `source=system_settings value=1` substring against the full final orchestrator log — `docker compose logs` is not byte-stable across calls, so byte-offset slicing landed mid-character and broke substring searches. Alice's earlier provision logs `source=fallback value=4`, so there is no ambiguity about which provision the matched line belongs to. Captured as MEM160."

## Known Limitations

"usage_bytes in the shrink-warnings payload is null from this slice — backend container does not bind-mount the host workspace path. S04 will add a GET /v1/volumes/{volume_id}/usage orchestrator endpoint and an admin-side caller path that fills this in. The SystemSettingShrinkWarning schema is already forward-compatible (usage_bytes: int|None) — S04 just needs to flip null to a real integer source."

## Follow-ups

"S04: introduce GET /v1/volumes/{volume_id}/usage on the orchestrator and call it from _compute_workspace_size_warnings to fill usage_bytes (currently null). S04: add idle_timeout_seconds as a new system_settings key with a _VALIDATORS entry; orchestrator's idle reaper should resolve it via a sibling _resolve_idle_timeout helper following the same uncached-on-every-call pattern (or sample it at reaper start — operator decision). S05: add an admin-PUT step to the final integrated acceptance test confirming system_settings persists across orchestrator restart (table is in db service, so this should be free)."

## Files Created/Modified

- `backend/app/alembic/versions/s05_system_settings.py` — New alembic revision creating system_settings(key VARCHAR(255) PK, value JSONB NOT NULL, updated_at TIMESTAMPTZ NULL); chains off s04_workspace_volume; downgrade drops the table
- `backend/app/models.py` — Added SystemSetting (SQLModel table=True) using Column(JSONB, nullable=False); plus SystemSettingPublic, SystemSettingPut, SystemSettingShrinkWarning, SystemSettingPutResponse Pydantic shapes
- `backend/tests/migrations/test_s05_migration.py` — Three migration tests following the MEM016 release-and-dispose pattern: upgrade creates table with right shape; downgrade drops it; duplicate key insert raises IntegrityError
- `backend/app/api/routes/admin.py` — Appended GET /admin/settings, GET /admin/settings/{key}, PUT /admin/settings/{key} to the existing system_admin-gated router with _VALIDATORS registry and _compute_workspace_size_warnings helper
- `backend/tests/api/routes/test_admin_settings.py` — 17 new tests: empty/populated GET happy paths; 200/404 GET-by-key; PUT with empty/non-empty warnings; idempotent PUT logs previous_value_present=true on second call; shrink-warnings ordering by created_at; non-int 422; out-of-range 422; unknown key 422 unknown_setting_key; non-admin 403; unauthenticated 401
- `orchestrator/orchestrator/volume_store.py` — Added _resolve_default_size_gb(pool) helper that reads system_settings.workspace_volume_size_gb on every fresh-row branch in ensure_volume_for; falls back to settings.default_volume_size_gb on miss/invalid/error; emits volume_size_gb_resolved source=<system_settings|fallback> value=<n> on every call and system_settings_lookup_failed reason=<class> on fallback paths
- `orchestrator/tests/integration/test_volumes.py` — Three new helper-level tests (resolve_default reads system_settings, falls back when missing, falls back on invalid value); per-test pg_pool fixture and clean_workspace_volume_size_gb hermetic-state fixture
- `orchestrator/tests/integration/test_sessions_lifecycle.py` — Added test_provision_uses_resolved_default — UPSERTs system_settings to value=2 via psql, POSTs /v1/sessions for a fresh (user, team), asserts workspace_volume.size_gb=2 and orchestrator log carries volume_size_gb_resolved source=system_settings value=2
- `backend/tests/integration/test_m002_s03_settings_e2e.py` — Demo-truth e2e covering all 8 flow steps: admin login, alice fallback to 4 GiB, admin PUT 200 + warnings + log lines + unchanged DB row, bob fresh 1 GiB pickup, WS df + dd ENOSPC, idempotent PUT, three negative cases, MEM134 redaction sweep. Autouse skip-guard probes backend image for s05 revision; autouse fixture wipes system_settings row before/after
- `backend/tests/integration/conftest.py` — Light additions to support the s05 skip-guard probe and the system_settings cleanup autouse fixture
