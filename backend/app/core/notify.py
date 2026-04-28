"""In-app notification dispatch helper (M005 / S02).

`notify(session, *, user_id, kind, payload, source_*)` is the single fan-in
every event-emitting site (team_invite_accepted at teams.py, project_created
at projects.py, future workflow runner, admin system test) calls. It
resolves the caller's team-default preference for the kind, optionally
suppresses the in-app row, redacts sensitive payload keys, INSERTs the
notification, and never raises into the calling route. Push channel is a
no-op stub today (S03 will wire it).

The function signature is the slice's stable contract — callers and tests
key off the kw-only shape. Do not reorder arguments without updating
callers in app/api/routes/*.py.
"""

from __future__ import annotations

import logging
import uuid

from sqlmodel import Session, select

from app.models import (
    Notification,
    NotificationKind,
    NotificationPreference,
)

logger = logging.getLogger(__name__)


# Hard-coded team-default in_app fallback when no preference row exists.
# Keep in sync with the seven NotificationKind values — adding a kind
# without updating this dict makes notify() fail-closed for that kind.
DEFAULTS: dict[NotificationKind, bool] = {
    NotificationKind.workflow_run_started: True,
    NotificationKind.workflow_run_succeeded: True,
    NotificationKind.workflow_run_failed: True,
    NotificationKind.workflow_step_completed: False,
    NotificationKind.team_invite_accepted: True,
    NotificationKind.project_created: True,
    NotificationKind.system: True,
}

# Lower-cased substrings that mark a payload key as sensitive. Any key whose
# .lower() contains one of these is redacted at insert time.
_SENSITIVE_SUBSTRS: tuple[str, ...] = ("password", "token", "secret", "email")
_REDACTED = "<redacted>"


def _redact_payload(payload: dict | None) -> dict:
    """Return a shallow-copied payload with sensitive keys masked.

    Mutating a copy keeps the caller's dict pristine — important because the
    payload object often originates inside a request handler that still uses
    it for other purposes (e.g. logging the unredacted team_name).
    """
    if not payload:
        return {}
    out: dict = {}
    for key, value in payload.items():
        kl = str(key).lower()
        if any(s in kl for s in _SENSITIVE_SUBSTRS):
            out[key] = _REDACTED
        else:
            out[key] = value
    return out


def _resolve_in_app(
    session: Session, *, user_id: uuid.UUID, kind: NotificationKind
) -> bool:
    """Read the team-default (workflow_id IS NULL) preference for (user, kind).

    Falls back to DEFAULTS when no row exists. There is no workflow_run →
    workflow lookup wired today (no engine), so source_workflow_run_id is
    intentionally not consulted here — the override path stays schema-only
    until the workflow detail page lands.
    """
    pref = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.workflow_id.is_(None),  # type: ignore[union-attr]
            NotificationPreference.event_type == kind.value,
        )
    ).first()
    if pref is None:
        return DEFAULTS.get(kind, True)
    return bool(pref.in_app)


def _push_stub(*, user_id: uuid.UUID, kind: NotificationKind) -> bool:
    """Push channel is a no-op until S03 wires it. Always returns False so
    the helper signature stays stable across slices."""
    del user_id, kind  # signature-stable no-op
    return False


def notify(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: NotificationKind,
    payload: dict | None = None,
    source_team_id: uuid.UUID | None = None,
    source_project_id: uuid.UUID | None = None,
    source_workflow_run_id: uuid.UUID | None = None,
) -> Notification | None:
    """Dispatch an in-app notification for `user_id`.

    Behavior:
      1. Look up the team-default preference. If `in_app` is False, log
         `notify.skipped_in_app` INFO and return None (no insert).
      2. Otherwise redact sensitive payload keys, INSERT a notifications
         row, log `notify.dispatched`, and return the ORM instance.
      3. Any DB error during insert is caught: log `notify.insert_failed`
         ERROR with the exception class and return None. Never re-raise —
         a notification side-effect must not fail the caller's route.
    """
    try:
        in_app = _resolve_in_app(session, user_id=user_id, kind=kind)
        if not in_app:
            logger.info(
                "notify.skipped_in_app user_id=%s kind=%s reason=preference_off",
                user_id,
                kind.value,
            )
            return None

        redacted = _redact_payload(payload)
        row = Notification(
            user_id=user_id,
            kind=kind.value,
            payload=redacted,
            source_team_id=source_team_id,
            source_project_id=source_project_id,
            source_workflow_run_id=source_workflow_run_id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)

        # Push channel still no-op; recorded for grep symmetry.
        _push_stub(user_id=user_id, kind=kind)

        logger.info(
            "notify.dispatched user_id=%s kind=%s notification_id=%s "
            "in_app=true source_team_id=%s source_workflow_run_id=%s",
            user_id,
            kind.value,
            row.id,
            source_team_id if source_team_id is not None else "none",
            source_workflow_run_id
            if source_workflow_run_id is not None
            else "none",
        )
        return row
    except Exception as exc:  # noqa: BLE001 — slice contract: never re-raise
        # Roll back the partial transaction so the caller's session is reusable.
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.error(
            "notify.insert_failed user_id=%s kind=%s cause=%s",
            user_id,
            kind.value,
            exc.__class__.__name__,
        )
        return None
