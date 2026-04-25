"""Per-(user, team) workspace_volume Postgres store + provisioning helper (T03).

Owns:
  - the asyncpg connection pool (opened in main.py's lifespan)
  - the two SQL operations the orchestrator needs against `workspace_volume`:
    `get_volume(user_id, team_id)` and `create_volume(user_id, team_id, size_gb,
    img_path)`
  - the `ensure_volume_for(pool, user_id, team_id)` helper that composes the
    DB lookup with the host-side allocate/mount machinery from `volumes.py`,
    so `provision_container` only ever calls one function

Why a separate module: keeps `sessions.py` focused on container/tmux concerns
(D012/D018 boundary) and keeps the SQL string literals + asyncpg shape in one
place where future schema changes can be reviewed without scrolling through
container provisioning logic.

Failure shape:
  - asyncpg connection / query timeout → `WorkspaceVolumeStoreUnavailable` → 503
  - unique-violation race on (user_id, team_id) → catch + refetch the existing
    row (concurrent-provision tie-break — the unique constraint is the source
    of truth, the loser refetches the winner's row)
  - allocate_image / mount_image errors propagate as `VolumeProvisionFailed`
    (caller-handled in main.py) — this module does not catch them

Logging discipline (MEM134): UUIDs only. Never log user email / team slug /
host paths that aren't uuid-keyed. The .img path is uuid-keyed by construction
so it is safe to log directly.
"""

from __future__ import annotations

import logging
import uuid
from typing import NamedTuple

import asyncpg

from orchestrator.config import settings
from orchestrator.errors import WorkspaceVolumeStoreUnavailable
from orchestrator.volumes import allocate_image, mount_image

logger = logging.getLogger("orchestrator")


# 5-second per-query timeout matches the slice plan's failure-mode contract
# for "Postgres unreachable mid-provision → 503". Anything longer and the
# backend's own request budget would expire first.
_POOL_COMMAND_TIMEOUT_SECONDS = 5.0


class VolumeRecord(NamedTuple):
    """Subset of the workspace_volume row the orchestrator actually uses.

    Mirrors the schema from `backend/app/alembic/versions/s04_workspace_volume.py`.
    """

    id: str
    user_id: str
    team_id: str
    size_gb: int
    img_path: str


def _row_to_record(row: asyncpg.Record) -> VolumeRecord:
    """Adapt an asyncpg Record to the NamedTuple shape.

    UUIDs come back as `uuid.UUID` from asyncpg; we stringify them at the
    boundary so downstream code (logging, JSON responses, bind-mount paths)
    never has to think about the type.
    """
    return VolumeRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        team_id=str(row["team_id"]),
        size_gb=int(row["size_gb"]),
        img_path=str(row["img_path"]),
    )


async def open_pool(database_url: str | None = None) -> asyncpg.Pool:
    """Open the asyncpg pool the lifespan owns.

    Pool size 5 matches the slice plan's Load Profile section: 1 query per
    fresh provision, the orchestrator can sustain 5× concurrent fresh
    provisions before kernel loop exhaustion (T02 boundary) bites first.
    """
    url = database_url or settings.database_url
    try:
        pool = await asyncpg.create_pool(
            dsn=url,
            min_size=1,
            max_size=5,
            command_timeout=_POOL_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, asyncpg.PostgresError) as exc:
        # Boot-time failure: surface the exception class only — the dsn
        # itself contains a password, never log it.
        logger.error(
            "pg_pool_open_failed reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"pg_pool_open_failed:{type(exc).__name__}"
        ) from exc
    if pool is None:
        # Defensive — asyncpg returns None when the dsn is malformed in
        # ways the typed return doesn't cover. Treat as unavailable.
        raise WorkspaceVolumeStoreUnavailable("pg_pool_open_returned_none")
    return pool


async def close_pool(pool: asyncpg.Pool | None) -> None:
    """Close the pool on shutdown. Best-effort; never raises."""
    if pool is None:
        return
    try:
        await pool.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pg_pool_close_failed reason=%s", type(exc).__name__)


async def get_volume(
    pool: asyncpg.Pool, user_id: str, team_id: str
) -> VolumeRecord | None:
    """Look up the workspace_volume row for `(user_id, team_id)`.

    Returns None if no row exists yet (fresh provision path). Raises
    `WorkspaceVolumeStoreUnavailable` on connection / timeout / pg-error.
    """
    sql = (
        "SELECT id, user_id, team_id, size_gb, img_path "
        "FROM workspace_volume "
        "WHERE user_id = $1 AND team_id = $2"
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, uuid.UUID(user_id), uuid.UUID(team_id))
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=get_volume reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"get_volume_failed:{type(exc).__name__}"
        ) from exc
    if row is None:
        return None
    return _row_to_record(row)


async def create_volume(
    pool: asyncpg.Pool,
    user_id: str,
    team_id: str,
    size_gb: int,
    img_path: str,
) -> VolumeRecord:
    """Insert a fresh workspace_volume row.

    On unique-violation against `uq_workspace_volume_user_team` (concurrent
    provision race), refetch the existing row and return it — the unique
    constraint is the canonical tie-break, the loser inherits the winner's
    state.

    `id` is generated by us (uuid4) so the .img filename is decided before
    the INSERT — keeps the (DB row id, .img filename) coupling tight and
    means a rolled-back INSERT does NOT leave an orphan .img naming the
    failed-to-be-created row.
    """
    new_id = uuid.uuid4()
    sql = (
        "INSERT INTO workspace_volume (id, user_id, team_id, size_gb, img_path, created_at) "
        "VALUES ($1, $2, $3, $4, $5, NOW()) "
        "RETURNING id, user_id, team_id, size_gb, img_path"
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                new_id,
                uuid.UUID(user_id),
                uuid.UUID(team_id),
                size_gb,
                img_path,
            )
    except asyncpg.UniqueViolationError:
        # Concurrent-provision tie-break: someone else won the (user, team)
        # uniqueness race. Their row is canonical; refetch and return it.
        logger.info(
            "volume_create_race_detected user_id=%s team_id=%s",
            user_id,
            team_id,
        )
        existing = await get_volume(pool, user_id, team_id)
        if existing is None:
            # Pathological: unique-violation but no row visible. Either an
            # FK constraint also fired (different shape) or visibility
            # delay. Treat as unavailable rather than spin.
            raise WorkspaceVolumeStoreUnavailable(
                "create_volume_race_no_winner_found"
            )
        return existing
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=create_volume reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"create_volume_failed:{type(exc).__name__}"
        ) from exc
    if row is None:
        raise WorkspaceVolumeStoreUnavailable("create_volume_returning_none")
    return _row_to_record(row)


async def ensure_volume_for(
    pool: asyncpg.Pool,
    user_id: str,
    team_id: str,
    *,
    mountpoint: str,
    size_gb: int | None = None,
    vols_dir: str | None = None,
) -> VolumeRecord:
    """Find-or-create the workspace_volume row, allocate the .img if needed,
    and mount it at `mountpoint`. Returns the canonical VolumeRecord.

    Idempotent end-to-end: a re-call with the same `(user_id, team_id)`
    finds the existing row, calls allocate_image (which is a no-op on an
    existing non-zero .img), and calls mount_image (which short-circuits
    on an already-mounted path). Net effect: a re-provision is a DB hit
    plus `os.path.ismount` plus `losetup -j` — no mkfs, no second loop
    device consumed, no risk of zeroing the user's data.

    Args:
      pool: open asyncpg pool (lifespan-owned)
      user_id, team_id: canonical UUIDs
      mountpoint: where to mount the volume (e.g.
        `/var/lib/perpetuity/workspaces/<user>/<team>`)
      size_gb: override for new-row creation; defaults to
        `settings.default_volume_size_gb` (4 GB until S03 wires
        system_settings)
      vols_dir: override for the .img directory; defaults to
        `settings.vols_dir` (`/var/lib/perpetuity/vols`)
    """
    target_vols_dir = vols_dir or settings.vols_dir
    existing = await get_volume(pool, user_id, team_id)
    if existing is not None:
        # Reuse path: ensure the .img and mount are still live (allocate +
        # mount are both idempotent on existing state — they short-circuit
        # without redoing destructive work).
        await allocate_image(
            existing.id,
            existing.size_gb,
            vols_dir=target_vols_dir,
            mkfs_check=False,
        )
        await mount_image(existing.img_path, mountpoint)
        logger.info(
            "volume_reused volume_id=%s user_id=%s team_id=%s size_gb=%d",
            existing.id,
            user_id,
            team_id,
            existing.size_gb,
        )
        return existing

    # Fresh-row path: allocate first, THEN insert. Two reasons:
    #   (1) If the DB insert fails, the .img is reusable on the next retry
    #       (allocate_image is idempotent) so we don't leak.
    #   (2) The .img filename is derived from a uuid we mint locally; if
    #       another concurrent provisioner inserts first and we lose the
    #       unique-violation race, our .img file lingers but is harmless
    #       (uuid-keyed, will be reaped manually if it ever matters).
    target_size_gb = size_gb if size_gb is not None else settings.default_volume_size_gb
    if target_size_gb <= 0:
        # Defensive — slice plan says 1..256 is enforced at the app layer
        # (S03 admin API). Until then, refuse <= 0 here so allocate_image
        # doesn't trip its own ValueError mid-provision.
        raise ValueError(f"size_gb must be >= 1, got {target_size_gb}")

    # Mint the volume id ourselves so allocate_image and create_volume
    # agree on the .img filename. create_volume re-mints internally if
    # we didn't pass it; we pass the value we used for the file path so
    # the (row id, filename) coupling holds.
    volume_id = str(uuid.uuid4())
    img_path = await allocate_image(
        volume_id,
        target_size_gb,
        vols_dir=target_vols_dir,
    )

    # INSERT with our pre-minted id by overriding create_volume's mint —
    # use the inline SQL so the .img we just created lines up with the
    # row's id field. (create_volume's public surface mints internally
    # for callers who don't care; here we care.)
    sql = (
        "INSERT INTO workspace_volume (id, user_id, team_id, size_gb, img_path, created_at) "
        "VALUES ($1, $2, $3, $4, $5, NOW()) "
        "RETURNING id, user_id, team_id, size_gb, img_path"
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                uuid.UUID(volume_id),
                uuid.UUID(user_id),
                uuid.UUID(team_id),
                target_size_gb,
                img_path,
            )
    except asyncpg.UniqueViolationError:
        # Concurrent fresh-provision: someone else inserted first. Their
        # row wins; refetch and use it. Our .img file lingers (uuid-keyed,
        # safe by construction) — not worth the cleanup race.
        logger.info(
            "volume_create_race_detected user_id=%s team_id=%s",
            user_id,
            team_id,
        )
        existing = await get_volume(pool, user_id, team_id)
        if existing is None:
            raise WorkspaceVolumeStoreUnavailable(
                "create_volume_race_no_winner_found"
            )
        # Mount the winner's volume, not ours.
        await mount_image(existing.img_path, mountpoint)
        return existing
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=create_volume reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"create_volume_failed:{type(exc).__name__}"
        ) from exc

    if row is None:
        raise WorkspaceVolumeStoreUnavailable("create_volume_returning_none")
    record = _row_to_record(row)

    # Mount the freshly-allocated img at the workspace mountpoint.
    await mount_image(record.img_path, mountpoint)

    logger.info(
        "volume_provisioned volume_id=%s user_id=%s team_id=%s size_gb=%d img_path=%s",
        record.id,
        user_id,
        team_id,
        record.size_gb,
        record.img_path,
    )
    return record


# Module-level pool reference — main.py's lifespan sets/clears it. Routes
# read it via `get_pool()` so test code can substitute a fake without
# touching FastAPI app state.
_pool: asyncpg.Pool | None = None


def set_pool(pool: asyncpg.Pool | None) -> None:
    """Set the module-level pool reference. Called from lifespan startup
    and shutdown. Tests may also call this with a stub pool.
    """
    global _pool
    _pool = pool


def get_pool() -> asyncpg.Pool:
    """Return the module-level pool reference, raising if unset.

    Mirrors the redis_client.get_registry shape so route handlers can
    fail fast if the lifespan never opened the pool (e.g. unit tests
    that import the app without running the lifespan).
    """
    if _pool is None:
        raise WorkspaceVolumeStoreUnavailable("pg_pool_unset")
    return _pool


__all__ = [
    "VolumeRecord",
    "open_pool",
    "close_pool",
    "get_volume",
    "create_volume",
    "ensure_volume_for",
    "set_pool",
    "get_pool",
]
