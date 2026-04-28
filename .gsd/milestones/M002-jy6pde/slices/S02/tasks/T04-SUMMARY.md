---
id: T04
parent: S02
milestone: M002-jy6pde
key_files:
  - backend/tests/integration/test_m002_s02_volume_cap_e2e.py
key_decisions:
  - Live-orchestrator swap (compose rm + ephemeral docker run with --network-alias orchestrator + DEFAULT_VOLUME_SIZE_GB=1, then compose up -d to restore the 4 GiB default) is the cleanest way to exercise both caps in one e2e — backend resolves orchestrator DNS at request time so it survives the swap. Captured in MEM143.
  - Sentinels in WS-piped shell commands must be built via printf string-substitution (e.g. `printf 'EN%sOK_%s' D <uuid>` produces ENDOK_<uuid>) so the literal substring is not present in the typed input that tmux echoes back. Otherwise `_drain_data` returns on the echo before the shell actually executes. Captured in MEM142.
  - Backend image must be rebuilt whenever a new alembic revision lands (s04 here) — the image bakes in /app/backend/app/alembic/versions/, so a stale image fails prestart with `Can't locate revision identified by 's04_workspace_volume'`. Not a T04 bug; pre-existing convention but bit us during first verification run.
  - End-marker for dd output uses a uuid suffix per run so consecutive test runs don't accidentally match a stale prior frame; the marker is constructed as `EN`+printf%s(`D`)+`OK_`+uuid so the literal `ENDOK_<uuid>` substring is never present in the typed bytes.
duration: 
verification_result: passed
completed_at: 2026-04-25T11:22:19.904Z
blocker_discovered: false
---

# T04: Add M002/S02 e2e acceptance test proving 1 GiB ENOSPC hard cap on alice's workspace, 4 GiB neighbor isolation for bob, DB rows match disk, container resource limits hold, and zero PII in compose logs.

**Add M002/S02 e2e acceptance test proving 1 GiB ENOSPC hard cap on alice's workspace, 4 GiB neighbor isolation for bob, DB rows match disk, container resource limits hold, and zero PII in compose logs.**

## What Happened

Created `backend/tests/integration/test_m002_s02_volume_cap_e2e.py` — the slice S02 demo-truth test. It runs the full slice success criteria against the real compose stack (no mocks): real Postgres, real Redis, real Docker daemon, real loopback-ext4 + privileged orchestrator.

**Phase A (alice, 1 GiB cap)**: stop compose orchestrator with `docker compose rm -sf orchestrator`, launch an ephemeral orchestrator on `perpetuity_default` with `--network-alias orchestrator`, `--privileged` (MEM136), the same `/var/lib/perpetuity/{vols,workspaces}` binds (rshared on workspaces per MEM139), and `DEFAULT_VOLUME_SIZE_GB=1`. Sign up alice (RFC-2606 example.com email per MEM131), POST /api/v1/sessions, attach to the WS, send `dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100` with stderr captured to /tmp/dd.err, then printf a uuid-keyed end-marker. Assert the dd output contains `no space left on device`, `dd` exited non-zero, and `stat -c %s` reports ≤ 1.05 × 1024^3 bytes (and ≥ 0.90 × 1024^3 — sanity that the cap is real, not 0). Verify the workspace_volume row has `size_gb=1` and a uuid-keyed `img_path` under `/var/lib/perpetuity/vols/`. Assert HostConfig.Memory=2 GiB, PidsLimit=512, NanoCpus=1e9 on alice's container.

**Phase B (bob, 4 GiB cap)**: kill the ephemeral orchestrator, `docker compose up -d orchestrator` (default 4 GiB), wait healthy + DNS reachable from inside the sibling backend container. Sign up bob (different team), POST /api/v1/sessions, WS-attach, run `df -BG /workspaces/<team>` and `ls -la`. Assert df total is 3-4 GiB (allowing for ext4 metadata overhead), Use% < 10 (near-empty), and bob's ls section does NOT contain a `big` entry — the per-(user, team) .img files are independent. Assert bob's volume row has `size_gb=4` and a different `img_path` than alice's. Assert alice's .img is still on disk inside the (re-spawned) orchestrator after the swap — `stat <alice_img_path>` returns ≥ 1 GiB.

**Final assertions**: DELETE both sessions (200), capture `docker compose logs orchestrator backend`, assert ZERO substring matches for alice/bob email/full_name (MEM134 redaction sweep), assert observability taxonomy keys `volume_provisioned` and `volume_mounted` are present. Suite wall-clock guard at 180 s (slice budget is 60 s; tolerated higher because compose orchestrator restart is the slow path).

**Two iterations to green**: first run failed because the backend image was built before T01's s04 migration landed, so prestart's alembic run errored on `Can't locate revision identified by 's04_workspace_volume'`. Rebuilt with `docker compose build backend`. Second run failed because tmux echoes typed input verbatim and the test's literal sentinel substring `MARK_END` appeared in the input echo before the shell had actually run dd — `_drain_data` returned immediately with only the echoed command. Fixed by building sentinels via `printf 'EN%sOK_%s\\n' D <uuid>` so the substring `ENDOK_<uuid>` only appears once stdout flushes (captured in MEM142). Third run passed. Re-ran a fourth time to confirm idempotency (the test creates fresh users per run, so re-running does not collide). S01 e2e re-ran green to prove no regression.

The cleanup finalizer reaps the ephemeral orchestrator (if Phase A bailed early), restores the compose orchestrator, and removes any workspace containers spawned during the test (label-scoped `perpetuity.managed=true`). On a second run, the previously-created workspace_volume rows for each prior alice/bob pair stay in Postgres (uuid-keyed, harmless) and their .img files survive — symmetric with T03's idempotency model.

Slice goal proven end-to-end: kernel-enforced per-volume hard cap, neighbor isolation across (user, team) pairs, workspace_volume row matches the on-disk .img, container resource limits hold, and observability logs do not leak PII.

## Verification

Ran the slice plan's exact verification command twice from `backend/`:

`uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v`

Both runs passed (17.87s and 15.68s — well under the 60s slice budget). The test exercises the demo flow described in the slice plan verbatim: sign up two fresh users, provision a 1 GiB-cap workspace, observe ENOSPC at the kernel boundary, provision a 4 GiB neighbor, observe isolation, confirm DB matches disk and container limits and log redaction.

Also verified S01 e2e still green (regression) and ruff is clean on the new file.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v` | 0 | ✅ pass (1 passed in 17.87s — first green run after rebuilding backend image with s04) | 17870ms |
| 2 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v` | 0 | ✅ pass (1 passed in 15.68s — idempotency confirmed) | 15680ms |
| 3 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` | 0 | ✅ pass (1 passed in 19.94s — S01 regression check) | 19940ms |
| 4 | `cd backend && uv run ruff check tests/integration/test_m002_s02_volume_cap_e2e.py` | 0 | ✅ pass (All checks passed) | 100ms |
| 5 | `docker compose build backend` | 0 | ✅ pass (rebuilt with s04 migration so prestart alembic upgrade can find head) | 12000ms |

## Deviations

Plan said the orchestrator would expose a per-request `size_gb_override` knob via `TEST_DEFAULT_VOLUME_SIZE_GB` settings field, and the test would exercise both alice (1 GiB) and bob (4 GiB) on the SAME orchestrator by passing the override on alice's POST. T03 settled on `DEFAULT_VOLUME_SIZE_GB` as a boot-time env on the orchestrator (no per-request override). The test adapts: phase A swaps in an ephemeral orchestrator with DEFAULT_VOLUME_SIZE_GB=1 for alice, then phase B restores compose default 4 GiB for bob. Same demo-truth, different mechanic. The sibling backend container resolves orchestrator DNS at request time so the swap is invisible from the backend's perspective. Plan's `docker inspect` step ran `docker inspect <alice_container_id>` via subprocess against compose's docker socket — kept that, but pulled it from the host's docker (the test runner already has the host docker CLI). Plan suggested negative tests for size_gb=0 and size_gb>256 — those rejection paths are wired at the orchestrator's volume_store layer (raises ValueError → 422 from the route) but exercising them requires either a per-request override (which T03 didn't expose) or a third orchestrator boot with the bad env, which would push the test well over budget. Deferred those negative cases to the orchestrator-level integration suite (test_volumes.py) where they already live; the e2e test focuses on the slice's demo-truth statement.

## Known Issues

Stale backend image (built before s04 landed) fails prestart with `Can't locate revision identified by 's04_workspace_volume'`. Not a T04 issue, but anyone running the e2e fresh after T01-T03 must `docker compose build backend` first. The conftest could ideally re-tag the image with a content hash and skip the test cleanly when the image is stale, but that's a future hardening — for now the assertion message includes the alembic error so a future agent sees it immediately. Test must run with `-n 1` (default serial); concurrent runs would exhaust kernel loop devices at ~4× concurrency (T02 budget). Not enforced in pytest config; documented in module docstring.

## Files Created/Modified

- `backend/tests/integration/test_m002_s02_volume_cap_e2e.py`
