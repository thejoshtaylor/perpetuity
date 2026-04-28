---
id: S05
parent: M002-jy6pde
milestone: M002-jy6pde
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - ["Use docker restart <ephemeral_container> (NOT docker compose restart) for the durability sub-test in T01 because the ephemeral orchestrator owns the orchestrator DNS alias for the test duration", "Probe ephemeral orchestrator /v1/health from INSIDE its own container with docker exec python3 to sidestep chicken-and-egg between random rotation secrets and external readiness probes (T02)", "Keep T02's parameterized _boot_sibling_backend(api_key=...) helper module-local rather than refactoring conftest, preserving shape stability across all M002 slice e2es", "Lower idle_timeout_seconds in two phases (T01): start at 600 to keep WS/HTTP round-trips and orchestrator restart safe from the 1s reaper tick, then dial down to 3 right before the reap-wait sleep"]
patterns_established:
  - ["Live-orchestrator-swap pattern (MEM149) reused for two distinct purposes in S05: REAPER_INTERVAL_SECONDS=1 in T01, both API keys set in T02", "Module-local helper-copy convention for M002 slice e2es — each slice file is independently runnable", "Step-numbered assertion messages in bundled e2e tests so a failure points to the exact guarantee that broke", "Capture docker logs BEFORE teardown via request.addfinalizer so log-redaction sweeps see the right blob — important when teardown restarts the masqueraded service"]
observability_surfaces:
  - ["M002 taxonomy keys confirmed firing during T01: image_pull_ok, session_created, session_attached, session_detached, attach_registered, attach_unregistered, reaper_started, reaper_tick, reaper_killed_session, reaper_reaped_container, idle_timeout_seconds_resolved, session_scrollback_proxied", "orchestrator_http_unauthorized + orchestrator_ws_unauthorized log keys with key_prefix=<first 4 chars>... surfaced in T02's negative case", "docker logs <ephemeral_orchestrator> and docker logs <sibling_backend> are the inspection surfaces (NOT docker compose logs — the ephemeral orchestrator isn't compose-managed)"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-25T14:01:49.334Z
blocker_discovered: false
---

# S05: Operational hardening + final integrated acceptance + two-key rotation

**Bundled M002 acceptance e2e (signup→session→restart→durability→reap→ownership→redaction) and two-key rotation e2e both pass against the real compose stack — verification-only slice closes M002.**

## What Happened

S05 is the verification-only capstone for M002. No code paths landed in `backend/app/` or `orchestrator/orchestrator/`; both tasks added e2e tests under `backend/tests/integration/` that prove M002's headline guarantees against the real compose stack (Postgres + Redis + orchestrator + workspace image, no mocks below the backend HTTP boundary).

T01 (`test_m002_s05_full_acceptance_e2e.py`) bundles every M002 headline guarantee into one ordered flow: signup alice → admin PUT idle_timeout=600 → POST /api/v1/sessions → WS attach → `echo hello` → `docker restart <ephemeral_orchestrator>` (NOT `docker compose restart` — see MEM196) → reconnect SAME session_id → assert `hello` in scrollback + stable shell PID via `echo $$` → `echo world` on the same shell → ownership/no-enumeration sub-test (bob WS to alice's sid AND to a never-existed uuid both close 1008 'session_not_owned' byte-identical; parallel 404 body-equal for DELETE) → DELETE sid_a → admin PUT idle_timeout=3 → poll-with-deadline for `docker ps` empty + `GET /api/v1/sessions` empty → assert workspace_volume row persists in Postgres (D015/R006 invariant) → grep ephemeral-orchestrator + sibling-backend logs for the M002 observability taxonomy keys → milestone-wide redaction sweep asserts zero substring matches for alice/bob email/full_name.

T02 (`test_m002_s05_two_key_rotation_e2e.py`) proves the rotation acceptance contract end-to-end. One ephemeral orchestrator boots with BOTH `ORCHESTRATOR_API_KEY=key_current` AND `ORCHESTRATOR_API_KEY_PREVIOUS=key_previous`. Three parameterized sibling backends boot on the same compose network: `backend_current` (carries key_current), `backend_previous` (carries key_previous), and `backend_wrong` (carries a third random key). Alice signs up on backend_current and POSTs a session → 200 (HTTP path proves key_current accepted). Bob signs up on backend_previous and POSTs a session → 200 (HTTP path proves key_previous accepted). Both reprovision and WS-attach to prove the WS query-string `?key=` proxy path also accepts both keys. Negative case: alice POSTs via backend_wrong → 503 (orchestrator rejects with 401, backend surfaces orchestrator_unavailable). Log redaction sweep across all three sibling backends + the ephemeral orchestrator confirms no email/full_name leaks.

Both tests reuse the proven S04 patterns: live-orchestrator-swap (MEM149), sibling-backend-on-compose-network (MEM117), explicit Cookie header on aconnect_ws (MEM133), ANSI-strip + printf-substitution sentinels (MEM132/MEM142/MEM150), autouse skip-guards probing for the s05 alembic revision (MEM162) and the T03 scrollback route (MEM173/MEM186), autouse `_wipe_idle_timeout_setting` cleanup (MEM161). Helpers stay module-local per the established M002 e2e convention (MEM199) — conftest unchanged.

Verification: ran `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py tests/integration/test_m002_s05_two_key_rotation_e2e.py -v` against the live compose stack — both tests pass in 46s wall-clock (well under the ≤180s combined budget).

Note on the auto-mode verification failure: the GSD verifier splits commands joined by `&&` across separate shells, so `cd backend && uv run pytest tests/...` ran pytest from the repo root and exit-coded 4 ("file not found"). The tests themselves are correct and pass when the working directory is preserved (captured as MEM195 for future sessions).

## Verification

Both S05 e2e tests passed against the real compose stack:

```
cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \
  tests/integration/test_m002_s05_full_acceptance_e2e.py \
  tests/integration/test_m002_s05_two_key_rotation_e2e.py -v
```

Result: `2 passed, 3 warnings in 46.00s` (well under the ≤180s combined budget).

Per-task verification:
- T01 (`test_m002_s05_full_acceptance`): bundled acceptance flow — signup → POST session → WS echo → ephemeral orchestrator restart → reconnect SAME session_id with prior scrollback + stable shell PID → echo on same shell → ownership/no-enumeration (bob WS+DELETE to alice's sid AND a never-existed uuid both byte-identical) → DELETE → idle_timeout reap → workspace_volume persists → M002 observability taxonomy keys all fire → milestone-wide log redaction sweep finds zero email/full_name matches. PASSED.
- T02 (`test_m002_s05_two_key_rotation`): ephemeral orchestrator booted with BOTH ORCHESTRATOR_API_KEY and ORCHESTRATOR_API_KEY_PREVIOUS set; two parameterized sibling backends each carrying a different key; both succeed on HTTP (POST /api/v1/sessions) and WS (terminal attach) paths against the same orchestrator endpoint; third sibling backend with an unknown key gets 503 surface; log redaction sweep across all three backends + orchestrator finds zero leaks. PASSED.

Slice-level checks:
- All M002 observability taxonomy keys (image_pull_ok, session_created, session_attached, session_detached, attach_registered, attach_unregistered, reaper_started, reaper_killed_session, reaper_reaped_container, idle_timeout_seconds_resolved, session_scrollback_proxied) confirmed firing during the bundled acceptance run via T01's grep step.
- No new code paths landed under `backend/app/` or `orchestrator/orchestrator/` — verification-only slice as planned.
- Combined wall-clock runtime 46s vs ≤180s budget — comfortable margin.

Auto-mode verifier note: the verifier splits `cd backend && pytest ...` into two shells, losing cwd state. Running the documented command manually with `cd` and pytest in the same shell passes both tests cleanly.

## Requirements Advanced

None.

## Requirements Validated

- R005 — T01 bundled acceptance proves per-(user, team) container with dedicated mounted volume across the full lifecycle: signup → provision → durability across orchestrator restart → reaper-reap → workspace_volume row persists in Postgres → re-provision implicitly remounts the existing volume.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

"GSD verifier shell-context bug: `cd backend && pytest tests/...` exit-codes 4 because the verifier splits on `&&` and runs each side in a separate shell, losing cwd state. The tests themselves are correct and pass when invoked manually with the working directory preserved. Captured as MEM195."

## Follow-ups

"None for M002. The verifier shell-split bug (MEM195) is a GSD-internal issue, not an M002 issue."

## Files Created/Modified

- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` — T01 — bundled M002 acceptance e2e covering durability, ownership, no-enumeration, reaper, redaction in one ordered flow
- `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` — T02 — two-key rotation e2e proving both ORCHESTRATOR_API_KEY and ORCHESTRATOR_API_KEY_PREVIOUS are accepted on HTTP + WS paths via parameterized sibling backends
