"""Projects domain — per-team GitHub-linked projects + push-back rules.

M004 / S04 / T01 — persistence + thin REST surface only. The orchestrator
materialize / open path lands in T03; this module ships the substrate every
later S04 task reads from.

Endpoints:

  - GET    /api/v1/teams/{team_id}/projects                — member, list
  - POST   /api/v1/teams/{team_id}/projects                — admin, create
  - GET    /api/v1/projects/{project_id}                   — member (via team)
  - PATCH  /api/v1/projects/{project_id}                   — admin (name only)
  - DELETE /api/v1/projects/{project_id}                   — admin
  - GET    /api/v1/projects/{project_id}/push-rule         — member
  - PUT    /api/v1/projects/{project_id}/push-rule         — admin

Cross-team enumeration is blocked by returning 404 `project_not_found` for
both missing rows and rows whose `team_id` the caller is not a member of
(MEM263 pattern). The team-scoped list endpoint goes through the existing
`assert_caller_is_team_member` / `assert_caller_is_team_admin` gates which
return 404 (Team not found) → 403 (not a member / not an admin) per MEM047.

Logging discipline (slice observability contract):
  INFO  project_created project_id=<uuid> team_id=<uuid> actor_id=<uuid>
        repo=<owner/repo>
  INFO  project_deleted project_id=<uuid> team_id=<uuid> actor_id=<uuid>
  INFO  project_push_rule_updated project_id=<uuid>
        mode=<auto|rule|manual_workflow> actor_id=<uuid>
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.team_access import (
    assert_caller_is_team_admin,
    assert_caller_is_team_member,
)
from app.core.config import settings
from app.core.notify import notify
from app.models import (
    GitHubAppInstallation,
    NotificationKind,
    Project,
    ProjectCreate,
    ProjectPublic,
    ProjectPushRule,
    ProjectPushRulePublic,
    ProjectPushRulePut,
    ProjectsPublic,
    ProjectUpdate,
    TeamMember,
    TeamRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])


_VALID_MODES = ("auto", "rule", "manual_workflow")


# Orchestrator client timeout — generous because the chained
# ensure → materialize-mirror → materialize-user can include a real
# `git clone` over the network on cold first open. The connect timeout
# stays short so a stopped orchestrator surfaces as a fast 503.
_ORCH_TIMEOUT = httpx.Timeout(60.0, connect=3.0)


def _orch_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_project_for_member(
    session: SessionDep, project_id: uuid.UUID, caller_id: uuid.UUID
) -> Project:
    """Return the project iff the caller is a member of its team.

    Returns 404 `project_not_found` for both missing rows and rows owned by a
    team the caller is not a member of — keeps cross-team existence
    non-enumerable.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project_not_found")

    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == project.team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    return project


def _load_project_for_admin(
    session: SessionDep, project_id: uuid.UUID, caller_id: uuid.UUID
) -> Project:
    """Return the project iff the caller is an admin of its team.

    Returns 404 `project_not_found` if the project is missing or the caller
    is not a member at all (existence non-enumerable). Returns 403
    `not_team_admin` if the caller is a member but not an admin — at that
    point existence is already known to the caller.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project_not_found")

    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == project.team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    if membership.role != TeamRole.admin:
        raise HTTPException(status_code=403, detail="not_team_admin")
    return project


def _project_to_public(project: Project) -> ProjectPublic:
    return ProjectPublic(
        id=project.id,
        team_id=project.team_id,
        installation_id=project.installation_id,
        github_repo_full_name=project.github_repo_full_name,
        name=project.name,
        last_push_status=project.last_push_status,
        last_push_error=project.last_push_error,
        created_at=project.created_at,
    )


def _push_rule_to_public(rule: ProjectPushRule) -> ProjectPushRulePublic:
    return ProjectPushRulePublic(
        project_id=rule.project_id,
        mode=rule.mode,
        branch_pattern=rule.branch_pattern,
        workflow_id=rule.workflow_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _validate_push_rule_body(body: ProjectPushRulePut) -> tuple[
    str, str | None, str | None
]:
    """Return (mode, branch_pattern, workflow_id) or raise HTTPException.

    Mode-specific shape: rule needs branch_pattern; manual_workflow needs
    workflow_id; auto stores both as NULL regardless of what was sent. An
    unknown mode is 422 — same status code FastAPI uses for body-schema
    failures so the FE has one error class to render.
    """
    if body.mode not in _VALID_MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_push_rule_mode",
                "mode": body.mode,
                "valid": list(_VALID_MODES),
            },
        )

    if body.mode == "rule":
        if not body.branch_pattern:
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": "branch_pattern_required",
                    "mode": "rule",
                },
            )
        return ("rule", body.branch_pattern, None)
    if body.mode == "manual_workflow":
        if not body.workflow_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": "workflow_id_required",
                    "mode": "manual_workflow",
                },
            )
        return ("manual_workflow", None, body.workflow_id)
    # mode == "auto": both extras stored NULL
    return ("auto", None, None)


# ---------------------------------------------------------------------------
# Team-scoped list / create
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/projects", response_model=ProjectsPublic
)
def list_team_projects(
    *, session: SessionDep, current_user: CurrentUser, team_id: uuid.UUID
) -> Any:
    """List the projects in a team. Member-gated.

    - 404 if team is missing.
    - 403 if caller is not a member of the team.
    - 200 `{data: [ProjectPublic, ...], count: int}` ordered by created_at DESC.
    """
    assert_caller_is_team_member(session, team_id, current_user.id)

    rows = session.exec(
        select(Project)
        .where(Project.team_id == team_id)
        .order_by(col(Project.created_at).desc())
    ).all()
    data = [_project_to_public(row) for row in rows]
    return ProjectsPublic(data=data, count=len(data))


@router.post(
    "/teams/{team_id}/projects", response_model=ProjectPublic
)
def create_team_project(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    body: ProjectCreate,
) -> Any:
    """Create a project + default push-rule (mode=manual_workflow). Admin-gated.

    Validates that the supplied `installation_id` is bound to this team — the
    cross-team installation case returns 404 `installation_not_in_team` so a
    caller cannot enumerate other teams' installations. UNIQUE (team_id, name)
    collision returns 409 `project_name_taken`.
    """
    assert_caller_is_team_admin(session, team_id, current_user.id)

    # Confirm installation_id belongs to this team. We look up by the GitHub
    # installation id (not the row's UUID PK) because that's the natural key
    # the FE knows from `GET /teams/{id}/github/installations`.
    install = session.exec(
        select(GitHubAppInstallation).where(
            GitHubAppInstallation.installation_id == body.installation_id
        )
    ).first()
    if install is None or install.team_id != team_id:
        raise HTTPException(
            status_code=404, detail="installation_not_in_team"
        )

    # UNIQUE collision on (team_id, name) — surface as 409 ahead of the
    # IntegrityError so the FE can branch deterministically.
    existing = session.exec(
        select(Project)
        .where(Project.team_id == team_id)
        .where(Project.name == body.name)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="project_name_taken")

    project = Project(
        team_id=team_id,
        installation_id=body.installation_id,
        github_repo_full_name=body.github_repo_full_name,
        name=body.name,
    )
    rule = ProjectPushRule(project_id=project.id, mode="manual_workflow")
    try:
        session.add(project)
        session.flush()
        session.add(rule)
        session.commit()
        session.refresh(project)
    except Exception:
        session.rollback()
        logger.warning(
            "project_create_tx_rollback team_id=%s actor_id=%s",
            team_id,
            current_user.id,
        )
        raise

    logger.info(
        "project_created project_id=%s team_id=%s actor_id=%s repo=%s",
        project.id,
        team_id,
        current_user.id,
        project.github_repo_full_name,
    )

    # Snapshot the response payload BEFORE the notify side-effect. notify()
    # commits per recipient, which expires the `project` ORM instance on
    # this session — a later attribute read would issue a stale-row reload.
    response = _project_to_public(project)

    # Fan out notifications to every team admin (the project's recipient
    # cohort for the team-default `project_created` channel). Wrapped in
    # try/except so the project create never fails if a notify side-effect
    # somehow re-raises — notify() itself already swallows DB errors, but
    # we belt-and-suspenders here to keep the route's contract intact.
    try:
        recipients = list(
            session.exec(
                select(TeamMember)
                .where(TeamMember.team_id == response.team_id)
                .where(col(TeamMember.role).in_([TeamRole.admin]))
            )
        )
        for recipient in recipients:
            notify(
                session,
                user_id=recipient.user_id,
                kind=NotificationKind.project_created,
                payload={
                    "project_id": str(response.id),
                    "project_name": response.name,
                    "team_id": str(response.team_id),
                    "repo": response.github_repo_full_name,
                },
                source_team_id=response.team_id,
                source_project_id=response.id,
            )
    except Exception:  # noqa: BLE001 — notification side-effect never breaks the route
        logger.warning(
            "project_create_notify_failed project_id=%s team_id=%s",
            response.id,
            response.team_id,
        )

    return response


# ---------------------------------------------------------------------------
# Per-project GET / PATCH / DELETE
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}", response_model=ProjectPublic
)
def get_project(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
) -> Any:
    """Read a project. Member-gated via the project's team.

    - 404 `project_not_found` if missing or owned by a team the caller is
      not a member of.
    """
    project = _load_project_for_member(session, project_id, current_user.id)
    return _project_to_public(project)


@router.patch(
    "/projects/{project_id}", response_model=ProjectPublic
)
def update_project(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: ProjectUpdate,
) -> Any:
    """Rename a project. Admin-gated.

    Today only `name` is updatable. UNIQUE (team_id, name) collisions return
    409 `project_name_taken`.
    """
    project = _load_project_for_admin(session, project_id, current_user.id)

    if body.name != project.name:
        clash = session.exec(
            select(Project)
            .where(Project.team_id == project.team_id)
            .where(Project.name == body.name)
            .where(Project.id != project.id)
        ).first()
        if clash is not None:
            raise HTTPException(
                status_code=409, detail="project_name_taken"
            )
        project.name = body.name
        try:
            session.add(project)
            session.commit()
            session.refresh(project)
        except Exception:
            session.rollback()
            logger.warning(
                "project_update_tx_rollback project_id=%s actor_id=%s",
                project_id,
                current_user.id,
            )
            raise

    return _project_to_public(project)


@router.delete(
    "/projects/{project_id}", status_code=204
)
def delete_project(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
) -> Response:
    """Delete a project. Admin-gated.

    The push_rule row cascades via the FK (ON DELETE CASCADE on
    `project_push_rules.project_id`).
    """
    project = _load_project_for_admin(session, project_id, current_user.id)

    team_id = project.team_id
    try:
        session.delete(project)
        session.commit()
    except Exception:
        session.rollback()
        logger.warning(
            "project_delete_tx_rollback project_id=%s actor_id=%s",
            project_id,
            current_user.id,
        )
        raise

    logger.info(
        "project_deleted project_id=%s team_id=%s actor_id=%s",
        project_id,
        team_id,
        current_user.id,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Per-project push-rule GET / PUT
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/push-rule",
    response_model=ProjectPushRulePublic,
)
def get_project_push_rule(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
) -> Any:
    """Read a project's push-rule. Member-gated via the project's team.

    The rule row is created at project-creation time (default
    mode=manual_workflow), so a 404 here means the project itself was
    missing or not visible to the caller.
    """
    _load_project_for_member(session, project_id, current_user.id)

    rule = session.get(ProjectPushRule, project_id)
    if rule is None:
        # Defensive — should never happen post-create, but cleaner than 500.
        raise HTTPException(status_code=404, detail="push_rule_not_found")
    return _push_rule_to_public(rule)


async def _orch_call_hook_endpoint(
    *,
    project_id: uuid.UUID,
    team_id: uuid.UUID,
    op: str,
) -> None:
    """POST install-push-hook / uninstall-push-hook on the orchestrator.

    Failures are logged WARNING and SWALLOWED — the rule write is the
    source of truth, the hook is derived state. Either:
      (a) the next clone-to-mirror will install/skip per the persisted
          rule, or
      (b) a future PUT push-rule with the same target mode will retry
    Per slice plan: "failures are logged WARNING but DO NOT fail the PUT".
    """
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    url = f"{base}/v1/projects/{project_id}/{op}"
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.post(
                url,
                headers=_orch_headers(),
                json={"team_id": str(team_id)},
            )
        if r.status_code != 200:
            logger.warning(
                "push_hook_orch_call_non_200 op=%s project_id=%s status=%d",
                op,
                project_id,
                r.status_code,
            )
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.HTTPError,
    ) as exc:
        logger.warning(
            "push_hook_orch_call_unreachable op=%s project_id=%s reason=%s",
            op,
            project_id,
            type(exc).__name__,
        )


@router.put(
    "/projects/{project_id}/push-rule",
    response_model=ProjectPushRulePublic,
)
async def put_project_push_rule(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: ProjectPushRulePut,
) -> Any:
    """Update a project's push-rule. Admin-gated.

    Accepts mode + optional branch_pattern + optional workflow_id. Mode-
    specific field validation:
      - mode=rule              → branch_pattern is required
      - mode=manual_workflow   → workflow_id is required
      - mode=auto              → both extras stored as NULL
    Unknown modes return 422.

    On a transition to/from mode=auto the handler also fires a one-shot
    POST to the orchestrator's install-push-hook / uninstall-push-hook
    endpoint to keep the mirror's hook state in sync with the rule. The
    rule write is the source of truth — the hook call is best-effort and
    its failures are logged WARNING but do NOT fail the PUT (the next
    clone-to-mirror reconverges hook state per the persisted rule).
    """
    project = _load_project_for_admin(session, project_id, current_user.id)

    mode, branch_pattern, workflow_id = _validate_push_rule_body(body)

    rule = session.get(ProjectPushRule, project_id)
    if rule is None:
        # Defensive — should never happen post-create.
        raise HTTPException(status_code=404, detail="push_rule_not_found")

    previous_mode = rule.mode

    rule.mode = mode
    rule.branch_pattern = branch_pattern
    rule.workflow_id = workflow_id
    from app.models import get_datetime_utc as _now

    rule.updated_at = _now()
    try:
        session.add(rule)
        session.commit()
        session.refresh(rule)
    except Exception:
        session.rollback()
        logger.warning(
            "project_push_rule_tx_rollback project_id=%s actor_id=%s",
            project_id,
            current_user.id,
        )
        raise

    logger.info(
        "project_push_rule_updated project_id=%s mode=%s actor_id=%s",
        project_id,
        mode,
        current_user.id,
    )

    # Hook lifecycle on auto<->non-auto transitions only. Rule changes that
    # stay within non-auto (rule <-> manual_workflow) need no hook touch
    # because neither mode installs a hook.
    if previous_mode != "auto" and mode == "auto":
        await _orch_call_hook_endpoint(
            project_id=project_id,
            team_id=project.team_id,
            op="install-push-hook",
        )
    elif previous_mode == "auto" and mode != "auto":
        await _orch_call_hook_endpoint(
            project_id=project_id,
            team_id=project.team_id,
            op="uninstall-push-hook",
        )

    return _push_rule_to_public(rule)


# ---------------------------------------------------------------------------
# POST /projects/{id}/open — chained ensure → materialize-mirror → materialize-user
# ---------------------------------------------------------------------------


def _orch_unavailable_503() -> HTTPException:
    """Construct a 503 with the same shape as backend.sessions.

    Centralized so the integration tests can grep for one log marker
    (`orchestrator_unavailable`) regardless of which orchestrator hop
    actually failed in the chain.
    """
    logger.warning(
        "orchestrator_unavailable url=%s op=project_open",
        settings.ORCHESTRATOR_BASE_URL,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="orchestrator_unavailable",
    )


def _propagate_orch_502(orch_response: httpx.Response) -> HTTPException:
    """Forward an orchestrator 502 verbatim to the user.

    The orchestrator's 502 body is ``{detail: "user_clone_failed", reason:
    "user_clone_exit_128", ...}`` (or the mirror equivalent). The FE
    branches on `reason` to decide whether to show "GitHub auth failed"
    vs "your team's mirror is down" vs "we can't resolve the mirror DNS"
    — preserving the body verbatim is the contract.
    """
    try:
        body = orch_response.json()
    except Exception:
        body = {"detail": "orchestrator_clone_failed"}
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=body.get("detail", body)
    )


@router.post("/projects/{project_id}/open")
async def open_project(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Materialize the project into the calling user's workspace. Member-gated.

    The chain (D016 trust boundary: backend gates ownership, orchestrator
    obeys shared-secret):

      1. Load the project + assert caller is a team member (404 on either
         missing or cross-team — MEM263 enumeration block).
      2. POST /v1/teams/{team_id}/mirror/ensure (idempotent — calling
         every time is fine and matches the documented contract; orchestrator
         no-ops when the mirror already runs).
      3. POST /v1/projects/{project_id}/materialize-mirror with
         {team_id, repo_full_name, installation_id} pulled from the project
         row.
      4. POST /v1/projects/{project_id}/materialize-user with
         {user_id, team_id, project_name}.

    Returns ``{workspace_path, mirror_status, user_status, duration_ms}``.

    Failure modes:
      - 404 ``project_not_found`` (missing or cross-team caller).
      - 503 ``orchestrator_unavailable`` (any hop unreachable).
      - 502 — propagated verbatim from the failing hop's body. Common
        reasons: ``github_clone_failed`` (mirror), ``user_clone_failed``
        (user-side; reason=``user_clone_exit_128`` if MEM264 regressed).
    """
    project = _load_project_for_member(session, project_id, current_user.id)

    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            # 2. ensure mirror is up. The orchestrator's ensure is
            #    idempotent so we always call — no need to track state.
            r_ensure = await c.post(
                f"{base}/v1/teams/{project.team_id}/mirror/ensure",
                headers=_orch_headers(),
            )
            if r_ensure.status_code >= 500:
                # 503 from orchestrator → 503 to user (degraded infra).
                # 502/4xx surfaces as the standard 502 propagation.
                if r_ensure.status_code == 503:
                    raise _orch_unavailable_503()
                raise _propagate_orch_502(r_ensure)
            if r_ensure.status_code != 200:
                raise _propagate_orch_502(r_ensure)

            # 3. clone GitHub → mirror.
            r_mirror = await c.post(
                f"{base}/v1/projects/{project_id}/materialize-mirror",
                headers=_orch_headers(),
                json={
                    "team_id": str(project.team_id),
                    "repo_full_name": project.github_repo_full_name,
                    "installation_id": project.installation_id,
                },
            )
            if r_mirror.status_code == 503:
                raise _orch_unavailable_503()
            if r_mirror.status_code != 200:
                raise _propagate_orch_502(r_mirror)
            mirror_body = r_mirror.json()

            # 4. clone mirror → user workspace.
            r_user = await c.post(
                f"{base}/v1/projects/{project_id}/materialize-user",
                headers=_orch_headers(),
                json={
                    "user_id": str(current_user.id),
                    "team_id": str(project.team_id),
                    "project_name": project.name,
                },
            )
            if r_user.status_code == 503:
                raise _orch_unavailable_503()
            if r_user.status_code != 200:
                raise _propagate_orch_502(r_user)
            user_body = r_user.json()
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
    ):
        raise _orch_unavailable_503()

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "project_opened project_id=%s user_id=%s duration_ms=%d",
        project_id,
        current_user.id,
        duration_ms,
    )
    return {
        "workspace_path": user_body["workspace_path"],
        "mirror_status": mirror_body["result"],
        "user_status": user_body["result"],
        "duration_ms": duration_ms,
    }
