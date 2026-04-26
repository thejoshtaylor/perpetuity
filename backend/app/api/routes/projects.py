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
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.team_access import (
    assert_caller_is_team_admin,
    assert_caller_is_team_member,
)
from app.models import (
    GitHubAppInstallation,
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
    return _project_to_public(project)


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


@router.put(
    "/projects/{project_id}/push-rule",
    response_model=ProjectPushRulePublic,
)
def put_project_push_rule(
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
    """
    _load_project_for_admin(session, project_id, current_user.id)

    mode, branch_pattern, workflow_id = _validate_push_rule_body(body)

    rule = session.get(ProjectPushRule, project_id)
    if rule is None:
        # Defensive — should never happen post-create.
        raise HTTPException(status_code=404, detail="push_rule_not_found")

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
    return _push_rule_to_public(rule)
