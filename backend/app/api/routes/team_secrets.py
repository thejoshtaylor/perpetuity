"""Team-scoped secret API (M005/S01/T03).

Public surface for the per-team `team_secrets` rows that S02–S06 read from
via `get_team_secret`. Four routes — PUT/GET-single/GET-list/DELETE —
mounted under `/api/v1/teams/{team_id}/secrets`.

Authorization split: writes (PUT, DELETE) require `team_admin`, reads (both
GETs) require `team_member`. The membership/admin checks are inlined rather
than calling `assert_caller_is_team_admin` — the shared helper bakes
`Only team admins can invite` into the 403 detail, but the slice plan locks
this surface on `team_admin_required` (the frontend's role-aware UI
disambiguates on that key). Membership SQL shape mirrors `team_access.py`.

Decryption never happens here. PUT validates + encrypts via T02's
`set_team_secret`; the routes that read presence (GET-single / GET-list)
look at `has_value` only. The single decrypt site is `get_team_secret` in
the service module, used by S02+ executors and the test-only round-trip
endpoint (T05). Decrypt failures bubble up as `TeamSecretDecryptError` and
are caught by the global handler in `app/main.py` → 503
`team_secret_decrypt_failed`.

Logging discipline (slice plan locks log keys): INFO `team_secret_set` on
PUT-success and `team_secret_deleted` on DELETE-success carry team_id +
key only. ERROR `team_secret_decrypt_failed` is emitted by the global
handler. The redaction sweep extension (`sk-ant-`, `sk-`) gates that no
value ever lands in any of these lines.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import Session, select

from app.api.deps import CurrentUser, SessionDep
from app.api.team_secrets import (
    delete_team_secret,
    list_team_secret_status,
    set_team_secret,
)
from app.api.team_secrets_registry import (
    InvalidTeamSecretValueError,
    UnregisteredTeamSecretKeyError,
    lookup,
)
from app.models import (
    Team,
    TeamMember,
    TeamRole,
    TeamSecret,
    TeamSecretPut,
    TeamSecretStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["team_secrets"])


def _assert_team_member(
    session: Session, team_id: uuid.UUID, caller_id: uuid.UUID
) -> tuple[Team, TeamMember]:
    """Return (team, membership) when caller is a member of `team_id`, else 404/403.

    The detail strings are the ones the slice plan locks for this surface
    (`team_not_found`, `not_team_member`) — distinct from the
    `Only team admins can invite` literal baked into `team_access.py`'s
    `assert_caller_is_team_admin`, which we cannot reuse without leaking
    invite-specific copy into AI-credentials responses.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(
            status_code=404, detail={"detail": "team_not_found"}
        )
    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if membership is None:
        raise HTTPException(
            status_code=403, detail={"detail": "not_team_member"}
        )
    return team, membership


def _assert_team_admin(
    session: Session, team_id: uuid.UUID, caller_id: uuid.UUID
) -> Team:
    """Return Team when caller is an admin on `team_id`, else 404/403.

    Wraps `_assert_team_member` and adds the role check, surfacing
    `team_admin_required` as the 403 detail (slice plan must-have #3).
    """
    team, membership = _assert_team_member(session, team_id, caller_id)
    if membership.role != TeamRole.admin:
        raise HTTPException(
            status_code=403, detail={"detail": "team_admin_required"}
        )
    return team


def _assert_registered(key: str) -> None:
    """Raise 400 `unregistered_key` if `key` is not in the validator registry.

    Centralized so every route returns the same shape — body is a structured
    object so the frontend can disambiguate without parsing the message.
    """
    try:
        lookup(key)
    except UnregisteredTeamSecretKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail={"detail": "unregistered_key", "key": exc.key},
        ) from exc


@router.put(
    "/{team_id}/secrets/{key}",
    response_model=TeamSecretStatus,
)
def put_team_secret(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    key: str,
    body: TeamSecretPut,
) -> Any:
    """Validate, encrypt, and upsert `team_secrets[(team_id, key)]`.

    Team-admin gate. Unknown key → 400 `unregistered_key`. Validator
    failure → 400 `invalid_value_shape` (with `hint` from the validator's
    `reason` attribute, never the value). On success: returns
    `TeamSecretStatus` with `has_value=True` and the freshly bumped
    `updated_at`; emits INFO `team_secret_set`.

    The plaintext lives only in this function's frame and `set_team_secret`'s
    frame — never logged, never echoed in the response, never attached to
    an exception. The response body is the same shape GET-single returns,
    so the frontend's React Query cache update can use the PUT response
    verbatim without a follow-up GET.
    """
    _assert_team_admin(session, team_id, current_user.id)
    _assert_registered(key)

    try:
        set_team_secret(session, team_id, key, body.value)
    except InvalidTeamSecretValueError as exc:
        # Forward the validator's reason as a structured `hint` — short
        # shape-only string ("bad_prefix", "too_short", "must_be_string");
        # NEVER the value or any prefix of it.
        raise HTTPException(
            status_code=400,
            detail={
                "detail": "invalid_value_shape",
                "key": exc.key,
                "hint": exc.reason,
            },
        ) from exc

    logger.info(
        "team_secret_set team_id=%s key=%s",
        team_id,
        key,
    )

    # Re-fetch the row's metadata for the response. Cheaper than threading
    # the upsert RETURNING through `set_team_secret` and keeps the helper's
    # signature read-free.
    row = session.get(TeamSecret, (team_id, key))
    spec = lookup(key)
    return TeamSecretStatus(
        key=key,
        has_value=True if row is None else row.has_value,
        sensitive=spec.sensitive,
        updated_at=None if row is None else row.updated_at,
    )


@router.get(
    "/{team_id}/secrets",
    response_model=list[TeamSecretStatus],
)
def list_team_secrets(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """Return one `TeamSecretStatus` per registered key for `team_id`.

    Team-member gate. Keys without a row come back with `has_value=False`,
    `updated_at=None`. Order matches the registry's declaration order, so
    the frontend panel renders rows in a stable sequence.

    No value ever crosses this surface — the DTO has no `value` field at all,
    so a future refactor cannot accidentally widen the response.
    """
    _assert_team_member(session, team_id, current_user.id)
    return list_team_secret_status(session, team_id)


@router.get(
    "/{team_id}/secrets/{key}",
    response_model=TeamSecretStatus,
)
def get_team_secret_status(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    key: str,
) -> Any:
    """Return the status row for `(team_id, key)`.

    Team-member gate. Unknown key → 400 `unregistered_key`. Missing row →
    404 `team_secret_not_set`.

    Returns `{key, has_value, sensitive, updated_at}` — never the value.
    """
    _assert_team_member(session, team_id, current_user.id)
    _assert_registered(key)

    row = session.get(TeamSecret, (team_id, key))
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"detail": "team_secret_not_set", "key": key},
        )

    spec = lookup(key)
    return TeamSecretStatus(
        key=key,
        has_value=row.has_value,
        sensitive=spec.sensitive,
        updated_at=row.updated_at,
    )


@router.delete(
    "/{team_id}/secrets/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_team_secret_route(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    key: str,
) -> Response:
    """Remove `(team_id, key)` from team_secrets.

    Team-admin gate. Unknown key → 400 `unregistered_key` (DELETE on an
    unregistered key is a programming error worth surfacing). Missing row →
    404 `team_secret_not_set` so the frontend distinguishes "already gone"
    from "successfully deleted" without a re-GET. On success: 204 empty
    body, INFO log `team_secret_deleted`.
    """
    _assert_team_admin(session, team_id, current_user.id)
    _assert_registered(key)

    deleted = delete_team_secret(session, team_id, key)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"detail": "team_secret_not_set", "key": key},
        )

    logger.info(
        "team_secret_deleted team_id=%s key=%s",
        team_id,
        key,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
