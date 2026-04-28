---
estimated_steps: 10
estimated_files: 7
skills_used: []
---

# T03: Wire volume manager into provision_container + add asyncpg lookup of workspace_volume

Connect T01's persistence and T02's host-side mount machinery into the existing `provision_container` flow so a fresh (user, team) provision: (1) finds-or-creates a `workspace_volume` Postgres row, (2) ensures the .img file is allocated and mounted at `<workspace_root>/<user_id>/<team_id>/`, (3) bind-mounts that mountpoint into the workspace container at `/workspaces/<team_id>/` exactly as before — so the locked S01 in-container path stays unchanged.

Changes:
  1. `orchestrator/orchestrator/volume_store.py` — new tiny module owning the asyncpg connection pool and the two SQL operations `get_volume(user_id, team_id) -> dict | None` and `create_volume(user_id, team_id, size_gb, img_path) -> dict`. Pool opened at lifespan startup, closed at shutdown. Raises `WorkspaceVolumeStoreUnavailable` (subclass of OrchestratorError) on connection error → 503.
  2. `orchestrator/orchestrator/config.py` — add `database_url: str` (read from env `DATABASE_URL`, default to the compose-internal `postgresql://postgres:<pwd>@db:5432/app` shape; tests pass an override) and `default_volume_size_gb: int = 4` (S03 will replace this with a system_settings lookup; D015 says per-row size_gb is the source of truth, so this default ONLY governs new-row creation).
  3. `orchestrator/orchestrator/sessions.py::provision_container` — replace the call to `_ensure_workspace_dir(host_workspace)` with a call into a new helper `ensure_volume_for(pg, user_id, team_id) -> VolumeRecord` that lives in `volume_store.py` (the helper composes get_volume → create_volume + volumes.allocate_image + volumes.mount_image). Bind-mount source becomes the mountpoint `<workspace_root>/<user>/<team>` (same path as before, but now backed by ext4 inside a loopback file). Container destination stays `/workspaces/<team_id>/`. The container_id flow is unchanged — only the bind-mount source backing differs.
  4. `orchestrator/orchestrator/main.py` — open `app.state.pg` (asyncpg pool) at lifespan; close on shutdown. Register a new exception handler for `VolumeProvisionFailed` → 500 `{detail:'volume_provision_failed', step, reason}` (replaces the T03-placeholder VolumeMountFailed handler shape; keep VolumeMountFailed handler too for backward compat — the loopback path can still fail at the os.makedirs step inside `volumes.allocate_image`).
  5. `docker-compose.yml` — add `/var/lib/perpetuity/vols:/var/lib/perpetuity/vols` bind to the orchestrator service so .img files survive orchestrator restarts (the workspace bind for mountpoints already exists from S01). No backend or compose-network changes.
  6. `orchestrator/tests/integration/test_sessions_lifecycle.py` — extend (do not rewrite) the existing T03 tests: the existing `test_provision_creates_container` now must also assert (a) a workspace_volume row exists for (user_id, team_id), (b) `losetup -a` inside orchestrator shows a loop attached to that row's img_path, (c) `mount | grep <mountpoint>` shows ext4. Container resource-limit re-verification: extend the existing provision test to inspect the spawned container with `docker inspect` and assert `HostConfig.Memory == 2 * 1024**3`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1_000_000_000`.
  7. ENOSPC integration check: `test_volume_hard_cap_enospc` — provision a session with `default_volume_size_gb` overridden to 1, exec a `dd if=/dev/zero of=/workspaces/<t>/big bs=1M count=1100` inside the workspace container, assert the dd command exits non-zero with `No space left on device` in stderr AND that exactly ~1 GB was written (use `stat -c %s /workspaces/<t>/big` and assert ~1 GB ± 5%).

Idempotency: a re-provision with the same (user_id, team_id) MUST find the existing workspace_volume row, MUST find the .img already mounted, MUST NOT mkfs.ext4 again (would zero the user's data — guarded by allocate_image's `mkfs_check=False` default). Test: `test_provision_idempotent_volume` calls provision twice and asserts the row's id is unchanged AND the .img inode is unchanged AND a sentinel file written between provisions still exists.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (asyncpg pool) | Raise `WorkspaceVolumeStoreUnavailable` → main.py handler returns 503 `{detail:'workspace_volume_store_unavailable'}` | asyncpg `command_timeout=5s` per query → same 503 | unique-violation on (user_id, team_id) → catch, refetch the existing row, return it (concurrent-provision race) |
| `volumes.allocate_image` | `VolumeProvisionFailed(step=truncate|mkfs)` propagates to main.py → 500 with step/reason | timeout from T02's 30 s subprocess timeout → propagates as VolumeProvisionFailed | N/A |
| `volumes.mount_image` | `VolumeProvisionFailed(step=losetup|mount)` propagates → 500 | timeout → VolumeProvisionFailed | N/A |
| Docker daemon (existing) | DockerUnavailable → 503 (S01-existing handler) | aiodocker default → DockerUnavailable | N/A (S01 contract held) |

Partial-failure recovery: if the workspace_volume row was created but allocate_image fails, the next retry's get_volume returns the row, allocate_image is idempotent on existing img file, mount_image is idempotent on existing mount — so a retried POST /v1/sessions converges. If allocate_image succeeded but mount_image failed, the .img file lingers on disk; mount_image retry completes the provision. No cleanup-on-failure for partial volumes — they're reusable on the next attempt.

## Load Profile

- **Shared resources**: asyncpg pool (size 5), kernel loop devices (T02 budget), docker.sock (single-fd serialized).
- **Per-operation cost**: 1 SELECT + (on miss) 1 INSERT against workspace_volume; 4-5 subprocess calls inside volumes.* on a fresh provision; 1-2 docker API calls (provision_container's existing list/create/start). Total ~600-800 ms cold provision, ~50-80 ms warm reprovision (DB hit only).
- **10x breakpoint**: kernel loop-device exhaustion (T02 boundary) hits before the asyncpg pool does. Pool size 5 vs 1 query per provision → pool can sustain 5× concurrent fresh provisions; warm path just reads, so steady-state is well above 10× the dev workload.

## Negative Tests

- **Malformed inputs**: caller passing a non-UUID `user_id` is blocked by the existing pydantic UUID validation on the route layer; verify the new code does not bypass that path. Backend trying to provision with a (user_id, team_id) where user_id ∉ team_member: orchestrator does not enforce membership (S01 boundary — backend's job); a malicious or buggy backend could create a volume for the wrong team. This is documented as out-of-scope for orchestrator (D016) and re-checked at the backend layer in T04's e2e.
- **Error paths**: Postgres unreachable mid-provision → 503 with structured reason; orchestrator restart while provision is mid-flight → no half-mounted state because mount_image is idempotent on the next retry; mkfs.ext4 fails (e.g., simulated by injecting bad mkfs path) → 500 with step='mkfs' and the actual stderr in the reason field.
- **Boundary conditions**: re-provision with same (user_id, team_id) returns existing row + existing mount (idempotent — `test_provision_idempotent_volume`). Two concurrent fresh provisions for the same (user_id, team_id) — one wins the unique-constraint race, the other refetches the winner's row (not a hard requirement of the slice but the unique constraint guarantees the invariant; covered by inspection of the unique-violation catch path in T03's unit test).

## Inputs

- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/config.py``
- ``orchestrator/orchestrator/errors.py``
- ``orchestrator/orchestrator/volumes.py``
- ``backend/app/alembic/versions/s04_workspace_volume.py``
- ``backend/app/models.py``
- ``docker-compose.yml``
- ``orchestrator/tests/integration/test_sessions_lifecycle.py``
- ``orchestrator/tests/integration/conftest.py``

## Expected Output

- ``orchestrator/orchestrator/volume_store.py``
- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/config.py``
- ``orchestrator/orchestrator/errors.py``
- ``docker-compose.yml``
- ``orchestrator/tests/integration/test_sessions_lifecycle.py``
- ``orchestrator/pyproject.toml``

## Verification

docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_sessions_lifecycle.py tests/integration/test_volumes.py -v

## Observability Impact

New INFO `volume_provisioned volume_id=<uuid> user_id=<uuid> team_id=<uuid> size_gb=N img_path=<path>` emitted from `ensure_volume_for` on the create-row path. INFO `volume_reused volume_id=<uuid>` on the get_volume hit path. WARNING `pg_unreachable op=get_volume reason=<exc-class>`. ERROR `volume_provision_failed step=... volume_id=<uuid>` (mapped to 500 from main.py exception handler). Container resource limits visible to a future agent via `docker inspect <container_id>` — no orchestrator log line needed (Docker is the source of truth) but the integration test asserts the values to lock the contract.
