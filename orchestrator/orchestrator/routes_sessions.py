"""HTTP routes for session lifecycle (T03).

Endpoints:
  - `POST   /v1/sessions`                     — provision container + start tmux + register in Redis
  - `GET    /v1/sessions?user_id=&team_id=`   — list sessions for (user, team)
  - `DELETE /v1/sessions/{session_id}`        — kill tmux + drop Redis record
  - `POST   /v1/sessions/{session_id}/scrollback` — capture-pane (used by WS attach in T04)
  - `POST   /v1/sessions/{session_id}/resize` — refresh-client cols,rows

Auth: every route is gated by the shared-secret middleware from T02. The
orchestrator does NOT enforce per-user ownership — backend does that before
forwarding (D016, slice plan). The orchestrator trusts the backend's
shared-secret presentation.

Negative tests in the slice plan:
  - malformed UUIDs in body         → 422 (handled by pydantic)
  - missing X-Orchestrator-Key      → 401 (handled by middleware in T02)
  - resize on non-existent session  → 404
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from orchestrator.errors import DockerUnavailable
from orchestrator.redis_client import get_registry
from orchestrator.sessions import (
    TmuxCommandFailed,
    capture_scrollback,
    kill_tmux_session,
    list_tmux_sessions,
    provision_container,
    resize_tmux_session,
    start_tmux_session,
)
from orchestrator.volume_store import get_pool

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


# ----- request/response models ---------------------------------------------


class CreateSessionBody(BaseModel):
    """POST /v1/sessions body.

    pydantic enforces UUID-shape on every id field (slice plan negative test:
    "malformed UUIDs in body → 422"). We accept str-shaped UUIDs and convert
    rather than `uuid.UUID` directly so the response stringifies cleanly.
    """

    session_id: uuid.UUID
    user_id: uuid.UUID
    team_id: uuid.UUID


class CreateSessionResponse(BaseModel):
    session_id: str
    container_id: str
    tmux_session: str
    created: bool


class ResizeBody(BaseModel):
    cols: int = Field(gt=0, le=1000)
    rows: int = Field(gt=0, le=1000)


class ScrollbackResponse(BaseModel):
    scrollback: str


# ----- routes --------------------------------------------------------------


@router.post(
    "",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def create_session(
    body: CreateSessionBody, request: Request
) -> CreateSessionResponse:
    """Provision container + start a named tmux session + register in Redis.

    Idempotent on (user_id, team_id, session_id): a second call with the same
    triple returns the same container_id and `created: false`. The tmux
    session is also reused if already present (the sessions module tolerates
    `duplicate session`).

    Per-route observability:
      - INFO  container_provisioned (emitted from sessions.provision_container)
      - INFO  session_created (emitted from sessions.start_tmux_session)

    Failure modes:
      - Docker unreachable     → 503 docker_unavailable (caught by app handler)
      - Redis unreachable      → 503 redis_unavailable (caught by app handler)
      - Volume mount failed    → 500 volume_mount_failed (S02 owns rich shape)
    """
    docker = request.app.state.docker
    if docker is None:
        # Boot ran with SKIP_IMAGE_PULL_ON_BOOT=1 — sessions can't work
        # without a docker handle. This path is only hit by the unit test
        # suite; integration tests boot a real orchestrator.
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    user_id = str(body.user_id)
    team_id = str(body.team_id)
    session_id = str(body.session_id)

    pg = get_pool()
    container_id, created = await provision_container(
        docker, user_id, team_id, pg=pg
    )
    await start_tmux_session(docker, container_id, session_id)

    registry = get_registry()
    record: dict[str, Any] = {
        "container_id": container_id,
        "tmux_session": session_id,
        "user_id": user_id,
        "team_id": team_id,
    }
    await registry.set_session(session_id, record)

    return CreateSessionResponse(
        session_id=session_id,
        container_id=container_id,
        tmux_session=session_id,
        created=created,
    )


@router.get("", response_model=list[dict])
async def list_sessions(
    user_id: uuid.UUID = Query(...),
    team_id: uuid.UUID = Query(...),
) -> list[dict[str, Any]]:
    """List sessions belonging to (user_id, team_id) per the Redis index.

    Backend enforces ownership before calling this endpoint; orchestrator
    just returns the records.
    """
    registry = get_registry()
    return await registry.list_sessions(str(user_id), str(team_id))


@router.get("/by-id/{session_id}", response_model=dict)
async def get_session_by_id(session_id: uuid.UUID) -> dict[str, Any]:
    """Look up a single session record by id.

    Added in T05 because the backend's WS bridge and DELETE handler need an
    O(1) ownership check (record.user_id == caller.id). The list endpoint
    requires (user_id, team_id) which the backend doesn't know yet at the
    point it's enforcing ownership — adding by-id keeps the orchestrator
    responsible for the storage shape and the backend responsible for the
    policy decision.

    Returns 404 if the session does not exist. Backend translates that 404
    into a session-shaped 1008/404 (no enumeration) — the orchestrator
    itself does NOT enforce per-user policy here (D016).
    """
    registry = get_registry()
    record = await registry.get_session(str(session_id))
    if record is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return record


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(session_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Kill the named tmux session and drop the Redis record.

    Container is intentionally not stopped — sibling tmux sessions on the
    same container stay alive (R008). The S04 idle reaper handles the
    container lifecycle.

    Returns `{deleted: true}` if the Redis record existed, `{deleted: false}`
    if it had already been cleaned up. Always returns 200 (idempotent on a
    missing record — callers shouldn't have to special-case "already gone").
    """
    sid = str(session_id)
    registry = get_registry()
    record = await registry.get_session(sid)
    if record is None:
        # Nothing to do; happily idempotent.
        return {"session_id": sid, "deleted": False}

    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    container_id = record.get("container_id")
    if container_id:
        try:
            killed = await kill_tmux_session(docker, container_id, sid)
        except TmuxCommandFailed as exc:
            # The Redis record was created on the assumption that the tmux
            # session existed; if tmux_ls disagrees we still drop the Redis
            # record but log a tmux_session_orphaned WARNING per the slice
            # observability taxonomy.
            logger.warning(
                "tmux_session_orphaned session_id=%s container_id=%s reason=%s",
                sid,
                str(container_id)[:12],
                exc.output.strip()[:120] if exc.output else "<no output>",
            )
            killed = False
    else:
        killed = False

    deleted = await registry.delete_session(sid)
    logger.info(
        "session_detached session_id=%s killed=%s registry_deleted=%s",
        sid,
        killed,
        deleted,
    )
    return {"session_id": sid, "deleted": deleted, "tmux_killed": killed}


@router.post(
    "/{session_id}/scrollback", response_model=ScrollbackResponse
)
async def get_scrollback(
    session_id: uuid.UUID, request: Request
) -> ScrollbackResponse:
    """Return the current scrollback for a session, hard-capped to
    `scrollback_max_bytes` (default 100 KB) per D017.

    Used by T04's WS attach to send the initial `{type:'attach', scrollback}`
    frame. Modeled as POST per the slice plan even though it's read-only —
    a future iteration could move it to GET; the choice is locked to the
    plan to keep frame-protocol contracts stable.
    """
    sid = str(session_id)
    registry = get_registry()
    record = await registry.get_session(sid)
    if record is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    container_id = str(record["container_id"])
    try:
        scrollback = await capture_scrollback(docker, container_id, sid)
    except TmuxCommandFailed as exc:
        logger.warning(
            "tmux_session_orphaned session_id=%s container_id=%s reason=%s",
            sid,
            container_id[:12],
            exc.output.strip()[:120] if exc.output else "<no output>",
        )
        scrollback = ""

    return ScrollbackResponse(scrollback=scrollback)


@router.post(
    "/{session_id}/resize", status_code=status.HTTP_200_OK
)
async def resize_session(
    session_id: uuid.UUID, body: ResizeBody, request: Request
) -> dict[str, Any]:
    """Resize the tmux session's pane.

    Returns 404 if the tmux session does not exist (slice plan negative
    test: "resize on non-existent session → 404"). 200 with `{ok: true}`
    on success.
    """
    sid = str(session_id)
    registry = get_registry()
    record = await registry.get_session(sid)
    if record is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    container_id = str(record["container_id"])
    try:
        await resize_tmux_session(docker, container_id, sid, body.cols, body.rows)
    except TmuxCommandFailed as exc:
        if "tmux_session_not_found" in str(exc):
            raise HTTPException(status_code=404, detail="session_not_found")
        # Other failures: surface as 500 with a stable code so backend can
        # treat it as a transient / retry-after-attach condition.
        logger.error(
            "tmux_resize_failed session_id=%s container_id=%s",
            sid,
            container_id[:12],
        )
        raise HTTPException(status_code=500, detail="tmux_resize_failed")

    return {"ok": True}


# ----- diagnostic / internal helpers ---------------------------------------


async def list_container_tmux_sessions(
    request: Request, container_id: str
) -> list[str]:
    """Internal helper used by the integration test to assert tmux ls output.

    Not registered on the router — exposed at module level so tests can
    invoke it directly without the round-trip through HTTP.
    """
    docker = request.app.state.docker
    return await list_tmux_sessions(docker, container_id)
