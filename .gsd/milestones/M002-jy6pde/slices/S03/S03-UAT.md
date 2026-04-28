# S03: system_settings API + dynamic workspace_volume_size_gb + partial-apply shrink — UAT

**Milestone:** M002-jy6pde
**Written:** 2026-04-25T12:08:18.146Z

## UAT — M002/S03: system_settings API + dynamic workspace_volume_size_gb + partial-apply shrink

### Preconditions

- Compose stack up: `db`, `redis`, `orchestrator` services healthy (`docker compose ps` shows all `healthy`).
- Backend image rebuilt after T01 lands the `s05_system_settings` alembic revision: `docker compose build backend orchestrator`.
- `system_settings` table has no `workspace_volume_size_gb` row at test start (autouse fixture wipes both before and after).
- Seeded FIRST_SUPERUSER (admin@example.com / changethis) is `role=system_admin`.
- Two fresh test users available — alice@example.com and bob@example.com — neither has signed up yet.
- Orchestrator's boot-time `DEFAULT_VOLUME_SIZE_GB=4` (compose default) so the fallback path resolves to 4.

### Test 1 — Migration round-trips cleanly

1. From `backend/`, run `POSTGRES_PORT=5432 uv run alembic upgrade head`.
   - **Expected:** revision `s05_system_settings` is at head; `system_settings` table exists with PK on `key` and JSONB `value` column.
2. Run `POSTGRES_PORT=5432 uv run alembic downgrade -1`.
   - **Expected:** head moves to `s04_workspace_volume`; `system_settings` table is dropped (PK index goes with it).
3. Run `POSTGRES_PORT=5432 uv run alembic upgrade head` again.
   - **Expected:** clean re-upgrade with `Running upgrade s04_workspace_volume -> s05_system_settings`.

### Test 2 — Pre-state: alice provisions a 4 GiB volume from the fallback

1. Sign up alice@example.com via POST `/api/v1/signup`; login as alice; POST `/api/v1/sessions` with alice's personal team_id.
   - **Expected:** orchestrator provisions a fresh container; backend returns 201 with the session_id; orchestrator stdout emits `volume_size_gb_resolved source=fallback value=4` (system_settings is empty → InvalidValue/RowMissing fallback).
2. Inspect DB: `docker compose exec db psql -U postgres -d app -c "SELECT size_gb FROM workspace_volume WHERE user_id='<alice>'"`.
   - **Expected:** single row with `size_gb=4`.
3. Confirm no `workspace_volume_size_gb` row exists yet: `docker compose exec db psql -U postgres -d app -c "SELECT key, value FROM system_settings WHERE key='workspace_volume_size_gb'"`.
   - **Expected:** zero rows.

### Test 3 — Admin PUT triggers partial-apply shrink (D015)

1. Login as the seeded admin (FIRST_SUPERUSER); confirm `GET /api/v1/users/me` returns `role=system_admin`.
2. PUT `/api/v1/admin/settings/workspace_volume_size_gb` body `{"value": 1}`.
   - **Expected response:** 200 OK with body `{key: "workspace_volume_size_gb", value: 1, updated_at: <ISO timestamp>, warnings: [{user_id: "<alice-uuid>", team_id: "<alice-personal-team-uuid>", size_gb: 4, usage_bytes: null}]}`.
   - **Expected backend stdout:** lines containing `system_setting_updated actor_id=<admin-uuid> key=workspace_volume_size_gb previous_value_present=false` AND `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<admin-uuid> affected=1`.
3. Re-inspect DB: `SELECT size_gb FROM workspace_volume WHERE user_id='<alice>'`.
   - **Expected:** still `size_gb=4` (D015 partial-apply — existing row never re-derived).
4. Verify system_settings row landed: `SELECT value FROM system_settings WHERE key='workspace_volume_size_gb'`.
   - **Expected:** value `1` (JSONB int).

### Test 4 — Bob's fresh provision picks up the new cap (1 GiB)

1. Sign up bob@example.com; login as bob; POST `/api/v1/sessions` with bob's personal team_id.
   - **Expected:** 201 with session_id; orchestrator stdout emits `volume_size_gb_resolved source=system_settings value=1`.
2. Inspect DB: `SELECT size_gb FROM workspace_volume WHERE user_id='<bob>'`.
   - **Expected:** single row with `size_gb=1`.
3. WS-connect to `/api/v1/ws/terminal/<bob-session-id>` and run `df -BG /workspaces/<bob-team>`.
   - **Expected:** Total column reports ~1 GiB.
4. Run `dd if=/dev/zero of=/workspaces/<bob-team>/big bs=1M count=1100`.
   - **Expected:** `dd` aborts with `dd: error writing '...': No space left on device` somewhere between byte ~990 MB and 1.05 GB. `stat -c %s /workspaces/<bob-team>/big` ≤ 1.05 GiB. Kernel-enforced cap (same proof as S02/T04 but the cap is now admin-driven).

### Test 5 — Idempotent PUT preserves divergence semantics

1. PUT `/api/v1/admin/settings/workspace_volume_size_gb` body `{"value": 1}` (same value, second time).
   - **Expected:** 200 OK; backend log emits `system_setting_updated ... previous_value_present=true`. Warnings list still includes alice (size_gb=4 > 1 stays a warning regardless of whether the value changed).
2. PUT same body a third time.
   - **Expected:** 200 OK; same shape. UPSERT is idempotent.

### Test 6 — Negative cases

1. **Non-admin PUT:** Login as alice (role=user); PUT `/api/v1/admin/settings/workspace_volume_size_gb` body `{"value": 2}`.
   - **Expected:** 403 Forbidden (router-level system_admin gate).
2. **Unauthenticated PUT:** Send PUT with no cookie jar.
   - **Expected:** 401 Unauthorized.
3. **Out-of-range value (>256):** As admin, PUT `{"value": 300}`.
   - **Expected:** 422 with body `{detail: {detail: "invalid_value_for_key", key: "workspace_volume_size_gb", reason: "must be int in 1..256"}}`.
4. **Out-of-range value (<1):** As admin, PUT `{"value": 0}`.
   - **Expected:** 422 same shape.
5. **Wrong type (string):** As admin, PUT `{"value": "banana"}`.
   - **Expected:** 422 invalid_value_for_key.
6. **Wrong type (bool):** As admin, PUT `{"value": true}`.
   - **Expected:** 422 invalid_value_for_key (bool rejected explicitly so JSON `true` does not coerce to 1).
7. **Unknown key:** As admin, PUT `/api/v1/admin/settings/bogus_key` body `{"value": 1}`.
   - **Expected:** 422 with body `{detail: {detail: "unknown_setting_key", key: "bogus_key"}}`.

### Test 7 — Orchestrator fallback paths

1. **Row missing:** Delete `workspace_volume_size_gb` from system_settings; trigger a fresh provision (new user, fresh team).
   - **Expected:** orchestrator stdout emits `system_settings_lookup_failed key=workspace_volume_size_gb reason=RowMissing` and `volume_size_gb_resolved source=fallback value=4`. New row gets size_gb=4.
2. **Invalid value type:** UPSERT `system_settings` with value `"banana"` (raw SQL); trigger a fresh provision.
   - **Expected:** stdout emits `system_settings_lookup_failed reason=InvalidValue` and `volume_size_gb_resolved source=fallback value=4`. New row gets size_gb=4.
3. **DB unreachable:** Stop the db service; attempt a fresh provision.
   - **Expected:** orchestrator returns 500 (provision step is `db_unreachable` or similar — provisioning fails because the workspace_volume row cannot be written, but the fallback path still emits the WARNING + INFO before the write fails. Captured for documentation.)

### Test 8 — Log redaction sweep (MEM134 invariant)

1. After running Tests 2-6 in sequence, capture `docker compose logs backend` and `docker compose logs orchestrator`.
2. Grep both for `alice@example.com`, `bob@example.com`, and the seeded full_names ("Alice Test", "Bob Test").
   - **Expected:** ZERO matches in either log. All identifying log lines emit UUIDs only.

### Test 9 — Demo-truth automated e2e

Run from `backend/`:

```
POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v
```

- **Expected:** 1 passed in ~9.5 s; all eight flow steps green; redaction sweep at the end finds zero email/full_name matches.

### Edge cases

- **Concurrent PUTs from two admins:** Postgres UPSERT serializes; second-arriving wins (last-write-wins semantics). No corruption — the system_settings row reflects whichever PUT committed last.
- **Concurrent provisions during a PUT:** Provision-A reads `value=4` before the PUT lands; Provision-B reads `value=1` after. Both succeed with their respective sizes — no in-process cache means provisions read live state. Existing volumes never affected.
- **PUT to a string-type setting (future):** Unknown to S03 — out of scope. The validator registry will reject any key that isn't `workspace_volume_size_gb` until S04+ adds more entries.
- **Migration partial state during alembic upgrade:** `s05_system_settings` is a single CREATE TABLE — atomic. Failure during creation rolls back; orchestrator's lookup hits the `RowMissing` fallback. Provisioning continues at the boot-time default.
- **System restart with system_settings populated:** orchestrator resolves on every fresh provision; no in-process cache means restart preserves correctness — first provision after restart reads live system_settings value.

### Sign-off criteria

- All Tests 1-9 pass on the live compose stack against real Postgres + real Redis + real orchestrator + real Docker daemon.
- Wall-clock for the full demo-truth e2e ≤ 60 s (slice budget; observed ~9.5 s).
- No log line in backend or orchestrator stdout contains email, full_name, or team slug at any point during the flow.
- DB inspection confirms D015 invariant: existing `workspace_volume.size_gb` rows are never mutated by an admin PUT.
- Idempotent PUT and reject-by-default on unknown keys both confirmed.
