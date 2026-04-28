---
id: T02
parent: S05
milestone: M002-jy6pde
key_files:
  - backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py
key_decisions:
  - Switched the ephemeral-orchestrator readiness probe from `docker exec perpetuity-db-1 wget ...` (plan-called) to `docker exec <eph_name> python3 -c urllib.request.urlopen(...)` because the postgres image lacks wget/curl and the busybox sh has no /dev/tcp. Probing /v1/health from inside the orchestrator container itself works because /v1/health is in `_PUBLIC_PATHS` (auth.py L45-49) and python3+urllib are always present in the orchestrator's python:3.12-slim base image — sidesteps the chicken-and-egg between the test's randomly-generated keys and readiness detection. Captured as MEM194.
  - Tightened the negative-case assertion to status==502 with a permissive substring check on the detail (`'orchestrator' in detail.lower()`) rather than pinning the exact phrase `orchestrator_rejected_create`. Future-proofs against a refactor of the wrapper string while still asserting the contract: orchestrator returns 401 → backend wraps as 502, never as 200 or 503.
  - Added an extra log-shape assertion (`key_prefix=<first 4 of wrong key>...` is present in the ephemeral orchestrator's logs AND the full wrong key is NOT present anywhere in any captured log) on top of the plan's `orchestrator_http_unauthorized` smoke check. Costs nothing and pins the auth.py `_key_prefix` redaction discipline (only first 4 chars + ellipsis) — a future regression that logs the full secret would now fail this test loudly.
duration: 
verification_result: passed
completed_at: 2026-04-25T13:57:40.834Z
blocker_discovered: false
---

# T02: Add two-key rotation e2e proving both ORCHESTRATOR_API_KEY and _PREVIOUS are accepted on HTTP and WS paths against the same live orchestrator

**Add two-key rotation e2e proving both ORCHESTRATOR_API_KEY and _PREVIOUS are accepted on HTTP and WS paths against the same live orchestrator**

## What Happened

Landed `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` proving the D016/MEM096 zero-downtime rotation contract end-to-end against the real compose stack. The test boots an ephemeral orchestrator carrying BOTH `ORCHESTRATOR_API_KEY=key_current` AND `ORCHESTRATOR_API_KEY_PREVIOUS=key_previous` (using the live-orchestrator-swap pattern from MEM149/MEM188), then boots three sibling backends in parallel-shaped (sequential boot) — `backend_current` carrying `key_current`, `backend_previous` carrying `key_previous`, and `backend_wrong` carrying a fully-random unrecognized key. Alice signs up on backend_current and bob on backend_previous (different users on different backends to prove neither path accidentally reuses one backend twice). The HTTP path is exercised via POST + DELETE /api/v1/sessions on each of the two valid backends and the contract holds: both succeed against the same orchestrator endpoint because `_candidate_keys()` accepts both. The WS path is then exercised by reprovisioning sessions on each backend and opening WS to /api/v1/ws/terminal/{sid}, asserting an `attach` frame on each — the backend's WS-bridge proxies as `?key=<settings.ORCHESTRATOR_API_KEY>` so backend_current sends `?key=key_current` and backend_previous sends `?key=key_previous`. The negative case asserts backend_wrong's POST surfaces as 502 `orchestrator_rejected_create` (sessions.py L183-189 wraps any non-2xx, non-5xx orchestrator response that way); the orchestrator's `orchestrator_http_unauthorized path=/v1/sessions key_prefix=<first 4>...` log line MUST fire on the ephemeral orchestrator, and the full wrong key MUST NOT appear anywhere in any captured log (only its 4-char prefix is permitted). Closes with the milestone-wide redaction sweep across the ephemeral orchestrator + all three sibling backends — zero email/full_name leaks. Module-local helpers (`_boot_ephemeral_orchestrator_dual_key`, `_boot_sibling_backend(api_key=...)`, `_wait_for_orch_running_self`) keep T02 self-contained; the conftest's `backend_url` fixture stays pinned to dotenv ORCHESTRATOR_API_KEY for every other M002 e2e per the plan's no-conftest-touch rule.

Single deviation from the plan: readiness-probe vehicle. The plan called for probing the ephemeral orchestrator from inside the compose `db` container, but the postgres image lacks both `wget` and `curl` (and its busybox sh has no `/dev/tcp`). Switched to `docker exec <ephemeral_orchestrator> python3 -c urllib.request.urlopen(...)` — orchestrator's image is python:3.12-slim so python3+urllib is always present, and /v1/health is in `_PUBLIC_PATHS` (auth.py L45-49) so the probe doesn't need either of the two test-only keys. Captured as MEM194 for future agents. Wall-clock: 16.21s on warm compose, well under the ≤120s slice budget.

## Verification

Ran the slice's specified verification command: `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v` — 1 passed in 16.21s. The test transparently exercises every contract surface in one ordered flow: signup on two different backends (proves backends boot with different keys), POST + DELETE on each backend (HTTP-path proof for both keys), reprovision + WS-attach on each backend (WS-path proof for both keys), POST via backend_wrong → 502 `orchestrator_rejected_create` (negative-branch proof), `orchestrator_http_unauthorized key_prefix=<4 chars>...` log assertion (existing observability key fires for the wrong-key branch), full-key-not-in-logs assertion (only 4-char prefix permitted), and the milestone-wide redaction sweep (zero email/full_name leaks across the ephemeral orchestrator + all three sibling backends). Pre-flight `pytest --collect-only` validated imports + signatures cleanly before the live run. The slice's other verification checks (test_m002_s05_full_acceptance_e2e + the broader slice-level Verification section) were green at T01 completion and are not regressed by this task — T02 only adds a new file, touches no other code.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v` | 0 | ✅ pass | 16210ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest --collect-only tests/integration/test_m002_s05_two_key_rotation_e2e.py` | 0 | ✅ pass | 10ms |

## Deviations

Plan step 4 said to probe ephemeral orchestrator readiness from inside the compose `db` container. The postgres image used by `db` lacks `wget`, `curl`, and its busybox sh has no `/dev/tcp`, so a shell-based HTTP probe cannot work there without spawning a throwaway curlimages/curl container (the plan's other suggestion). Switched to `docker exec <ephemeral_orch> python3 -c urllib.request.urlopen('http://127.0.0.1:8001/v1/health')` — same DNS-free probe path, no extra container, and /v1/health bypasses shared-secret auth so the test's randomly-generated keys don't need to leak into the probe. Documented inline in `_wait_for_orch_running_self`'s docstring and captured as MEM194 for future agents.

## Known Issues

none

## Files Created/Modified

- `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py`
