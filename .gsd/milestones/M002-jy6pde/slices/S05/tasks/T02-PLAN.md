---
estimated_steps: 17
estimated_files: 1
skills_used: []
---

# T02: Add two-key rotation e2e proving both ORCHESTRATOR_API_KEY and _PREVIOUS are accepted

Land `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` proving the rotation acceptance contract end-to-end. The auth code path was already unit-tested in S01/T02, but no integration test runs an orchestrator with both keys set and proves that two distinct backends, each carrying a different key, both succeed against the same orchestrator endpoint.

Approach — reuse the live-orchestrator-swap pattern from S04 (MEM149) plus a custom sibling-backend boot. The sibling-backend `backend_url` fixture in `conftest.py` is hard-wired to the dotenv `ORCHESTRATOR_API_KEY` (line 301-302) and explicitly empties `ORCHESTRATOR_API_KEY_PREVIOUS` (line 324). T02 needs two backends with different keys — neither matches that fixture's shape. Solution: T02 ships its own `_boot_sibling_backend(api_key=...)` helper that takes the key as an argument and calls `docker run` with the matching env; same shape as the existing fixture, just parameterized by key.

Flow:
1. Autouse skip-guard: probe `backend:latest` for the s05 alembic revision presence (MEM162) and skip with `docker compose build backend` instructions on miss. Probe orchestrator image presence + workspace image presence (cheap re-checks beyond the conftest autouse) so the test gives a useful skip when the orchestrator hasn't been rebuilt for S05.
2. Generate two distinct random API keys (use `secrets.token_urlsafe(32)` — no need to read .env's value here; the test owns both halves of the secret). Call them `key_current` and `key_previous`.
3. Stop the compose orchestrator (`docker compose rm -sf orchestrator`) and boot an ephemeral orchestrator with BOTH `ORCHESTRATOR_API_KEY=key_current` AND `ORCHESTRATOR_API_KEY_PREVIOUS=key_previous` set, on `--network perpetuity_default --network-alias orchestrator`. Reuse the rest of the S04 swap shape (privileged, vol mounts, REDIS/DATABASE_URL). REAPER_INTERVAL_SECONDS can stay at the orchestrator default (no reaper interaction in this test).
4. Probe the ephemeral orchestrator from inside compose's `db` container (or a throwaway `docker run --network perpetuity_default --rm curlimages/curl curl -sf http://orchestrator:8001/v1/health`) until `image_present` is true. The probe path needs SOME container on the compose network; using the seeded compose db container as the probe host avoids spawning a throwaway curl image.
5. Boot TWO sibling backends on the same compose network — `backend_current` (env `ORCHESTRATOR_API_KEY=key_current`, `ORCHESTRATOR_API_KEY_PREVIOUS=`) and `backend_previous` (env `ORCHESTRATOR_API_KEY=key_previous`, `ORCHESTRATOR_API_KEY_PREVIOUS=`). Each gets its own host port and waits for `/api/v1/utils/health-check/` to respond 200. Critical: the compose `prestart` already ran during compose's initial bring-up (alembic migrations applied to the shared db), so the second backend's `bash scripts/prestart.sh` is a no-op upgrade — but we still run prestart for both to keep the boot shape identical to the existing fixture and avoid shape drift.
6. Sign up alice on `backend_current`. Sign up bob on `backend_previous` (different user — proves the test isn't accidentally reusing one backend twice). Use the standard signup helpers from S04.
7. Alice POST `/api/v1/sessions` via `backend_current` → 200 with sid_a. (HTTP path: backend_current → orchestrator with `X-Orchestrator-Key: key_current`. Orchestrator's `_key_matches` accepts because key_current is the active key.) Tear down sid_a with DELETE.
8. Bob POST `/api/v1/sessions` via `backend_previous` → 200 with sid_b. (HTTP path: backend_previous → orchestrator with `X-Orchestrator-Key: key_previous`. Orchestrator's `_key_matches` accepts because key_previous is in the candidates list.) Tear down sid_b with DELETE.
9. WS path proof — alice WS-attach to sid_a is no longer feasible since sid_a was deleted in step 7. Reprovision: alice POST again via `backend_current` → sid_a2. Open WS to `ws://localhost:<port_current>/api/v1/ws/terminal/{sid_a2}` with alice's cookies. The backend's WS-bridge code in `routes/sessions.py` proxies to orchestrator with `?key=key_current` query string. Assert `attach` frame received. Close. Same flow for bob's `sid_b2` against `backend_previous` (which proxies with `?key=key_previous`). Assert `attach` frame received. Close. Both DELETEs to clean up.
10. Negative case — boot a THIRD ephemeral sibling backend `backend_wrong` with `ORCHESTRATOR_API_KEY=<random_third_key>`. POST `/api/v1/sessions` as alice via `backend_wrong` → expect 503 with `{detail: "orchestrator_unavailable"}` or whatever shape the backend surfaces when orchestrator returns 401 (read `routes/sessions.py` to confirm the actual shape — do NOT hardcode the body shape; assert status == 503 OR the orchestrator-unauthorized branch the backend chose to surface). The orchestrator's `orchestrator_http_unauthorized` log line should appear in the ephemeral orchestrator's `docker logs` with `key_prefix=<first 4 chars>...`.
11. Log redaction sweep — same shape as T01 + S04. Capture `docker logs` for ephemeral_orchestrator + all three sibling backends; assert no email or full_name leaks.

Teardown (use `request.addfinalizer` so it runs even on assertion failure): `docker rm -f` all three sibling backends + the ephemeral orchestrator; `docker compose up -d orchestrator` to restore the compose orchestrator (use the existing `_restore_compose_orchestrator` pattern). Reap any workspace containers that ended up labelled to alice/bob. Same shape as S04's teardown.

Duration target: ≤120s wall-clock on warm compose. Boot of the ephemeral orchestrator + 3 sibling backends is the dominant cost (~30-45s); the actual auth assertions are ~5s of HTTP/WS round-trips.

Do NOT touch `backend/tests/integration/conftest.py` — keep T02's parameterized backend boot module-local. The conftest's `backend_url` fixture stays as-is for every other M002 e2e (S01/S02/S03/S04), which all use the dotenv ORCHESTRATOR_API_KEY value.

## Inputs

- ``backend/tests/integration/test_m002_s04_e2e.py` — copy `_boot_ephemeral_orchestrator`, `_restore_compose_orchestrator`, `_ensure_host_workspaces_shared`, `_compose`, `_docker`, signup helpers as module-local`
- ``backend/tests/integration/conftest.py` — read-only reference to `backend_url`'s `_docker run` shape; T02 reimplements it parameterized by api_key`
- ``orchestrator/orchestrator/auth.py` — read-only reference to confirm `_candidate_keys` accepts both current and previous when both are set`
- ``backend/app/api/routes/sessions.py` — read-only reference to confirm error shape when orchestrator returns 401 (drives the assertion in step 10)`
- ``backend/app/core/config.py` — read-only reference to confirm `ORCHESTRATOR_API_KEY_PREVIOUS` is wired through (it is — S01/T02 added it)`

## Expected Output

- ``backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` — new file containing the two-key rotation acceptance test, module-local helpers (parameterized `_boot_sibling_backend`, ephemeral-orchestrator-with-both-keys boot, log capture) and an autouse fixture restoring the compose orchestrator on teardown`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v

## Observability Impact

Asserts existing `orchestrator_http_unauthorized` log key fires for the wrong-key negative branch (already implemented in `auth.py`, this slice just exercises it via integration). Confirms `orchestrator_starting` + `orchestrator_ready` fire on the ephemeral orchestrator booted with both keys. Captures full `docker logs` for the ephemeral orchestrator + 3 sibling backends BEFORE teardown so the milestone-wide redaction sweep has the right blob.
