"""HTTP routes for per-team mirror lifecycle (M004/S03/T02).

Endpoints:
  - ``POST /v1/teams/{team_id}/mirror/ensure`` — idempotent ensure-spinup
    of the team's mirror container; returns ``{container_id, network_addr}``
  - ``POST /v1/teams/{team_id}/mirror/reap`` — admin force-reap; returns
    ``{reaped: bool}``

Auth: every route is gated by the shared-secret middleware. The
orchestrator does NOT enforce per-team ownership — backend does that
before forwarding (D016, mirrors routes_sessions.py). The orchestrator
trusts the backend's shared-secret presentation.

Negative tests in the slice plan:
  - malformed UUID in path → 422 (handled by pydantic via the path-typed
    parameter)
  - reap on unknown team_id → 200 ``{reaped: false}`` (idempotent — the
    orchestrator never owned a container for the team)
  - ensure with docker unreachable → 503 ``docker_unavailable`` (handled
    by the app-level exception handler)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from orchestrator.errors import DockerUnavailable
from orchestrator.team_mirror import ensure_team_mirror, reap_team_mirror
from orchestrator.volume_store import get_pool

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/teams", tags=["team_mirror"])


class EnsureMirrorResponse(BaseModel):
    """Body of POST /v1/teams/{team_id}/mirror/ensure.

    ``container_id`` is the full docker id (caller may truncate for
    display). ``network_addr`` is what user containers should dial:
    ``team-mirror-<first8-team>:9418`` (D023). ``reused`` is True when
    a running container was found for the team and we did not have to
    create one — useful for the backend to know whether to clear any
    transient "warming up" state.
    """

    container_id: str
    network_addr: str
    reused: bool


class ReapMirrorResponse(BaseModel):
    """Body of POST /v1/teams/{team_id}/mirror/reap.

    ``reaped`` is True when a container was actually stopped+removed,
    False when the team had no running container (idempotent — the route
    is safe to call repeatedly without the caller checking state first).
    """

    reaped: bool


@router.post(
    "/{team_id}/mirror/ensure",
    response_model=EnsureMirrorResponse,
    status_code=status.HTTP_200_OK,
)
async def post_ensure_mirror(
    team_id: uuid.UUID, request: Request
) -> EnsureMirrorResponse:
    """Ensure the team's mirror container is running. Idempotent.

    Per-route observability:
      - INFO  team_mirror_started      (first ensure for the team)
      - INFO  team_mirror_reused       (running container found)
      - INFO  team_mirror_create_race_detected (concurrent ensure tie-break)

    Failure modes:
      - Docker unreachable → 503 docker_unavailable (app handler)
      - Postgres unreachable → 503 workspace_volume_store_unavailable
        (app handler)
    """
    docker = request.app.state.docker
    if docker is None:
        # Boot ran with SKIP_IMAGE_PULL_ON_BOOT=1 — mirror ensure can't
        # work without a docker handle. Mirrors routes_sessions.py.
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    pg = get_pool()
    result = await ensure_team_mirror(pg, docker, str(team_id))
    return EnsureMirrorResponse(
        container_id=str(result["container_id"]),
        network_addr=str(result["network_addr"]),
        reused=bool(result.get("reused", False)),
    )


@router.post(
    "/{team_id}/mirror/reap",
    response_model=ReapMirrorResponse,
    status_code=status.HTTP_200_OK,
)
async def post_reap_mirror(
    team_id: uuid.UUID, request: Request
) -> ReapMirrorResponse:
    """Force-reap the team's mirror container. Idempotent.

    Used by the team-admin "stop the mirror now" affordance and by
    integration tests. The reaper handles the steady-state idle case;
    this route is the imperative escape hatch.

    Failure modes match ensure: 503 on Docker / Postgres outage.
    """
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    pg = get_pool()
    reaped = await reap_team_mirror(pg, docker, str(team_id), reason="admin")
    return ReapMirrorResponse(reaped=reaped)


__all__: list[str] = ["router"]
