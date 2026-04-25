"""Per-(user, team) container provisioning + tmux session lifecycle (T03).

This is the heart of M002. The model:
  - One Docker container per (user_id, team_id), discovered by Docker labels
    `user_id=<uuid>` and `team_id=<uuid>` and named `perpetuity-ws-<first8-team>`.
  - Many tmux sessions per container — each WS session_id maps to a named tmux
    session inside that container (R008).
  - tmux owns the pty (D012), so the orchestrator can come and go via
    `docker exec` without killing the user's shell. Restart-survival is the
    whole point of putting tmux between docker exec and the shell.

T03 uses a plain bind-mount under `/var/lib/perpetuity/workspaces/<user>/<team>/`.
S02 swaps that for a loopback-ext4 volume mounted at the same path — the path
shape is reserved here so the swap doesn't ripple through the API surface.

Scrollback hard-cap is enforced **here**, not at the tmux side (D017). tmux's
own history-limit is configurable per-session and a buggy or hostile shell
could blow past it; the orchestrator-side cap is the contract.

Failure modes:
  - Docker daemon unreachable at any step → DockerUnavailable → 503.
  - Volume mkdir fails (permission, ENOSPC) → VolumeMountFailed → 500.
  - tmux exec returns non-zero → propagated as a generic OrchestratorError,
    logged with the failing command and the stdout/stderr captured from exec.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import aiodocker
import asyncpg
from aiodocker.exceptions import DockerError

from orchestrator.config import settings
from orchestrator.errors import (
    DockerUnavailable,
    OrchestratorError,
)
from orchestrator.volume_store import ensure_volume_for

logger = logging.getLogger("orchestrator")


class VolumeMountFailed(OrchestratorError):
    """Bind-mount source dir could not be created on the host.

    Mapped to 500 in T03 — placeholder for the richer set of S02 errors when
    loopback-ext4 volumes land. Logged as `volume_mount_failed`.
    """


class TmuxCommandFailed(OrchestratorError):
    """A `docker exec tmux ...` returned non-zero.

    The exec output is captured into `output` so the caller can include it in
    the log line — tmux usually writes a one-liner like
    `duplicate session: <name>` or `can't find session: <name>` to stderr.
    """

    def __init__(self, message: str, *, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def _container_name(team_id: str) -> str:
    """Container name policy: `perpetuity-ws-<first8-team>` (MEM098).

    Docker name validation: 2-255 chars matching `[a-zA-Z0-9][a-zA-Z0-9_.-]+`.
    Team UUIDs are hex with dashes; first 8 chars are pure hex so we're safe.
    """
    # Strip dashes defensively even though [0:8] of a hex uuid is dash-free —
    # makes the function tolerant of non-canonical UUID strings if a caller
    # ever passes one in.
    clean = team_id.replace("-", "")
    return f"perpetuity-ws-{clean[:8]}"


def _workspace_host_path(user_id: str, team_id: str) -> str:
    """Path on the orchestrator host (and inside the orchestrator container,
    since they're bind-mounted to the same path) that holds the user's files.

    `settings.workspace_root` defaults to `/var/lib/perpetuity/workspaces` —
    in compose we mount that path 1:1 from host to orchestrator so the
    orchestrator can `mkdir -p` it before passing it to the workspace
    container as a bind-mount source.
    """
    return os.path.join(settings.workspace_root, user_id, team_id)


def _workspace_container_path(team_id: str) -> str:
    """Path inside the workspace container.

    Per D004 the workspace files live at `/workspaces/<user_id>/<team_id>/`
    inside the container. T03 only knows the team_id at this level — the
    user_id segment is included in the bind-mount source path; both segments
    are in the destination too so concurrent containers (one per user-team)
    don't collide if the layout ever flattens.
    """
    return f"/workspaces/{team_id}"


def _build_container_config(
    user_id: str,
    team_id: str,
    host_workspace_dir: str,
    container_workspace_dir: str,
) -> dict[str, Any]:
    """Compose the JSON config for `containers/create`.

    Matches the planned constraints:
      - mem_limit, pids_limit, nano_cpus from settings (1.0 vCPU, 2g, 512 pids)
      - command `sleep infinity` so tmux sessions live inside via docker exec
      - labels user_id/team_id for discovery (`perpetuity.managed=true` is the
        umbrella label so the future reaper can list all M002 containers
        without cross-contaminating with non-M002 containers on the host)
      - bind-mount the host workspace dir into the container

    Memory limit must be in bytes for the Docker API; settings.container_mem_limit
    is "2g" — convert here.
    """
    return {
        "Image": settings.workspace_image,
        "Cmd": ["sleep", "infinity"],
        "Labels": {
            "user_id": user_id,
            "team_id": team_id,
            "perpetuity.managed": "true",
        },
        "HostConfig": {
            "Memory": _parse_mem_limit(settings.container_mem_limit),
            "PidsLimit": settings.container_pids_limit,
            "NanoCpus": settings.container_nano_cpus,
            "Binds": [f"{host_workspace_dir}:{container_workspace_dir}"],
            # Restart policy: no — the idle reaper (S04) controls container
            # lifecycle. A respawning container would ressurect after the
            # reaper kills it, defeating the quota model.
            "RestartPolicy": {"Name": "no"},
        },
        # Open a stdin/tty pair on the container itself so future S02
        # operations (in-container losetup helpers) can spawn child execs
        # without renegotiating tty allocation per-call. Workspace shells
        # are spawned via `docker exec`, not `attach`, so this is harmless
        # for the tmux model.
        "Tty": False,
        "OpenStdin": False,
    }


def _parse_mem_limit(value: str) -> int:
    """Convert "2g"/"512m"/"123" into bytes for HostConfig.Memory.

    Docker expects bytes. We accept the friendly suffixes used in compose
    config so settings.container_mem_limit can stay readable.
    """
    s = value.strip().lower()
    if not s:
        raise ValueError("empty memory limit")
    multiplier = 1
    if s.endswith("k"):
        multiplier, s = 1024, s[:-1]
    elif s.endswith("m"):
        multiplier, s = 1024 * 1024, s[:-1]
    elif s.endswith("g"):
        multiplier, s = 1024 * 1024 * 1024, s[:-1]
    return int(s) * multiplier


async def _find_container_by_labels(
    docker: aiodocker.Docker, user_id: str, team_id: str
) -> str | None:
    """Return the id of the existing (user, team) container, or None.

    Filters by both `user_id=` and `team_id=` labels — using only one would
    return the wrong container if a user is on multiple teams. Includes
    stopped containers (`all=1`) so the reaper's stopped-but-not-removed
    state is reachable too — a future task may want to start it back up;
    for T03 we still treat "found a stopped container" as "create a fresh
    one" since the planner explicitly said T03 doesn't manage stop/start
    transitions, but list_containers needs the full picture either way.
    """
    filters = json.dumps(
        {
            "label": [
                f"user_id={user_id}",
                f"team_id={team_id}",
                "perpetuity.managed=true",
            ]
        }
    )
    try:
        results = await docker.containers.list(all=True, filters=filters)
    except DockerError as exc:
        raise DockerUnavailable(f"docker_list_failed:{exc.status}:{exc.message}") from exc
    except OSError as exc:
        raise DockerUnavailable(f"docker_unreachable:{type(exc).__name__}") from exc
    for container in results:
        # `list()` returns DockerContainer instances whose _container dict
        # holds the full inspect-style record. Skip non-running containers
        # for T03 — if a reaped-but-not-removed container is sitting around
        # we'd rather create a fresh one than try to start a possibly-broken
        # one. State is in _container["State"] for list responses.
        state = container._container.get("State")
        if isinstance(state, str):
            running = state == "running"
        elif isinstance(state, dict):
            running = state.get("Running", False)
        else:
            running = False
        if running:
            return container.id
    return None


async def _ensure_workspace_dir(host_path: str) -> None:
    """`mkdir -p` the workspace dir before bind-mounting it.

    Runs on the orchestrator host (the orchestrator container has the host
    path bind-mounted in at the same location, so this resolves to the same
    inode as the eventual workspace container's bind-mount source). The S02
    loopback-volume manager will replace this with a `losetup`+`mount` flow.
    """
    try:
        os.makedirs(host_path, mode=0o700, exist_ok=True)
    except OSError as exc:
        logger.error(
            "volume_mount_failed path=%s reason=%s",
            host_path,
            type(exc).__name__,
        )
        raise VolumeMountFailed(f"mkdir_failed:{type(exc).__name__}:{host_path}") from exc


async def provision_container(
    docker: aiodocker.Docker,
    user_id: str,
    team_id: str,
    *,
    pg: asyncpg.Pool,
) -> tuple[str, bool]:
    """Look up or create the (user, team) workspace container.

    Returns `(container_id, created)`. `created=True` only when a fresh
    container was just created — used by the route to populate the
    `created` field in the POST /v1/sessions response.

    Volume backing: before creating the container we ensure a
    workspace_volume row + .img + ext4 mount exist for `(user_id, team_id)`
    via `ensure_volume_for`. The bind-mount source is the loopback-ext4
    mountpoint (same path the T03 plain-dir flow used) so the container's
    in-container path `/workspaces/<team_id>/` is unchanged from S01 —
    the kernel-enforced size cap is the only behavior change.

    Container reuse: if a running container with matching labels already
    exists, return its id without disturbing it. We still call
    `ensure_volume_for` on the reuse path so a re-provision after a host
    reboot re-mounts the .img (mount_image is idempotent on an already-
    mounted path; the cost is one losetup -j read on the warm path).

    Concurrent provisioning requests from the same (user, team) can race
    on container create; in the worst case two simultaneous calls might
    both miss and both attempt create — the second would 409-conflict on
    the deterministic name. We treat that 409 as "someone else won" and
    refetch. Anything else we surface as DockerUnavailable. The volume
    layer has its own concurrent-create handling (uniqueness on
    workspace_volume).
    """
    host_workspace = _workspace_host_path(user_id, team_id)
    container_workspace = _workspace_container_path(team_id)

    # Ensure the volume row + .img + mount exist BEFORE we touch Docker.
    # If this fails, we never create a container — keeps the half-state
    # surface area to "DB row exists, .img exists, mount may or may not"
    # which is recoverable on retry (everything is idempotent).
    await ensure_volume_for(
        pg, user_id, team_id, mountpoint=host_workspace
    )

    existing = await _find_container_by_labels(docker, user_id, team_id)
    if existing is not None:
        logger.info(
            "container_reused container_id=%s user_id=%s team_id=%s",
            existing[:12],
            user_id,
            team_id,
        )
        return existing, False

    config = _build_container_config(
        user_id, team_id, host_workspace, container_workspace
    )
    name = _container_name(team_id)
    try:
        container = await docker.containers.create_or_replace(name=name, config=config)
        # create_or_replace doesn't auto-start. Workspace containers must be
        # running for `docker exec` to attach tmux sessions.
        await container.start()
    except DockerError as exc:
        # 409 = name conflict caused by a concurrent provision; fall through
        # to a fresh lookup.
        if exc.status == 409:
            existing = await _find_container_by_labels(docker, user_id, team_id)
            if existing is not None:
                return existing, False
        logger.error(
            "container_provision_failed user_id=%s team_id=%s reason=%s",
            user_id,
            team_id,
            f"{exc.status}:{exc.message}",
        )
        raise DockerUnavailable(
            f"container_create_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(f"docker_unreachable:{type(exc).__name__}") from exc

    logger.info(
        "container_provisioned container_id=%s user_id=%s team_id=%s",
        container.id[:12],
        user_id,
        team_id,
    )
    return container.id, True


async def _exec_collect(
    docker: aiodocker.Docker,
    container_id: str,
    cmd: list[str],
    *,
    tty: bool = False,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Run a command inside the container; return (stdout-as-text, exit_code).

    Uses `start(detach=False)` so the exec stream is consumed; we read until
    EOF and join the stdout/stderr frames into a text blob. `max_bytes`
    short-circuits the read — once that many bytes have been seen we stop
    pulling, close the stream, and let the kernel/tmux sort out the truncated
    rest. This is the scrollback hard-cap mechanism (D017): we **never** trust
    tmux's own limit.

    Exit code is read via `inspect()` after the stream EOF.
    """
    try:
        container = await docker.containers.get(container_id)
        exec_inst = await container.exec(cmd=cmd, tty=tty, stdout=True, stderr=True)
        out_bytes = bytearray()
        async with exec_inst.start(detach=False) as stream:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                out_bytes.extend(msg.data)
                if max_bytes is not None and len(out_bytes) >= max_bytes:
                    # Hit the cap. Stop reading; the stream context manager
                    # closes the underlying socket on __aexit__.
                    out_bytes = out_bytes[:max_bytes]
                    break
        info = await exec_inst.inspect()
        exit_code = int(info.get("ExitCode") or 0)
        return out_bytes.decode("utf-8", errors="replace"), exit_code
    except DockerError as exc:
        raise DockerUnavailable(
            f"docker_exec_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(f"docker_unreachable:{type(exc).__name__}") from exc


async def start_tmux_session(
    docker: aiodocker.Docker, container_id: str, session_id: str
) -> None:
    """Create a fresh detached tmux session named `session_id` running bash.

    `-d` (detached) is critical: docker exec returns immediately, tmux owns
    the pty (D012). `-x 200 -y 50` sets initial dimensions; the WS bridge
    will resize on first attach.

    If a tmux session with the same name already exists, tmux exits non-zero
    with `duplicate session`. We treat that as success — the planner allows
    re-using existing tmux sessions across reconnects. Any other non-zero
    exit raises TmuxCommandFailed.
    """
    cmd = ["tmux", "new-session", "-d", "-s", session_id, "-x", "200", "-y", "50", "bash"]
    out, code = await _exec_collect(docker, container_id, cmd)
    if code != 0:
        if "duplicate session" in out:
            logger.info(
                "tmux_session_reused session_id=%s container_id=%s",
                session_id,
                container_id[:12],
            )
            return
        logger.error(
            "tmux_new_session_failed session_id=%s container_id=%s out=%s",
            session_id,
            container_id[:12],
            out.strip(),
        )
        raise TmuxCommandFailed("tmux_new_session_failed", output=out)
    logger.info(
        "session_created session_id=%s container_id=%s",
        session_id,
        container_id[:12],
    )


async def list_tmux_sessions(
    docker: aiodocker.Docker, container_id: str
) -> list[str]:
    """Return tmux session names inside the container, or [] if none.

    `tmux ls` exits 1 with `no server running` when no sessions exist; we
    treat that as the empty list rather than an error.
    """
    cmd = ["tmux", "ls", "-F", "#{session_name}"]
    out, code = await _exec_collect(docker, container_id, cmd)
    if code != 0:
        if "no server running" in out or "error connecting" in out:
            return []
        # Other non-zero — surface so the caller can decide.
        raise TmuxCommandFailed("tmux_ls_failed", output=out)
    return [line for line in out.splitlines() if line.strip()]


async def capture_scrollback(
    docker: aiodocker.Docker, container_id: str, session_id: str
) -> str:
    """Capture the current pane's scrollback as text; hard-capped to
    `settings.scrollback_max_bytes` (default 100 KB) per D017.

    `tmux capture-pane -t <s> -p -S - -E -` prints the entire scrollback
    buffer (start to end) to stdout. We tell `_exec_collect` the byte cap;
    if tmux blasts more bytes than that we silently drop the suffix and
    return the prefix. Honest truncation: the prefix is contiguous from
    the start of the buffer, so the user gets the oldest scrollback —
    matches the "scroll back to find context" intent.

    Returns "" if the tmux session is missing — surfaces the orphaned-state
    case for the WARNING `tmux_session_orphaned` log line emitted at the
    HTTP route layer.
    """
    cmd = [
        "tmux",
        "capture-pane",
        "-t",
        session_id,
        "-p",
        "-S",
        "-",
        "-E",
        "-",
    ]
    out, code = await _exec_collect(
        docker, container_id, cmd, max_bytes=settings.scrollback_max_bytes
    )
    if code != 0:
        if "can't find session" in out or "no server running" in out:
            return ""
        raise TmuxCommandFailed("tmux_capture_pane_failed", output=out)
    return out


async def kill_tmux_session(
    docker: aiodocker.Docker, container_id: str, session_id: str
) -> bool:
    """Kill a single named tmux session inside the container.

    Returns True if a session was killed, False if it didn't exist (the
    tmux client distinguishes `can't find session` from other failures).
    Container is **not** stopped — sibling tmux sessions stay alive (R008).
    """
    cmd = ["tmux", "kill-session", "-t", session_id]
    out, code = await _exec_collect(docker, container_id, cmd)
    if code == 0:
        logger.info(
            "session_killed session_id=%s container_id=%s",
            session_id,
            container_id[:12],
        )
        return True
    if "can't find session" in out or "no server running" in out:
        return False
    raise TmuxCommandFailed("tmux_kill_session_failed", output=out)


async def resize_tmux_session(
    docker: aiodocker.Docker,
    container_id: str,
    session_id: str,
    cols: int,
    rows: int,
) -> None:
    """Resize the tmux client attached to `session_id`.

    Default tmux semantics: when multiple clients are attached, the smaller
    of the requested dimensions wins (D017). `refresh-client -C cols,rows`
    is the right knob for cooperative resize across multiple WS attaches.

    Calling resize on a non-existent session yields `can't find session`,
    which we surface to the caller so the HTTP route can return 404.
    """
    cmd = [
        "tmux",
        "refresh-client",
        "-t",
        session_id,
        "-C",
        f"{cols},{rows}",
    ]
    out, code = await _exec_collect(docker, container_id, cmd)
    if code != 0:
        if "can't find session" in out or "no server running" in out:
            raise TmuxCommandFailed("tmux_session_not_found", output=out)
        logger.warning(
            "tmux_refresh_failed session_id=%s container_id=%s out=%s",
            session_id,
            container_id[:12],
            out.strip(),
        )
        raise TmuxCommandFailed("tmux_refresh_failed", output=out)
