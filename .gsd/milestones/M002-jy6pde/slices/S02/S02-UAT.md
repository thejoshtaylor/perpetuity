# S02: Loopback-ext4 hard-cap volumes + per-container resource limits — UAT

**Milestone:** M002-jy6pde
**Written:** 2026-04-25T11:30:09.667Z

# M002/S02 — UAT: Loopback-ext4 hard-cap volumes + per-container resource limits

## Preconditions

- Live `docker compose` stack on the dev host with `db`, `redis`, `backend`, `orchestrator` services healthy.
- Backend image rebuilt against the s04 alembic revision (`docker compose build backend`).
- Orchestrator running `privileged: true` with `/var/lib/perpetuity/{vols,workspaces}` binds (workspaces with `propagation=rshared`).
- Host's `/var/lib/perpetuity/workspaces` already converted to a shared mountpoint by the `workspace-mount-init` sidecar (idempotent at compose up).
- `/dev/loop0..31` mknod'd by orchestrator boot (`_ensure_loop_device_nodes`).
- DEFAULT_VOLUME_SIZE_GB defaults to 4 unless overridden per orchestrator boot.

## Test 1 — Kernel-enforced 1 GiB hard cap (alice)

Demonstrates the slice headline assertion: ext4 hard-cap bites at the kernel level.

1. Stop the compose orchestrator: `docker compose rm -sf orchestrator`.
2. Launch an ephemeral orchestrator on `perpetuity_default` with `--privileged`, `--network-alias orchestrator`, the standard `/var/lib/perpetuity/{vols,workspaces}` binds (workspaces `:rshared`), and env `DEFAULT_VOLUME_SIZE_GB=1`.
   *Expected:* boot logs include `pg_pool_opened size=5`, `loop_devices_ready count=32`, container reports `(healthy)`.
3. POST `/api/v1/users/signup` with alice's RFC-2606 example.com email; capture the session cookie.
4. POST `/api/v1/sessions` with that cookie.
   *Expected:* 201 with `{session_id, container_id}`. Orchestrator INFO log `volume_provisioned volume_id=<uuid> user_id=<alice.id> team_id=<alice.personal_team> size_gb=1 img_path=/var/lib/perpetuity/vols/<uuid>.img`.
5. Open WS to `/api/v1/ws/terminal/<session_id>` with the alice cookie + an explicit `Cookie:` header (per MEM133). Observe an `attach` frame.
6. Send `{type:"input", bytes:"dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100 2>/tmp/dd.err\n"}`. Then send a uuid-keyed end-marker built via printf string substitution (per MEM150) — e.g. `printf 'EN%sOK_%s\n' D <uuid>` — and read `data` frames until the literal sentinel appears.
   *Expected:* dd exits non-zero and `/tmp/dd.err` contains `No space left on device`.
7. Send `{type:"input", bytes:"stat -c %s /workspaces/<team>/big\n"}` and read until the next sentinel.
   *Expected:* size is between 0.90 × 1024^3 and 1.05 × 1024^3 bytes (real cap, not 0; ext4 metadata overhead allowed).

## Test 2 — Per-volume DB row matches disk

Run after Test 1, while the ephemeral 1 GiB orchestrator is still up.

1. From the test backend's session: `SELECT id, size_gb, img_path FROM workspace_volume WHERE user_id=<alice.id> AND team_id=<alice.personal_team>`.
   *Expected:* exactly one row; `size_gb=1`; `img_path` matches `/var/lib/perpetuity/vols/<uuid>.img` shape.
2. From inside the orchestrator: `losetup -j <img_path>` and `cat /proc/mounts | grep /var/lib/perpetuity/workspaces/<alice.id>/<alice.personal_team>`.
   *Expected:* losetup shows the .img bound to a /dev/loopN; /proc/mounts shows the path mounted as ext4.

## Test 3 — Container resource limits (alice)

1. From the host (test runner has docker CLI): `docker inspect <alice.container_id>`.
   *Expected:* `HostConfig.Memory == 2147483648`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1000000000`.

## Test 4 — Neighbor isolation under a different cap (bob, 4 GiB)

Demonstrates the per-(user, team) isolation contract and the default 4 GiB cap.

1. Kill the ephemeral orchestrator. `docker compose up -d orchestrator`. Wait for `(healthy)` and DNS reachable from inside the sibling backend container.
2. Sign up bob (different example.com email, different personal team).
3. POST `/api/v1/sessions` with bob's cookie.
   *Expected:* 201; orchestrator INFO `volume_provisioned ... size_gb=4 img_path=/var/lib/perpetuity/vols/<different_uuid>.img`.
4. WS-attach as bob; send `{type:"input", bytes:"df -BG /workspaces/<bob.team>\n"}`.
   *Expected:* `df` reports total in `[3, 4]` GiB inclusive (ext4 metadata overhead) and Use% in single digits.
5. Send `{type:"input", bytes:"ls -la /workspaces/<bob.team>/\n"}`.
   *Expected:* output does NOT contain a `big` entry — alice's 1 GiB write is invisible to bob.
6. From the test backend's session: assert bob's `workspace_volume` row has `size_gb=4` and an `img_path` distinct from alice's.

## Test 5 — Alice's volume survives the orchestrator swap

1. After Test 4, from inside the (now compose) orchestrator: `stat /var/lib/perpetuity/vols/<alice.img_path>`.
   *Expected:* file exists, apparent size ≥ 1 GiB. (Confirms the compose-up restore did not touch alice's data; .img files survive orchestrator restarts because the `/var/lib/perpetuity/vols` bind is 1:1.)

## Test 6 — Idempotent re-provision (no data loss)

1. Inside alice's session, `echo sentinel > /workspaces/<team>/sentinel.txt`.
2. DELETE `/api/v1/sessions/<alice.session_id>`.
3. POST `/api/v1/sessions` for alice again (cookie unchanged).
   *Expected:* 201 with a new session_id but the SAME `workspace_volume` row id and the SAME .img inode (orchestrator INFO `volume_reused`).
4. WS-attach to the new session. `cat /workspaces/<team>/sentinel.txt`.
   *Expected:* output is `sentinel`. (Proves `mkfs_check=False` default — re-provision did not zero user data.)

## Test 7 — Log redaction sweep (PII regression)

1. DELETE both sessions: `DELETE /api/v1/sessions/<alice.session_id>` and `DELETE /api/v1/sessions/<bob.session_id>` — expect 200 each.
2. `docker compose logs orchestrator backend | grep -E '<alice.email>|<alice.full_name>|<bob.email>|<bob.full_name>'`.
   *Expected:* zero matches. Logs include only UUIDs in `actor_id`, `target_user_id`, `team_id`, `session_id`, `container_id`, `volume_id`, and uuid-keyed `img_path`.
3. `docker compose logs orchestrator | grep -E 'volume_provisioned|volume_mounted'`.
   *Expected:* both keys present (proves observability taxonomy is emitted).

## Edge cases

### E1 — Concurrent provision race on the same (user, team)

1. From two test sessions, fire `POST /api/v1/sessions` simultaneously for the same alice cookie.
   *Expected:* both return 201 with the SAME container_id (one wins the container-create race; the other reuses); exactly ONE `workspace_volume` row exists for (alice, alice.personal_team) — the unique constraint is the canonical tie-break. Orphan .img file from the loser may linger in `/var/lib/perpetuity/vols/` but is uuid-keyed and harmless.

### E2 — Postgres unreachable at provision time

1. `docker compose stop db`. POST `/api/v1/sessions` for a fresh user.
   *Expected:* 503 with `{detail: 'workspace_volume_store_unavailable'}` from the orchestrator surfaced through the backend.
2. `docker compose start db`. Retry POST `/api/v1/sessions`.
   *Expected:* 201 — orchestrator pool reconnects on next acquire; no orchestrator restart needed.

### E3 — mkfs.ext4 binary missing (regression guard for the e2fsprogs Dockerfile add)

1. Inside the orchestrator: `which mkfs.ext4`.
   *Expected:* `/usr/sbin/mkfs.ext4`. (If absent, T02's e2fsprogs apt install regressed; first provision would fail with `VolumeProvisionFailed(step='mkfs', reason='binary_not_found:mkfs.ext4')`.)

### E4 — Volume-mount failure surfaces with the failing step

1. Force `mount_image` to fail by passing a nonexistent .img path through the orchestrator's debug surface (or temporarily unbind `/var/lib/perpetuity/vols`).
   *Expected:* POST `/api/v1/sessions` returns 500 `{detail: 'volume_provision_failed', step: 'losetup', reason: '<truncated stderr first line, ≤200 chars>'}`. Reason MUST NOT contain neighbor volume paths or any user PII.

### E5 — More than 8 concurrent provisions (loop-device exhaustion guard)

1. Fire 16 concurrent POST `/api/v1/sessions` for 16 different (user, team) pairs.
   *Expected:* all 16 succeed. (Confirms `_ensure_loop_device_nodes(count=32)` is doing its job; without it linuxkit only ships /dev/loop0..7 and the 9th provision would `losetup` against a "lost" device.)
