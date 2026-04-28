"""Notifications domain — in-app bell + per-event preference toggles.

M005 / S02 / T02. The route surface the NotificationBell + NotificationPanel
+ Settings preferences tab consume. All endpoints are scoped to
`current_user.id` — there is no cross-user read path.

Endpoints:

  GET    /notifications                        — list, ORDER BY created_at DESC
  GET    /notifications/unread_count           — badge count
  POST   /notifications/{id}/read              — mark one read (idempotent)
  POST   /notifications/read_all               — mark all unread for caller
  GET    /notifications/preferences            — 7 entries merged with DEFAULTS
  PUT    /notifications/preferences/{event_type} — upsert team-default toggle

Logging discipline (slice observability contract):
  INFO  notifications.list user_id=<uuid> count=<n> unread_only=<bool>
  INFO  notifications.read id=<uuid> user_id=<uuid>
  INFO  notifications.read_all user_id=<uuid> affected=<n>
  INFO  notifications.preference_updated user_id=<uuid>
        event_type=<type> in_app=<bool>
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core.notify import DEFAULTS, notify
from app.models import (
    Notification,
    NotificationKind,
    NotificationPreference,
    NotificationPreferencePublic,
    NotificationPreferencePut,
    NotificationPublic,
    NotificationReadAllResponse,
    NotificationsPublic,
    NotificationTestTrigger,
    NotificationUnreadCount,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ---------------------------------------------------------------------------
# List + counters
# ---------------------------------------------------------------------------


@router.get("", response_model=NotificationsPublic)
def list_notifications(
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = 50,
    unread_only: bool = False,
) -> NotificationsPublic:
    """Chronological notifications feed for the calling user."""
    stmt = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        stmt = stmt.where(col(Notification.read_at).is_(None))
    stmt = stmt.order_by(col(Notification.created_at).desc()).limit(limit)
    rows = list(session.exec(stmt))

    logger.info(
        "notifications.list user_id=%s count=%d unread_only=%s",
        current_user.id,
        len(rows),
        unread_only,
    )
    return NotificationsPublic(
        data=[NotificationPublic.model_validate(r, from_attributes=True) for r in rows],
        count=len(rows),
    )


@router.get("/unread_count", response_model=NotificationUnreadCount)
def unread_count(
    session: SessionDep, current_user: CurrentUser
) -> NotificationUnreadCount:
    """Badge counter — how many unread notifications the caller has."""
    rows = session.exec(
        select(Notification).where(
            Notification.user_id == current_user.id,
            col(Notification.read_at).is_(None),
        )
    ).all()
    return NotificationUnreadCount(count=len(rows))


# ---------------------------------------------------------------------------
# Mark read
# ---------------------------------------------------------------------------


@router.post(
    "/{notification_id}/read", response_model=NotificationPublic
)
def mark_read(
    notification_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> NotificationPublic:
    """Stamp `read_at = NOW()` on a single notification owned by the caller.

    Idempotent — calling on an already-read row is a no-op (returns the row
    with its existing read_at). 404 on missing or cross-user rows; we do not
    differentiate to keep cross-user existence non-enumerable.
    """
    row = session.get(Notification, notification_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="notification_not_found")

    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(
            "notifications.read id=%s user_id=%s",
            row.id,
            current_user.id,
        )
    return NotificationPublic.model_validate(row, from_attributes=True)


@router.post("/read_all", response_model=NotificationReadAllResponse)
def mark_all_read(
    session: SessionDep, current_user: CurrentUser
) -> NotificationReadAllResponse:
    """Mark every unread notification owned by the caller as read."""
    unread = session.exec(
        select(Notification).where(
            Notification.user_id == current_user.id,
            col(Notification.read_at).is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for row in unread:
        row.read_at = now
        session.add(row)
    session.commit()

    logger.info(
        "notifications.read_all user_id=%s affected=%d",
        current_user.id,
        len(unread),
    )
    return NotificationReadAllResponse(affected=len(unread))


# ---------------------------------------------------------------------------
# System-admin seed trigger — proves the bell wiring without a real event
# ---------------------------------------------------------------------------


@router.post("/test", response_model=NotificationPublic)
def trigger_test_notification(
    body: NotificationTestTrigger,
    session: SessionDep,
    actor: User = Depends(get_current_active_superuser),
) -> NotificationPublic:
    """Insert a `kind=system` notification — gated to system_admin.

    Useful as a seed-truth path so an operator can prove the bell renders
    a real row even when no invite/project flow has fired yet. ``user_id``
    in the body resolves to ``actor.id`` when omitted. If the recipient has
    suppressed the ``system`` channel, ``notify()`` returns None and we
    surface 500 ``system_channel_suppressed`` so the operator can tell the
    difference between a wiring bug and an opted-out user.
    """
    target_user_id = body.user_id or actor.id
    logger.info(
        "notifications.test_triggered actor_id=%s target_user_id=%s",
        actor.id,
        target_user_id,
    )
    row = notify(
        session,
        user_id=target_user_id,
        kind=NotificationKind.system,
        payload={"message": body.message},
    )
    if row is None:
        # Either the system channel is opted out or the insert failed.
        # The notify() helper has already logged the actual cause.
        raise HTTPException(
            status_code=500, detail="system_channel_suppressed"
        )
    return NotificationPublic.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def _merge_with_defaults(
    rows: list[NotificationPreference],
    user_id: uuid.UUID,
) -> list[NotificationPreferencePublic]:
    """Return one preference entry per NotificationKind in deterministic order.

    For each kind, prefer the persisted team-default row (workflow_id IS NULL)
    if present; otherwise synthesize a transient `NotificationPreferencePublic`
    populated from `DEFAULTS`. The synthesized rows have the user's id and a
    deterministic-but-fake uuid (zero-uuid) so the UI never sees `null` for
    `id` — it can key the toggle without distinguishing real-vs-default.
    """
    by_event = {r.event_type: r for r in rows if r.workflow_id is None}
    out: list[NotificationPreferencePublic] = []
    zero = uuid.UUID(int=0)
    for kind in NotificationKind:
        existing = by_event.get(kind.value)
        if existing is not None:
            out.append(
                NotificationPreferencePublic.model_validate(
                    existing, from_attributes=True
                )
            )
            continue
        out.append(
            NotificationPreferencePublic(
                id=zero,
                user_id=user_id,
                workflow_id=None,
                event_type=kind.value,
                in_app=DEFAULTS[kind],
                push=False,
                created_at=None,
                updated_at=None,
            )
        )
    return out


@router.get(
    "/preferences",
    response_model=list[NotificationPreferencePublic],
)
def list_preferences(
    session: SessionDep, current_user: CurrentUser
) -> list[NotificationPreferencePublic]:
    """Return one entry per NotificationKind, merging persisted rows with
    the hard-coded DEFAULTS. Team-default rows only — workflow_id IS NULL."""
    rows = list(
        session.exec(
            select(NotificationPreference).where(
                NotificationPreference.user_id == current_user.id,
                col(NotificationPreference.workflow_id).is_(None),
            )
        )
    )
    return _merge_with_defaults(rows, current_user.id)


@router.put(
    "/preferences/{event_type}",
    response_model=NotificationPreferencePublic,
)
def upsert_preference(
    event_type: str,
    body: NotificationPreferencePut,
    session: SessionDep,
    current_user: CurrentUser,
) -> NotificationPreferencePublic:
    """Upsert the team-default (workflow_id IS NULL) preference row for the
    calling user. The seven-value CHECK on the column rejects bad event_type
    at the DB layer — we mirror it here for a friendlier 422 surface."""
    # Validate the event_type against the str-enum contract before any DB write.
    try:
        kind = NotificationKind(event_type)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="unknown_event_type"
        ) from None

    existing = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.user_id == current_user.id,
            col(NotificationPreference.workflow_id).is_(None),
            NotificationPreference.event_type == kind.value,
        )
    ).first()

    now = datetime.now(timezone.utc)
    if existing is None:
        existing = NotificationPreference(
            user_id=current_user.id,
            workflow_id=None,
            event_type=kind.value,
            in_app=body.in_app,
            push=body.push,
        )
        session.add(existing)
    else:
        existing.in_app = body.in_app
        existing.push = body.push
        existing.updated_at = now
        session.add(existing)
    session.commit()
    session.refresh(existing)

    logger.info(
        "notifications.preference_updated user_id=%s event_type=%s in_app=%s",
        current_user.id,
        kind.value,
        body.in_app,
    )
    return NotificationPreferencePublic.model_validate(
        existing, from_attributes=True
    )
