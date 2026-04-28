---
estimated_steps: 12
estimated_files: 2
skills_used: []
---

# T04: End-to-end demo test — admin PUT triggers partial-apply shrink and next signup gets the new cap

Land the slice's demo-truth integration test in `backend/tests/integration/test_m002_s03_settings_e2e.py`, marked `e2e`. Reuses the e2e harness pattern from `test_m002_s02_volume_cap_e2e.py` (sibling backend container on `perpetuity_default`, real Postgres + real Redis + real orchestrator + real Docker daemon — no mocks, no swapping orchestrator containers). The orchestrator can stay on its compose default (DEFAULT_VOLUME_SIZE_GB=4) for the entire test because the new system_settings row is what now governs fresh provisions, not the env knob.

Flow:
1. Promote the seeded admin@example.com to system_admin if not already (or use the FIRST_SUPERUSER seeded by prestart) and log in to get the admin cookie jar.
2. Sign up alice@example.com. POST /api/v1/sessions with alice's personal team_id → orchestrator provisions a 4 GiB volume (system_settings is empty so resolve falls back to settings.default_volume_size_gb=4). Assert alice's workspace_volume row has size_gb=4 via a direct SQL fetch through a sibling psql exec (`docker compose exec db psql -U postgres -d app -c "SELECT size_gb FROM workspace_volume WHERE user_id='<alice>'"`).
3. As admin, PUT /api/v1/admin/settings/workspace_volume_size_gb {value: 1}. Assert response is 200 with `value=1` and `warnings` is a non-empty list containing alice's row (user_id, team_id, size_gb=4, usage_bytes=null). Assert log line `system_setting_updated actor_id=<seeded admin uuid> key=workspace_volume_size_gb previous_value_present=false` AND `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<uuid> affected=1` appear in the backend's stdout (`docker logs <backend_container>`). Assert alice's workspace_volume row is unchanged (still size_gb=4) — the partial-apply shrink rule.
4. Sign up bob@example.com. POST /api/v1/sessions with bob's personal team_id → orchestrator provisions a 1 GiB volume (system_settings now governs). Assert bob's workspace_volume row has size_gb=1 via the same psql exec. Assert orchestrator log emits `volume_size_gb_resolved source=system_settings value=1` for bob's provision.
5. WS-attach as bob; run `df -BG /workspaces/<team>` and assert reported total is 1 GiB; run `dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100` and assert `No space left on device` in the dd output (the same kernel-cap proof S02 demonstrated, only this time the cap was admin-driven, not env-driven).
6. Idempotent PUT: re-PUT `workspace_volume_size_gb` with value=1; assert 200 with `previous_value_present=true` and warnings now empty (alice still has size_gb=4 but the partial-apply rule still emits the same warnings — actually the rule says WARN whenever size_gb > new_value, so the warnings list is non-empty as long as alice exists. Confirm and assert appropriately).
7. Negative cases: non-admin PUT → 403; PUT with value=300 → 422 invalid_value_for_key; PUT to unknown key `bogus_key` → 422 unknown_setting_key.
8. Log redaction sweep: `docker logs <backend>` and `docker compose logs orchestrator` are scanned for alice@example.com / bob@example.com / their full names → assert ZERO matches (MEM134 invariant carried forward from S02/T04).

Wall-clock budget: ≤ 60 s per the milestone success criterion. Two fresh provisions (4 GiB then 1 GiB mkfs) + one dd + a handful of admin API calls — expect ≈ 30 s. The test must run with `-n 1` (default serial) to avoid kernel loop-device contention.

MEM147 reminder: this test will fail with `Can't locate revision identified by 's05_system_settings'` until `docker compose build backend` runs, because the backend image bakes /app/backend/app/alembic/versions/. The test fixture must `pytest.skip` if the backend image lacks the s05 revision (probe via `docker run --rm backend:latest cat /app/backend/app/alembic/versions/s05_system_settings.py`); the skip message must direct the operator to `docker compose build backend`.

## Inputs

- ``backend/app/api/routes/admin.py` — T02 endpoints under test`
- ``backend/app/alembic/versions/s05_system_settings.py` — T01 migration must be baked into backend image`
- ``orchestrator/orchestrator/volume_store.py` — T03's _resolve_default_size_gb behavior under test`
- ``backend/tests/integration/conftest.py` — backend_url + compose_stack_up fixtures (extend if needed for s05-revision skip probe)`
- ``backend/tests/integration/test_m002_s02_volume_cap_e2e.py` — pattern reference for sibling-backend + WS attach + dd`

## Expected Output

- ``backend/tests/integration/test_m002_s03_settings_e2e.py` — single e2e test method covering steps 1–8 above; marked `e2e``
- ``backend/tests/integration/conftest.py` — optional extension for the s05-revision-baked-into-backend-image skip probe (only if needed)`

## Verification

docker compose build backend orchestrator && docker compose up -d db redis orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v
