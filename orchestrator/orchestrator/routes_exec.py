"""One-shot exec route for the M005/S02 AI executor.

The Celery `run_workflow` task in the backend (T03) delegates each ``ai``
step (action=claude / codex / shell) to this endpoint. The orchestrator is
the only process with a Docker socket, so the task itself never speaks
docker — it speaks HTTP to here.

Endpoint:
  POST /v1/sessions/{session_id}/exec
    {
      "user_id":         <uuid>,
      "team_id":         <uuid>,
      "cmd":             ["claude", "-p", "$PROMPT", ...],
      "env":             {"ANTHROPIC_API_KEY": "...", "PROMPT": "..."},
      "timeout_seconds": 600,    # cap at 600
      "cwd":             "/workspaces/<team_id>" | null
    }
  →
    { "stdout": "...", "exit_code": 0, "duration_ms": 1234 }

The endpoint:
  1. Provisions / finds the per-(user, team) workspace container via the
     existing idempotent ``provision_container`` helper. ``session_id`` here
     is purely a correlation handle for logs — there is no tmux session
     involved. The endpoint deliberately does NOT persist the session in
     Redis; the workflow run id is the durable handle (recorded on
     ``workflow_runs`` / ``step_runs`` by the backend).
  2. Wraps ``cmd`` as ``["script", "-q", "-c", "<shell-quoted cmd>", "/dev/null"]``
     so the AI CLIs see a real pty (MEM007 / D012). util-linux ``script(1)``
     -c invokes the command through ``/bin/sh`` already, so we shell-quote
     the original argv and join it back with spaces; occurrences of ``$VAR``
     reference the env dict so secrets only ever live in the env frame,
     never in the cmd list (MEM274).
  3. Calls a local ``_exec_collect_with_env`` helper modeled after
     ``sessions._exec_collect`` and ``clone._exec_with_env`` — opens the
     exec stream with ``tty=True`` (script -q produces merged stdout/stderr
     anyway and we record the merged stream as ``step_runs.stdout``),
     drains until EOF or ``timeout_seconds`` (capped), inspects ``ExitCode``.
  4. Returns the merged stdout, the exit code, and the wall-clock duration.

Logs (per the slice's observability taxonomy):
  - INFO ``oneshot_exec_started session_id=<uuid> user_id=<uuid> team_id=<uuid> action=<claude|codex|shell>``
  - INFO ``oneshot_exec_completed session_id=<uuid> exit=<n> duration_ms=<n>``

Logs MUST NOT carry the cmd argv list, env values, or stdout. The stdout
is persisted by the backend in ``step_runs.stdout`` (R018 history) but never
flows through the orchestrator log stream.

Failure modes:
  - Docker unreachable        → 503 docker_unavailable (existing handler)
  - Volume provision failed   → 500 volume_provision_failed (existing handler)
  - Workspace store gone      → 503 workspace_volume_store_unavailable
  - Exec timeout              → 504 oneshot_exec_timeout — distinguished from
    a CLI-nonzero exit so the executor can map it to a structural error_class
    rather than a "your CLI failed" surface
  - Malformed body / oversize → 422 (pydantic) / 413 (cmd or env too large)
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
import uuid
from typing import Any

import aiodocker
from aiodocker.exceptions import DockerError
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from orchestrator.errors import DockerUnavailable
from orchestrator.sessions import provision_container
from orchestrator.volume_store import get_pool

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/sessions", tags=["oneshot-exec"])


# Hard caps on the request shape. The backend executor never sends payloads
# anywhere near these — they exist so a misbehaving caller can't pin the
# orchestrator's heap or hold the exec stream open forever.
_MAX_CMD_ENTRIES = 64
_MAX_CMD_ENTRY_LEN = 8 * 1024
_MAX_ENV_ENTRIES = 64
_MAX_ENV_KEY_LEN = 128
_MAX_ENV_VALUE_LEN = 64 * 1024
_MAX_TIMEOUT_SECONDS = 600
_MAX_STDOUT_BYTES = 5 * 1024 * 1024  # 5 MiB; M005 trusts step_runs.stdout for history


class OneShotExecBody(BaseModel):
    """POST /v1/sessions/{session_id}/exec body.

    pydantic enforces UUID shape on user/team and bounds on the variable-
    sized fields; oversized requests get a 422 (or 413 if we surface the
    bound from the validator).
    """

    user_id: uuid.UUID
    team_id: uuid.UUID
    cmd: list[str] = Field(min_length=1, max_length=_MAX_CMD_ENTRIES)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, gt=0, le=_MAX_TIMEOUT_SECONDS)
    cwd: str | None = None
    # action is a free-form discriminator the executor passes through so
    # the orchestrator log line can emit ``action=claude|codex|shell``
    # without having to peek at cmd[0]. Bounded to 32 chars to keep the
    # log shape tight.
    action: str = Field(default="shell", min_length=1, max_length=32)

    @field_validator("cmd")
    @classmethod
    def _bound_cmd_entries(cls, v: list[str]) -> list[str]:
        for entry in v:
            if len(entry) > _MAX_CMD_ENTRY_LEN:
                raise ValueError(
                    f"cmd entry too large (max {_MAX_CMD_ENTRY_LEN} chars)"
                )
        return v

    @field_validator("env")
    @classmethod
    def _bound_env(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > _MAX_ENV_ENTRIES:
            raise ValueError(f"env too large (max {_MAX_ENV_ENTRIES} entries)")
        for key, value in v.items():
            if len(key) > _MAX_ENV_KEY_LEN:
                raise ValueError(
                    f"env key too long (max {_MAX_ENV_KEY_LEN} chars)"
                )
            if len(value) > _MAX_ENV_VALUE_LEN:
                raise ValueError(
                    f"env value too long for key={key!r} "
                    f"(max {_MAX_ENV_VALUE_LEN} chars)"
                )
        return v


class OneShotExecResponse(BaseModel):
    stdout: str
    exit_code: int
    duration_ms: int


def _build_script_cmd(cmd: list[str]) -> list[str]:
    """Wrap a cmd argv as ``script -q -c '<shell-quoted cmd>' /dev/null``.

    Uses util-linux ``script(1)`` syntax (Ubuntu 24.04 base image): the
    command body is passed via ``-c`` and ``/dev/null`` is the typescript
    output file (we discard it). This shape is required because util-linux
    does NOT accept the BSD ``script <file> <command>`` form — it would
    surface ``script: unexpected number of arguments``.

    Each entry is shlex-quoted so embedded ``$VAR`` references survive into
    the ``sh -c`` invocation without being interpreted by docker's argv
    handling. Inside ``sh -c`` the shell expands ``$VAR`` against the env
    dict that the orchestrator passed via ``container.exec(environment=...)``
    — which is the MEM274 secret-passing pattern: API keys never appear in
    the cmd list, only in the env frame.

    NB: shlex.quote uses single quotes, so a literal ``$VAR`` in an entry
    is preserved verbatim (single quotes suppress shell expansion). To get
    expansion, the caller must pass the entry as ``$VAR`` (no quotes around
    the entry itself); the joined ``sh -c`` body then sees ``... $VAR ...``
    after the surrounding shlex quotes are themselves quoted-and-glued.

    To make ``$VAR`` in a cmd entry expand inside ``sh -c``, we don't quote
    entries that look like a single ``$NAME`` reference. This keeps the
    secret-passing contract simple: the executor sends
    ``["claude", "-p", "$PROMPT"]`` plus ``env={"PROMPT": "..."}``, and
    the wrapper produces ``script -q -c 'claude -p "$PROMPT"' /dev/null``.
    """
    quoted: list[str] = []
    for entry in cmd:
        # Bare ``$NAME`` (or ``${NAME}``) → keep as-is so sh expands it.
        # Anything else gets shlex-quoted so spaces / metacharacters survive.
        if _is_bare_var_ref(entry):
            quoted.append(f'"{entry}"')
        else:
            quoted.append(shlex.quote(entry))
    body = " ".join(quoted)
    # script(1) -c runs the command body via the user's shell already
    # (defaults to /bin/sh), so we don't pre-wrap with sh -c. /dev/null
    # is the typescript file we discard. -e (--return) makes script
    # propagate the child's exit code rather than always returning 0
    # — required so the executor can map cli_nonzero (exit != 0) vs
    # the happy path.
    return ["script", "-q", "-e", "-c", body, "/dev/null"]


def _is_bare_var_ref(entry: str) -> bool:
    """True if ``entry`` is a bare ``$NAME`` or ``${NAME}`` reference.

    Variable name follows the POSIX shell rule: starts with letter/underscore,
    rest is alphanumeric/underscore. Anything else (e.g. ``foo$BAR``,
    ``$1``, embedded text) returns False so shlex.quote handles it.
    """
    if not entry or entry[0] != "$":
        return False
    inner = entry[1:]
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    if not inner:
        return False
    if not (inner[0].isalpha() or inner[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in inner)


async def _exec_collect_with_env(
    docker: aiodocker.Docker,
    container_id: str,
    cmd: list[str],
    *,
    environment: dict[str, str],
    timeout_seconds: int,
    cwd: str | None,
) -> tuple[str, int]:
    """Run ``cmd`` inside the workspace container with env overrides + a
    wall-clock timeout. Returns ``(stdout-as-text, exit_code)``.

    Mirrors ``sessions._exec_collect`` and ``clone._exec_with_env`` but adds
    a hard timeout. We open the exec with ``tty=True`` because:
      - the wrapper invokes ``script -q /dev/null`` which already merges
        stdout/stderr; opening with tty makes aiodocker treat the upgraded
        socket as a single byte stream rather than a multiplexed pair, which
        is exactly the shape we want (MEM110).
      - claude/codex CLIs detect tty=False at startup and refuse — that's
        the whole reason for the script wrapper.

    ``stdout`` is hard-capped at ``_MAX_STDOUT_BYTES`` (5 MiB). The cap
    protects the orchestrator's heap if a CLI starts streaming forever; the
    backend's step_runs.stdout column accepts any size, but emitting > 5 MiB
    over a single workflow step is far past the realistic shape (R018
    persistence is for forever-debuggability, not surveillance).

    On timeout we close the exec stream (cancelling the read pump) and
    raise ``OneShotExecTimeout`` — the route maps that to 504. The exec
    process inside the container is left to be reaped on container reap;
    we deliberately do not try to docker-kill it because the workspace
    container is shared with the user's tmux sessions.
    """
    try:
        container = await docker.containers.get(container_id)
        exec_inst = await container.exec(
            cmd=cmd,
            tty=True,
            stdout=True,
            stderr=True,
            environment=environment,
            workdir=cwd,
        )
        out_bytes = bytearray()

        async def _drain(stream: Any) -> None:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                out_bytes.extend(msg.data)
                if len(out_bytes) >= _MAX_STDOUT_BYTES:
                    # Hit the heap cap — let the context manager close
                    # the upgraded socket on __aexit__.
                    del out_bytes[_MAX_STDOUT_BYTES:]
                    return

        async with exec_inst.start(detach=False) as stream:
            try:
                await asyncio.wait_for(_drain(stream), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise OneShotExecTimeout(timeout_seconds) from exc
        info = await exec_inst.inspect()
        exit_code = int(info.get("ExitCode") or 0)
        return out_bytes.decode("utf-8", errors="replace"), exit_code
    except DockerError as exc:
        raise DockerUnavailable(
            f"docker_exec_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc


class OneShotExecTimeout(Exception):
    """Wall-clock timeout while draining the exec stream.

    Raised from ``_exec_collect_with_env``; the route maps it to 504. Carries
    the timeout value purely so the log line records what was breached.
    """

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"oneshot_exec_timeout:{timeout_seconds}s")


@router.post(
    "/{session_id}/exec",
    response_model=OneShotExecResponse,
    status_code=status.HTTP_200_OK,
)
async def oneshot_exec(
    session_id: uuid.UUID, body: OneShotExecBody, request: Request
) -> OneShotExecResponse:
    """Run a single command inside the (user, team) workspace container."""
    docker = request.app.state.docker
    if docker is None:
        # Boot ran with SKIP_IMAGE_PULL_ON_BOOT=1 — no docker handle. Unit
        # tests exercise the auth/middleware shape; integration tests boot
        # a real orchestrator.
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    sid = str(session_id)
    user_id = str(body.user_id)
    team_id = str(body.team_id)

    pg = get_pool()
    container_id, _created = await provision_container(
        docker, user_id, team_id, pg=pg
    )

    wrapped = _build_script_cmd(body.cmd)
    started = time.monotonic()
    logger.info(
        "oneshot_exec_started session_id=%s user_id=%s team_id=%s action=%s",
        sid,
        user_id,
        team_id,
        body.action,
    )
    try:
        stdout, exit_code = await _exec_collect_with_env(
            docker,
            container_id,
            wrapped,
            environment=body.env,
            timeout_seconds=body.timeout_seconds,
            cwd=body.cwd,
        )
    except OneShotExecTimeout as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "oneshot_exec_completed session_id=%s exit=timeout duration_ms=%d",
            sid,
            duration_ms,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "oneshot_exec_timeout",
                "timeout_seconds": exc.timeout_seconds,
                "duration_ms": duration_ms,
            },
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "oneshot_exec_completed session_id=%s exit=%d duration_ms=%d",
        sid,
        exit_code,
        duration_ms,
    )
    return OneShotExecResponse(
        stdout=stdout, exit_code=exit_code, duration_ms=duration_ms
    )
