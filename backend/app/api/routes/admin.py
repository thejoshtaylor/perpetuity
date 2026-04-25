"""System admin endpoints — bypass per-team membership.

Every route in this module is gated by `get_current_active_superuser`, which
already enforces `current_user.role == UserRole.system_admin` in deps.py.
Per-team membership checks (`_assert_caller_is_team_member` /
`_assert_caller_is_team_admin` from teams.py) are deliberately NOT reused —
system admins can inspect any team's roster and promote any user.

Out of scope for this slice (S05): demote-from-system-admin. The promote
endpoint is one-directional; demotion is future work.

Logs are UUID-only (matches S03 redaction posture) — no email or team name.
"""
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlmodel import Session, col, func, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.models import (
    SystemSetting,
    SystemSettingPublic,
    SystemSettingPut,
    SystemSettingPutResponse,
    SystemSettingShrinkWarning,
    Team,
    TeamMember,
    TeamMemberPublic,
    TeamMembersPublic,
    TeamPublic,
    User,
    UserPublic,
    UserRole,
    WorkspaceVolume,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_active_superuser)],
)


@router.get("/teams")
def read_all_teams(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List every team in the system, paginated, ordered by created_at DESC.

    Returns `{data: [TeamPublic, ...], count: int}` where `count` is the
    unfiltered total (so the FE can render Prev/Next correctly even with
    skip/limit applied). Mirrors the count+skip/limit pattern in
    `users.py::read_users`.
    """
    count_statement = select(func.count()).select_from(Team)
    count = session.exec(count_statement).one()

    statement = (
        select(Team)
        .order_by(col(Team.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    teams = session.exec(statement).all()
    data = [TeamPublic.model_validate(team, from_attributes=True) for team in teams]

    logger.info(
        "admin_teams_listed actor_id=%s skip=%s limit=%s count=%s",
        current_user.id,
        skip,
        limit,
        len(data),
    )
    return {"data": data, "count": count}


@router.get(
    "/teams/{team_id}/members", response_model=TeamMembersPublic
)
def read_admin_team_members(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """Return the roster of any team — does NOT require caller membership.

    - 404 if team missing.
    - 200 `{data: [{user_id, email, full_name, role}, ...], count: int}`.

    Note: deliberately does not call `_assert_caller_is_team_member` from
    teams.py — system admin must be able to inspect teams they aren't on.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    statement = (
        select(User, TeamMember.role)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(col(User.email))
    )
    rows = session.exec(statement).all()
    data = [
        TeamMemberPublic(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=role,
        )
        for user, role in rows
    ]
    logger.info(
        "admin_team_members_listed actor_id=%s team_id=%s count=%s",
        current_user.id,
        team_id,
        len(data),
    )
    return TeamMembersPublic(data=data, count=len(data))


@router.post(
    "/users/{user_id}/promote-system-admin", response_model=UserPublic
)
def promote_system_admin(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    user_id: uuid.UUID,
) -> Any:
    """Promote a user to UserRole.system_admin. Idempotent.

    - 404 if target user does not exist.
    - 200 with the (possibly unchanged) UserPublic on success.
    - If the target is already system_admin, no DB write is performed and
      the log line records `already_admin=true`.

    Demotion (system_admin → user) is intentionally not exposed — out of
    scope for S05. A future slice can add it with last-admin guards.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    already_admin = target.role == UserRole.system_admin
    if not already_admin:
        target.role = UserRole.system_admin
        try:
            session.add(target)
            session.commit()
            session.refresh(target)
        except Exception:
            session.rollback()
            logger.warning(
                "system_admin_promote_tx_rollback actor_id=%s target_user_id=%s",
                current_user.id,
                user_id,
            )
            raise

    logger.info(
        "system_admin_promoted actor_id=%s target_user_id=%s already_admin=%s",
        current_user.id,
        user_id,
        str(already_admin).lower(),
    )
    return target


# ---------------------------------------------------------------------------
# System settings — generic key/value store backing admin-tunable globals.
#
# Reject-by-default: PUTs to keys not in `_VALIDATORS` return 422. This closes
# the foot-gun where a typo in the key would silently add a row that nothing
# reads. New keys must be registered here alongside their validator.
#
# Logging discipline: never log the raw value — future settings may carry
# secrets (e.g. SMTP_PASSWORD); log presence/absence and the key name only.
# ---------------------------------------------------------------------------


WORKSPACE_VOLUME_SIZE_GB_KEY = "workspace_volume_size_gb"
IDLE_TIMEOUT_SECONDS_KEY = "idle_timeout_seconds"


def _validate_workspace_volume_size_gb(value: Any) -> None:
    """Mirror the orchestrator's volume_store range (1..256 GiB).

    bool is a subclass of int in Python — reject it explicitly so a JSON
    `true` doesn't silently coerce to 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": WORKSPACE_VOLUME_SIZE_GB_KEY,
                "reason": "must be int in 1..256",
            },
        )
    if not (1 <= value <= 256):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": WORKSPACE_VOLUME_SIZE_GB_KEY,
                "reason": "must be int in 1..256",
            },
        )


def _validate_idle_timeout_seconds(value: Any) -> None:
    """Mirror the orchestrator's reaper resolver range (1..86400 seconds).

    Same shape as the volume size validator — bool is rejected explicitly
    so JSON `true` doesn't coerce to 1. The new value just biases the
    next reaper tick; no partial-apply warnings are emitted because there
    is no per-row state to reconcile (unlike workspace_volume_size_gb).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 1..86400",
            },
        )
    if not (1 <= value <= 86400):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 1..86400",
            },
        )


_VALIDATORS: dict[str, Callable[[Any], None]] = {
    WORKSPACE_VOLUME_SIZE_GB_KEY: _validate_workspace_volume_size_gb,
    IDLE_TIMEOUT_SECONDS_KEY: _validate_idle_timeout_seconds,
}


def _compute_workspace_size_warnings(
    session: Session, new_value: int
) -> list[SystemSettingShrinkWarning]:
    """Return one warning row per existing volume whose size_gb > new_value.

    usage_bytes is reported as None in this slice — the backend container
    does not mount the workspace_volume host bind, so on-disk usage is not
    reachable. S04 will add a backend→orchestrator usage lookup; the schema
    is forward-compatible.
    """
    statement = (
        select(WorkspaceVolume)
        .where(WorkspaceVolume.size_gb > new_value)
        .order_by(col(WorkspaceVolume.created_at))
    )
    rows = session.exec(statement).all()
    return [
        SystemSettingShrinkWarning(
            user_id=row.user_id,
            team_id=row.team_id,
            size_gb=row.size_gb,
            usage_bytes=None,
        )
        for row in rows
    ]


@router.get("/settings")
def list_system_settings(
    session: SessionDep,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """List all system settings, ordered by key.

    Returns `{data: [SystemSettingPublic, ...], count}`. The full set is
    expected to stay tiny (one row per registered key), so no pagination.
    """
    statement = select(SystemSetting).order_by(col(SystemSetting.key))
    rows = session.exec(statement).all()
    data = [
        SystemSettingPublic.model_validate(row, from_attributes=True)
        for row in rows
    ]
    logger.info(
        "system_settings_listed actor_id=%s count=%s",
        current_user.id,
        len(data),
    )
    return {"data": data, "count": len(data)}


@router.get("/settings/{key}", response_model=SystemSettingPublic)
def get_system_setting(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    key: str,
) -> Any:
    """Return a single system setting or 404."""
    row = session.get(SystemSetting, key)
    if row is None:
        raise HTTPException(status_code=404, detail="setting_not_found")
    logger.info(
        "system_setting_read actor_id=%s key=%s",
        current_user.id,
        key,
    )
    return row


@router.put("/settings/{key}", response_model=SystemSettingPutResponse)
def put_system_setting(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    key: str,
    body: SystemSettingPut,
) -> Any:
    """Validate, UPSERT, and return the setting plus any shrink warnings.

    Reject-by-default on unknown keys. Per-key validators raise 422 with
    `{detail: 'invalid_value_for_key', key, reason}` on bad input.

    For `workspace_volume_size_gb`, also computes the partial-apply shrink
    warnings (D015): rows with size_gb > new_value are reported but not
    rewritten. New volumes pick up the new default; existing rows keep their
    historical cap (cap divergence allowed).
    """
    validator = _VALIDATORS.get(key)
    if validator is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "unknown_setting_key", "key": key},
        )
    validator(body.value)

    previous = session.get(SystemSetting, key)
    previous_value_present = previous is not None

    # UPSERT via Postgres ON CONFLICT. Use raw SQL because SQLAlchemy's
    # JSONB binding handles arbitrary JSON-serializable Python values, and
    # this is the canonical pattern for INSERT...ON CONFLICT in Postgres.
    upsert = text(
        """
        INSERT INTO system_settings (key, value, updated_at)
        VALUES (:key, CAST(:value AS JSONB), NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, updated_at = NOW()
        RETURNING key, value, updated_at
        """
    )
    result = session.execute(
        upsert, {"key": key, "value": json.dumps(body.value)}
    )
    row = result.one()
    session.commit()

    warnings: list[SystemSettingShrinkWarning] = []
    if key == WORKSPACE_VOLUME_SIZE_GB_KEY:
        warnings = _compute_workspace_size_warnings(session, body.value)

    logger.info(
        "system_setting_updated actor_id=%s key=%s previous_value_present=%s",
        current_user.id,
        key,
        str(previous_value_present).lower(),
    )
    if warnings:
        logger.info(
            "system_setting_shrink_warnings_emitted key=%s actor_id=%s affected=%s",
            key,
            current_user.id,
            len(warnings),
        )

    return SystemSettingPutResponse(
        key=row.key,
        value=row.value,
        updated_at=row.updated_at,
        warnings=warnings,
    )
