---
estimated_steps: 13
estimated_files: 2
skills_used: []
---

# T04: End-to-end loopback hard-cap acceptance test (the slice demo)

Land the integration test that proves the slice success criteria verbatim. Reuses the e2e harness pattern from `backend/tests/integration/test_m002_s01_e2e.py` (sibling backend container on `perpetuity_default`, ephemeral orchestrator, real Postgres + real Redis + real Docker daemon — no mocks).

File: `backend/tests/integration/test_m002_s02_volume_cap_e2e.py`. Marked with the `e2e` pytest marker. Skipped if Docker unreachable (same fixture pattern as S01's e2e).

Flow:
  1. Sign up TWO fresh users via M001 endpoints (alice + bob, both at example.com per MEM131 — `email_validator` rejects .local).
  2. POST /api/v1/sessions for alice with `size_gb_override=1` (a test-only env override on the orchestrator, exposed as `TEST_DEFAULT_VOLUME_SIZE_GB` and consumed in T03's settings). Capture alice's session_id and container_id from the response.
  3. WS-attach as alice (cookie-authed, RFC-2606 example.com address, explicit Cookie: header per MEM133). Send `dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100\n` and read until the prompt comes back. Assert the dd output contains `No space left on device` AND `stat -c %s /workspaces/<team>/big` returns a value ≤ 1.05 * 1024^3 (≤ ~1.05 GB).
  4. POST /api/v1/sessions for bob (different team, default size_gb=4). Run `df -BG /workspaces/<team>` inside bob's container; assert the reported total is 4 GB and `Use%` is single-digit. Run `ls /workspaces/<team>/` — must NOT see alice's `big` file (independent .img per (user, team)).
  5. Query Postgres directly through the test backend's session: `SELECT size_gb, img_path FROM workspace_volume WHERE user_id=<alice.id> AND team_id=<alice.personal_team>` — assert size_gb=1 AND img_path matches the orchestrator's `/var/lib/perpetuity/vols/<volume_id>.img` shape.
  6. Run `docker inspect <alice_container_id>` from the test (via subprocess against compose's docker socket — the test runs from the host) and assert `HostConfig.Memory == 2147483648`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1000000000`.
  7. Log redaction sweep (mirrors S01's T06): `docker compose logs orchestrator backend | grep -E '<alice.email>|<alice.full_name>|<bob.email>|<bob.full_name>'` — assert ZERO matches across orchestrator and backend logs (MEM134).
  8. Cleanup: DELETE both sessions; orchestrator should unmount alice's volume on session-tear-down ONLY if no live sessions remain on the container — but per S01's lifecycle, container reaping is S04's job, so for now the volume stays mounted and the test does not assert tear-down of the .img. The orchestrator process (next test run) finds the existing row+.img and reuses both — that's the idempotency path covered in T03 unit tests, asserted indirectly here by the test fixture's label-scoped cleanup not touching the volumes.

Wall-clock budget: ≤ 60 s per the milestone success criterion. The 1-GB mkfs.ext4 is the slowest single step (~500 ms); dd of 1100 MB is bounded by ENOSPC and exits within ~3 s. Total expected ≈ 25-35 s, comfortably under budget.

This test is the demo-truth statement: 'a workspace with size_gb=1 honors a kernel-enforced hard cap, neighbors are isolated, the workspace_volume row matches disk, container resource limits hold, and observability logs do not leak PII.' If every prior task is complete and this test passes, the slice goal is true.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| sibling backend container | `pytest.fail` with the container's `docker logs` tail attached so a future agent can see WHY the backend never came up | Health-poll timeout 60 s → fail | N/A |
| compose orchestrator | `pytest.fail` with `docker compose logs orchestrator | tail -100` | Health-poll timeout 60 s → fail | N/A |
| compose Postgres | `pytest.fail` with the connection error | 30 s connect timeout → fail | N/A |
| WS attach (httpx_ws) | `pytest.fail` with the close code/reason and tailing 50 frames received | Frame read deadline 15 s → fail | malformed JSON frame on data path → fail with `repr(frame)` (S01 frame schema is locked, so this should never fire) |

## Load Profile

- **Shared resources**: dev host (Postgres + Redis + Docker socket + 2 workspace containers + 1 sibling backend); kernel loop devices (T02 budget); ~5 GB on-disk apparent volume (sparse — alice's 1 GB consumed entirely by the dd write, bob's 4 GB barely touched).
- **Per-operation cost**: 2 signups, 2 session POSTs, 2 WS attaches, 1 dd of ~1 GB (real disk write — this is the most expensive single op in the test), 1 docker inspect, 1 docker logs grep. Expected wall clock 25-35 s.
- **10x breakpoint**: running this test 10× concurrently would hit kernel loop-device exhaustion (T02 budget) at ~4 concurrent runs; pytest serializes within a worker so this is only a concern for `-n auto` parallel runs. Document that the e2e test must run with `-n 1` (or default serial).

## Negative Tests

- **Malformed inputs**: dd writing to a path outside `/workspaces/<team>/` (e.g., `/etc/passwd`) — covered by container's read-only root-fs assumptions; assert that an attempt to write to `/etc/foo` inside the workspace returns `Permission denied` rather than `No space left on device` (proves the cap is on the workspace mount, not the container's overlay).
- **Error paths**: provision attempt with size_gb=0 (rejected at the orchestrator layer with 422); provision attempt with size_gb > 256 (rejected). Volume already mounted from a previous run (idempotent — assert second run does not blow up).
- **Boundary conditions**: alice's dd hits ENOSPC at exactly the ext4-usable boundary (size_gb=1 with `-m 0` mkfs gives ~1.0 GB usable; assert ≤ 1.05 × 1024^3). Bob's `df` reports total ≈ 4 GB (allow ±5% for ext4 metadata overhead). Log-redaction sweep is exact-string match (no regex bypass).

## Inputs

- ``backend/tests/integration/test_m002_s01_e2e.py``
- ``backend/tests/integration/conftest.py``
- ``orchestrator/orchestrator/volume_store.py``
- ``orchestrator/orchestrator/volumes.py``
- ``orchestrator/orchestrator/sessions.py``
- ``backend/app/alembic/versions/s04_workspace_volume.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/tests/integration/test_m002_s02_volume_cap_e2e.py``
- ``backend/tests/integration/conftest.py``

## Verification

cd backend && uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v

## Observability Impact

Test asserts presence of INFO `volume_provisioned`, `volume_mounted`, `container_provisioned` log keys for both alice and bob in `docker compose logs orchestrator`. Asserts ABSENCE of any log line containing alice's or bob's email/full_name (MEM134 redaction sweep). On test failure, the test prints the last 100 lines of orchestrator + backend logs to give a future agent a localized view of what went wrong without re-running the entire stack.
