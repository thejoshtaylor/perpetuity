"""HTTP routes for project materialization (M004/S04/T02).

Endpoints:
  - ``POST /v1/projects/{project_id}/materialize-mirror`` — clone
    ``repo_full_name`` into the team's mirror as a bare repo. Idempotent:
    re-materializing a project that already has a bare repo returns
    ``{result:'reused', duration_ms:0}`` without touching GitHub.

Auth: gated by the orchestrator-wide SharedSecretMiddleware (X-Orchestrator-Key).
The backend forwards the team-admin's create-project call here after
resolving installation_id from the team's connection row.

Error mapping:
  502 github_clone_failed   — InstallationTokenMintFailed (token mint /
                              cache-side failure) OR git-clone exec
                              non-zero (auth fail, repo-not-found, etc.)
  500 clone_credential_leak — CloneCredentialLeakDetected (the
                              never-reached safety net)
  503 docker_unavailable    — DockerUnavailable (existing app handler)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from orchestrator.clone import _CloneExecFailed, clone_to_mirror
from orchestrator.errors import (
    CloneCredentialLeakDetected,
    DockerUnavailable,
)
from orchestrator.github_tokens import InstallationTokenMintFailed
from orchestrator.volume_store import get_pool

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class MaterializeMirrorRequest(BaseModel):
    """POST /v1/projects/{project_id}/materialize-mirror body.

    The backend resolves ``installation_id`` from the team's connection
    row before calling. ``repo_full_name`` is ``<owner>/<repo>``.
    """

    team_id: uuid.UUID
    repo_full_name: str = Field(min_length=3, max_length=200)
    installation_id: int = Field(ge=1, le=2_147_483_647)


class MaterializeMirrorResponse(BaseModel):
    """Body of POST /v1/projects/{project_id}/materialize-mirror.

    ``result`` is either ``'created'`` (a fresh clone) or ``'reused'``
    (the bare repo was already present in the mirror, so we no-op'd).
    ``duration_ms`` is wall-clock from clone start to atomic rename
    completion, 0 on the reused path.
    """

    result: str
    duration_ms: int


def _redis_client_from(request: Request):  # type: ignore[no-untyped-def]
    """Pull the underlying redis.asyncio client off the registry.

    Mirrors routes_github._redis_client_from — same registry, same
    keyspace prefix (``gh:installtok:``). Returning None is fine; the
    token cache is best-effort and the mint path will succeed without it.
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return None
    return getattr(registry, "_client", None)


@router.post(
    "/{project_id}/materialize-mirror",
    response_model=MaterializeMirrorResponse,
    status_code=status.HTTP_200_OK,
)
async def post_materialize_mirror(
    project_id: uuid.UUID,
    body: MaterializeMirrorRequest,
    request: Request,
) -> MaterializeMirrorResponse:
    """Materialize the project's GitHub repo into the team's mirror.

    Idempotent: a re-call after a successful clone returns
    ``{result:'reused', duration_ms:0}`` without minting a token.

    Failure modes:
      - 502 ``github_clone_failed`` — token mint failed (status/reason from
        InstallationTokenMintFailed) OR git clone returned non-zero
        (reason=``git_clone_exit_<code>``).
      - 500 ``clone_credential_leak`` — the structural safety net fired.
      - 503 ``docker_unavailable`` — docker daemon trouble (app handler).
    """
    docker = request.app.state.docker
    if docker is None:
        # Boot ran with SKIP_IMAGE_PULL_ON_BOOT=1 — clone path can't work
        # without a docker handle. Mirrors routes_team_mirror.
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    pool = get_pool()
    redis_client = _redis_client_from(request)

    try:
        result = await clone_to_mirror(
            docker,
            pool,
            team_id=str(body.team_id),
            project_id=str(project_id),
            repo_full_name=body.repo_full_name,
            installation_id=body.installation_id,
            redis_client=redis_client,
        )
    except InstallationTokenMintFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_clone_failed",
                "status": exc.status,
                "reason": exc.reason,
            },
        )
    except _CloneExecFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_clone_failed",
                "status": exc.exit_code,
                "reason": f"git_clone_exit_{exc.exit_code}",
            },
        )
    except CloneCredentialLeakDetected as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "detail": "clone_credential_leak",
                "project_id": exc.project_id,
            },
        )

    return MaterializeMirrorResponse(
        result=str(result["result"]),
        duration_ms=int(result["duration_ms"]),
    )


__all__: list[str] = ["router"]
