---
estimated_steps: 16
estimated_files: 2
skills_used: []
---

# T04: End-to-end demo: two WS sessions share a container, reaper kills idle survivor + reaps container, next POST remounts existing volume

Land the slice's demo-truth integration test in `backend/tests/integration/test_m002_s04_e2e.py`, marked `e2e`. Reuses the e2e harness pattern from S03 (sibling backend container on `perpetuity_default`, real Postgres + real Redis + real orchestrator + real Docker daemon — no mocks, no swapping orchestrator containers). The orchestrator stays on its compose-default settings except that the test admin-PUTs `idle_timeout_seconds=3` (the new T02 admin setting) so the reaper trips quickly. The compose orchestrator is also (re)started with `REAPER_INTERVAL_SECONDS=1` for the test run — set this via `docker compose up -d --force-recreate orchestrator` with `-e REAPER_INTERVAL_SECONDS=1` overriding compose, OR via a `compose.override.yml` test file the conftest writes — choose the env-var override path (simpler, no file to clean up).

Flow (single `async def test_s04_full_demo`):
  1. Promote / use the seeded admin@example.com to system_admin and log in. Sign up `alice` (RFC2606 example.com per MEM131). Both via the sibling backend on `backend_url`.
  2. As admin PUT `idle_timeout_seconds=3` → assert 200, value=3. Belt-and-suspenders: an autouse fixture also does `DELETE FROM system_settings WHERE key='idle_timeout_seconds'` before AND after the test (MEM161 — compose's app-db-data persists across runs).
  3. As alice POST /api/v1/sessions twice (different team would be wrong — same personal team_id from signup so the same container is reused) → got two session_ids, sid_a and sid_b. Assert orchestrator response.created==True for the first, False for the second (T03/MEM120 — same container reused, distinct tmux sessions inside).
  4. WS attach to sid_a, send `echo 'a' > /workspaces/<team_id>/marker.txt && cat /workspaces/<team_id>/marker.txt\n` then close. WS attach to sid_b, send `ls /workspaces/<team_id>/marker.txt && cat /workspaces/<team_id>/marker.txt\n`, ANSI-strip and assert `'a'` in the data-frame stream — proves multi-tmux/single-container filesystem sharing (R008/MEM120).
  5. GET /api/v1/sessions → assert response.data has exactly 2 items, both belonging to alice, set of ids == {sid_a, sid_b}.
  6. GET /api/v1/sessions/{sid_a}/scrollback (the new T03 endpoint) → assert 200 with `scrollback` non-empty containing the echoed marker contents. Negative: GET /api/v1/sessions/{sid_a}/scrollback as a different user (sign up bob mid-test) → 404 with body identical to a missing-session GET.
  7. DELETE /api/v1/sessions/{sid_a} → assert 200. GET /api/v1/sessions → assert exactly 1 item, == sid_b. Assert the workspace container is STILL running (`docker ps --filter label=team_id=<tid>` returns 1).
  8. Wait for the reaper to act on sid_b. The two-phase check requires (a) Redis last_activity > idle_timeout (3s) AND (b) attach_map shows no live attach. Since we closed the WS attach for sid_b in step 4, the attach_map is empty for sid_b; the heartbeat was last bumped at the close. Sleep 5s (3s timeout + 1s interval + 1s buffer). Assert: GET /api/v1/sessions returns empty data; `docker ps --filter label=team_id=<tid> --filter label=user_id=<uid>` returns zero rows (container reaped).
  9. Volume persists: as alice POST /api/v1/sessions → 200 sid_c. WS attach, send `cat /workspaces/<team_id>/marker.txt\n`, assert `'a'` in the data frame — proves the workspace_volume row + .img were not destroyed by the reap and the new container remounted the existing volume (D015 invariant + R006 + the slice success criterion).
 10. Log redaction sweep (MEM134): `docker compose logs orchestrator backend` (since the test start; fixture timestamps the run). grep for alice's email and full_name in the captured log output → assert zero matches across all reaper/attach/scrollback log lines that this test newly exercised. Match the S01/S03 pattern.

Wall-clock budget: ≤45s (sleep budget alone is ~6s; everything else is HTTP/WS round-trips against a warm container). Test is marked `pytest.mark.serial` if any other e2e test PUTs idle_timeout_seconds (none currently do, but defend with the autouse delete fixture).

The test must NOT mock anything below the backend HTTP boundary — the slice acceptance demands the real reaper trips on the real Docker daemon. Validate the orchestrator emitted at least one `reaper_killed_session` and one `reaper_reaped_container` log line for the test's session/container by grep on `docker compose logs orchestrator --since=<test_start_iso>` after step 9.

Docstring at the top must state the exact run command (mirror S03's docstring) so future agents can reproduce: `docker compose build backend orchestrator && docker compose up -d --force-recreate orchestrator -e REAPER_INTERVAL_SECONDS=1 && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py -v`.

Also run the existing S01/S02/S03 e2e tests as a regression bar — they must remain green with the reaper running (REAPER_INTERVAL_SECONDS=1 default for test compose). If any flake on the new short reaper interval, that is a real bug in T01/T02 to fix, not a test to weaken.

## Inputs

- ``backend/tests/integration/conftest.py``
- ``backend/tests/integration/test_m002_s03_settings_e2e.py``
- ``backend/tests/integration/test_m002_s01_e2e.py``
- ``backend/app/api/routes/sessions.py``
- ``orchestrator/orchestrator/reaper.py``
- ``orchestrator/orchestrator/attach_map.py``

## Expected Output

- ``backend/tests/integration/test_m002_s04_e2e.py``
- ``backend/tests/integration/conftest.py``

## Verification

docker compose build backend orchestrator && docker compose up -d --force-recreate orchestrator && cd backend && POSTGRES_PORT=5432 REAPER_INTERVAL_SECONDS=1 uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s02_volume_cap_e2e.py tests/integration/test_m002_s03_settings_e2e.py -v

## Observability Impact

Test asserts on log lines emitted by T01 (`attach_registered`/`attach_unregistered`), T02 (`reaper_started`, `reaper_tick`, `reaper_killed_session`, `reaper_reaped_container`, `idle_timeout_seconds_resolved`), and T03 (`session_scrollback_proxied`). The redaction sweep at step 10 is the slice-level proof that all new log keys honor MEM134's UUID-only rule — any leak fails the test.
