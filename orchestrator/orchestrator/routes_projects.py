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

from orchestrator.auto_push import run_auto_push
from orchestrator.clone import (
    _CloneExecFailed,
    _install_post_receive_hook,
    _uninstall_post_receive_hook,
    clone_to_mirror,
    clone_to_user_workspace,
)
from orchestrator.errors import (
    CloneCredentialLeakDetected,
    DockerUnavailable,
)
from orchestrator.github_tokens import InstallationTokenMintFailed
from orchestrator.team_mirror import _find_team_mirror_container
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


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/materialize-user (M004/S04/T03)
# ---------------------------------------------------------------------------


class MaterializeUserRequest(BaseModel):
    """POST /v1/projects/{project_id}/materialize-user body.

    The backend fills these from the project + the calling user — the
    orchestrator does not look anything up itself (D016: orchestrator is
    trusted to obey the shared-secret boundary, but ownership is enforced
    on the backend).
    """

    user_id: uuid.UUID
    team_id: uuid.UUID
    project_name: str = Field(min_length=1, max_length=255)


class MaterializeUserResponse(BaseModel):
    """Body of POST /v1/projects/{project_id}/materialize-user.

    ``result`` is ``'created'`` for a fresh clone or ``'reused'`` when the
    user already had this project cloned and we short-circuited.
    ``workspace_path`` is the absolute path inside the user container.
    """

    result: str
    duration_ms: int
    workspace_path: str


@router.post(
    "/{project_id}/materialize-user",
    response_model=MaterializeUserResponse,
    status_code=status.HTTP_200_OK,
)
async def post_materialize_user(
    project_id: uuid.UUID,
    body: MaterializeUserRequest,
    request: Request,
) -> MaterializeUserResponse:
    """Clone the team-mirror's bare repo into the user's workspace.

    Idempotent: a re-call after a successful clone returns
    ``{result:'reused', duration_ms:0}`` without re-cloning.

    Failure modes:
      - 502 ``user_clone_failed`` — git clone returned non-zero
        (reason=``user_clone_exit_<code>``). The most common cause in
        steady-state is a MEM264 regression — name resolution failed
        because the user container is no longer on ``perpetuity_default``.
      - 500 ``clone_credential_leak`` — defensive safety net (see
        clone.clone_to_user_workspace docstring).
      - 503 ``docker_unavailable`` — docker daemon trouble (app handler).
    """
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    pool = get_pool()

    try:
        result = await clone_to_user_workspace(
            docker,
            pool,
            user_id=str(body.user_id),
            team_id=str(body.team_id),
            project_id=str(project_id),
            project_name=body.project_name,
        )
    except _CloneExecFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "user_clone_failed",
                "status": exc.exit_code,
                "reason": f"user_clone_exit_{exc.exit_code}",
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

    return MaterializeUserResponse(
        result=str(result["result"]),
        duration_ms=int(result["duration_ms"]),
        workspace_path=str(result["workspace_path"]),
    )


# ---------------------------------------------------------------------------
# Hook management endpoints (M004/S04/T04)
# ---------------------------------------------------------------------------


class HookManagementRequest(BaseModel):
    """POST /v1/projects/{project_id}/(install|uninstall)-push-hook body.

    The backend resolves ``team_id`` from the project row before calling.
    Both endpoints are no-ops if the team's mirror container does not
    currently exist — the next clone-to-mirror will install/skip per the
    persisted rule, so the lifecycle eventually re-converges.
    """

    team_id: uuid.UUID


class HookManagementResponse(BaseModel):
    """Body of the install/uninstall hook endpoints.

    ``result`` is one of:
      - 'installed'        — hook written to the mirror's bare repo
      - 'uninstalled'      — hook removed (file may or may not have existed)
      - 'mirror_missing'   — no running mirror container for this team
                             (no-op; next clone-to-mirror will reconverge)
      - 'rule_not_auto'    — install attempted but mode is not 'auto'
                             (no hook installed)
    """

    result: str


@router.post(
    "/{project_id}/install-push-hook",
    response_model=HookManagementResponse,
    status_code=status.HTTP_200_OK,
)
async def post_install_push_hook(
    project_id: uuid.UUID,
    body: HookManagementRequest,
    request: Request,
) -> HookManagementResponse:
    """Install the post-receive hook for ``project_id`` on the team mirror.

    Called by the backend's PUT /push-rule when transitioning a project
    to mode=auto. No-op if the mirror container does not currently exist
    — the next clone-to-mirror will install the hook per the persisted
    rule (the rule is the source of truth; the hook is derived state).
    """
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    team_id_str = str(body.team_id)
    mirror_id = await _find_team_mirror_container(docker, team_id_str)
    if mirror_id is None:
        logger.info(
            "post_receive_hook_install_skipped_no_mirror "
            "project_id=%s team_id=%s",
            project_id,
            team_id_str,
        )
        return HookManagementResponse(result="mirror_missing")

    try:
        installed = await _install_post_receive_hook(
            docker,
            mirror_container_id=mirror_id,
            project_id=str(project_id),
            push_rule_mode="auto",
        )
    except _CloneExecFailed as exc:
        # Hook install failure on the management endpoint is a 502 — the
        # rule was already persisted by the backend, but the derived state
        # didn't land. The backend logs WARNING and does NOT fail the PUT;
        # surface 502 here so the test surface can distinguish from 200.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "post_receive_hook_install_failed",
                "exit_code": exc.exit_code,
                "op": exc.op,
            },
        )

    if not installed:
        return HookManagementResponse(result="rule_not_auto")
    return HookManagementResponse(result="installed")


@router.post(
    "/{project_id}/uninstall-push-hook",
    response_model=HookManagementResponse,
    status_code=status.HTTP_200_OK,
)
async def post_uninstall_push_hook(
    project_id: uuid.UUID,
    body: HookManagementRequest,
    request: Request,
) -> HookManagementResponse:
    """Remove the post-receive hook for ``project_id`` from the team mirror.

    Called by the backend's PUT /push-rule when transitioning a project
    away from mode=auto. No-op if the mirror container does not currently
    exist — there's nothing to remove, and the next clone-to-mirror will
    correctly skip hook install per the persisted (non-auto) rule.
    """
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    team_id_str = str(body.team_id)
    mirror_id = await _find_team_mirror_container(docker, team_id_str)
    if mirror_id is None:
        logger.info(
            "post_receive_hook_uninstall_skipped_no_mirror "
            "project_id=%s team_id=%s",
            project_id,
            team_id_str,
        )
        return HookManagementResponse(result="mirror_missing")

    await _uninstall_post_receive_hook(
        docker,
        mirror_container_id=mirror_id,
        project_id=str(project_id),
    )
    return HookManagementResponse(result="uninstalled")


# ---------------------------------------------------------------------------
# Auto-push callback (M004/S04/T04)
# ---------------------------------------------------------------------------


class AutoPushCallbackBody(BaseModel):
    """Optional JSON body for POST /v1/projects/{project_id}/auto-push-callback.

    Legacy callers (the post-receive hook script) send no body; the route
    defaults to an empty model so both old and new callers are compatible.

    ``ref`` is the full Git ref string (e.g. ``refs/heads/feature/foo``)
    forwarded from the webhook payload when the backend triggers a
    mode='rule' dispatch on behalf of a push event.
    """

    ref: str | None = None


class AutoPushCallbackResponse(BaseModel):
    """Body of POST /v1/projects/{project_id}/auto-push-callback.

    The post-receive hook ignores the response (it has `|| true` on the
    wget), but downstream tooling and tests benefit from a structured
    body. ``result`` carries the same shape returned by ``run_auto_push``.
    """

    result: str
    exit_code: int | None = None
    duration_ms: int | None = None
    stderr_short: str | None = None


@router.post(
    "/{project_id}/auto-push-callback",
    response_model=AutoPushCallbackResponse,
    status_code=status.HTTP_200_OK,
)
async def post_auto_push_callback(
    project_id: uuid.UUID,
    request: Request,
    body: AutoPushCallbackBody = AutoPushCallbackBody(),
) -> AutoPushCallbackResponse:
    """Auto-push trigger from the mirror's post-receive hook or webhook dispatch.

    The endpoint is gated by the orchestrator-wide SharedSecretMiddleware
    (the hook script presents X-Orchestrator-Key from PERPETUITY_ORCH_KEY
    in the mirror's env). Legacy callers (post-receive hook) send no body;
    webhook-triggered mode='rule' dispatch forwards ``{"ref": "refs/heads/..."}``
    in the body.

    Always returns 200 with the run_auto_push result body. The post-receive
    hook ignores the status code anyway (auto-push is best-effort by D024).
    """
    docker = request.app.state.docker
    if docker is None:
        raise DockerUnavailable("docker_handle_unavailable_in_lifespan")

    pool = get_pool()
    redis_client = _redis_client_from(request)

    result = await run_auto_push(
        docker,
        pool,
        project_id=str(project_id),
        redis_client=redis_client,
        ref=body.ref,
    )

    return AutoPushCallbackResponse(
        result=str(result.get("result", "unknown")),
        exit_code=(
            int(result["exit_code"]) if "exit_code" in result else None
        ),
        duration_ms=(
            int(result["duration_ms"]) if "duration_ms" in result else None
        ),
        stderr_short=(
            str(result["stderr_short"])
            if "stderr_short" in result
            else None
        ),
    )


__all__: list[str] = ["router"]
