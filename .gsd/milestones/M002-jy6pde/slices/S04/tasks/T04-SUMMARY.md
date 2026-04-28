---
id: T04
parent: S04
milestone: M002-jy6pde
key_files:
  - backend/tests/integration/test_m002_s04_e2e.py
  - backend/pyproject.toml
key_decisions:
  - Used the live-orchestrator-swap pattern from S02 (MEM149) to inject REAPER_INTERVAL_SECONDS=1, NOT a docker-compose.override.yml — the swap is already proven in S02 and adds no test-only file to clean up. Compose's orchestrator service has no env hook for REAPER_INTERVAL_SECONDS so a plain `docker compose up -d` cannot override it.
  - Two-phase idle_timeout PUT (600 up-front, 3 right before the reap wait) — single 3 s up-front races the reaper on a 1 s tick and step 6's scrollback fetch finds an already-reaped session; resolved as MEM175.
  - Stale-backend-image autouse skip-guard (probes for the T03 scrollback substring) added because a T04 dev iteration burned ~30 min on a misleading FastAPI default 404; mirrors the S03 alembic-revision skip-guard (MEM162). Captured as MEM173.
  - Test passes ?team_id explicitly to GET /api/v1/sessions to work around the orchestrator-422 (MEM174) — pre-existing backend bug filed as memory rather than fixed mid-task because it's outside T04's scope (T04 is the e2e demo, not a list-route refactor).
duration: 
verification_result: passed
completed_at: 2026-04-25T13:17:17.870Z
blocker_discovered: false
---

# T04: Add S04 e2e acceptance test driving two WS sessions in one container, reaper-killed survivor + container reap, and re-provision-onto-existing-volume against the live compose stack

**Add S04 e2e acceptance test driving two WS sessions in one container, reaper-killed survivor + container reap, and re-provision-onto-existing-volume against the live compose stack**

## What Happened

Landed `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` — a single async test that drives the full S04 demo against the real compose stack: the seeded admin PUTs `idle_timeout_seconds`, alice signs up and POSTs two sessions for her personal team, the test WS-attaches to both and proves multi-tmux/single-container filesystem sharing (R008/MEM120) by writing a marker through sid_a and reading it back through sid_b, GET /api/v1/sessions returns both, the new T03 GET /api/v1/sessions/{sid}/scrollback proxy returns alice's marker AND a freshly-signed-up bob gets a 404 with a body bit-for-bit equal to a missing-session GET (no enumeration), DELETE one session leaves the sibling AND the container alive, then a PUT idle_timeout_seconds=3 + ~6 s wait lets the reaper kill the surviving tmux session and reap the container; finally a third POST /api/v1/sessions re-provisions the container and the WS attach `cat`s the same marker — proving the workspace_volume row + .img persisted across the reap (D015/R006). Marker `reaper_killed_session` and `reaper_reaped_container` log lines are asserted on the ephemeral orchestrator. The MEM134 redaction sweep grep across both backend and orchestrator logs catches any email/full_name leak. Used the live-orchestrator-swap pattern from S02 (MEM149) to inject REAPER_INTERVAL_SECONDS=1 since compose's orchestrator service has no env hook for it; the test fixture restores the compose orchestrator on teardown. Added two autouse fixtures: a stale-image probe that skips with `docker compose build backend` guidance when the new T03 scrollback route isn't baked in (cost ~30 min during dev to a confusing FastAPI default 404 — captured as MEM173), and a system_settings DELETE-before-and-after for `idle_timeout_seconds` per MEM161. Implementation surfaced a real-but-pre-existing backend bug: GET /api/v1/sessions without ?team_id surfaces as 503 orchestrator_status_422 because the orchestrator's GET /v1/sessions requires both (user_id, team_id) — captured as MEM174 and worked around in the test by always passing alice_team explicitly. Also discovered that PUT idle_timeout_seconds=3 cannot be set up-front on a 1 s reaper tick or the prep steps (WS / scrollback / list) race the reaper — adopted a two-phase strategy (PUT 600 up-front, PUT 3 right before the reap wait) captured as MEM175. Pyproject markers got `serial` registered alongside `e2e`. Test passes in ~18 s on warm compose (well under the 45 s slice budget); when run after S01/S02/S03 in the same pytest session it can hit pre-existing host loop-device exhaustion (>32 attached) — same constraint S02 documents.

## Verification

Ran `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py -v` against the live compose stack (db, redis, orchestrator) with backend:latest freshly built — passes in 18.59 s. The ephemeral orchestrator runs with REAPER_INTERVAL_SECONDS=1 swapped in via the network-alias pattern, so the reaper trips on the test's 3 s idle timeout. All ten step assertions pass: admin PUT 200, two POST /api/v1/sessions both reuse the same docker labelled (user_id, team_id) container, WS attach to sid_a writes the marker, WS attach to sid_b cats it (proving filesystem sharing), GET /api/v1/sessions returns set {sid_a, sid_b}, GET scrollback returns the marker, no-enumeration negative case (bob hits sid_a + missing UUID) returns identical body, DELETE sid_a leaves sid_b and the container alive, after the second PUT to 3 s + sleep 6 s the GET returns empty AND the container is removed, post-reap POST creates sid_c which re-attaches and reads the persisted marker. The asserted log lines `reaper_killed_session`, `reaper_reaped_container`, `attach_registered`, `attach_unregistered`, `idle_timeout_seconds_resolved`, `session_scrollback_proxied` all fire. The MEM134 redaction sweep finds zero occurrences of alice's or bob's email/full_name in backend or orchestrator logs. Ran S01 + S04 in sequence after a clean orchestrator restart — both pass (38.62 s total).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run ruff check tests/integration/test_m002_s04_e2e.py` | 0 | ✅ pass | 800ms |
| 2 | `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py -v` | 0 | ✅ pass | 18590ms |
| 3 | `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s04_e2e.py -v -p no:randomly (after compose restart orchestrator)` | 0 | ✅ pass | 38620ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/integration/test_m002_s04_e2e.py`
- `backend/pyproject.toml`
