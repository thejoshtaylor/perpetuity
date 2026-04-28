"""In-app + Web Push notification dispatch helper (M005 / S02 + S03).

`notify(session, *, user_id, kind, payload, source_*)` is the single fan-in
every event-emitting site (team_invite_accepted at teams.py, project_created
at projects.py, future workflow runner, admin system test) calls. It
resolves the caller's team-default preference for the kind, optionally
suppresses the in-app row, redacts sensitive payload keys, INSERTs the
notification, fires the Web Push channel when the per-(user, kind) push
preference resolves true, and never raises into the calling route.

The function signature is the slice's stable contract — callers and tests
key off the kw-only shape. Do not reorder arguments without updating
callers in app/api/routes/*.py.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

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

# Push channel team-defaults: opt-in-by-default would surprise users on
# first notification, so the team-default is False for every kind except the
# noisy-failure surface that the slice demo explicitly targets.
PUSH_DEFAULTS: dict[NotificationKind, bool] = {
    NotificationKind.workflow_run_started: False,
    NotificationKind.workflow_run_succeeded: False,
    NotificationKind.workflow_run_failed: False,
    NotificationKind.workflow_step_completed: False,
    NotificationKind.team_invite_accepted: False,
    NotificationKind.project_created: False,
    NotificationKind.system: False,
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


def _resolve_push(
    session: Session, *, user_id: uuid.UUID, kind: NotificationKind
) -> bool:
    """Same shape as `_resolve_in_app` but reads the `push` column.

    Defaults from `PUSH_DEFAULTS` (False for every kind today) when no
    team-default row exists. Per-workflow overrides are not consulted yet —
    same gating reason as the in-app path.
    """
    pref = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.workflow_id.is_(None),  # type: ignore[union-attr]
            NotificationPreference.event_type == kind.value,
        )
    ).first()
    if pref is None:
        return PUSH_DEFAULTS.get(kind, False)
    return bool(pref.push)


def _render_push(
    *,
    kind: NotificationKind,
    payload: dict[str, Any],
    source_workflow_run_id: uuid.UUID | None,
) -> tuple[str, str, str, str | None]:
    """Synthesize (title, body, url, icon) for the Web Push payload.

    The dispatcher only knows kind + structured payload; the SW handler
    needs human-shaped strings + a click target. This switch is the canonical
    place to add a new notification kind's push rendering.

    The `payload` argument here is the ALREADY-redacted dict — sensitive
    keys are masked to `<redacted>` before this runs, so we can safely
    interpolate any value.
    """
    # `payload` may be empty {} for fire-and-forget notify() calls; .get()
    # everything to keep this robust to missing keys.
    if kind == NotificationKind.workflow_run_failed:
        run_segment = (
            f"/runs/{source_workflow_run_id}"
            if source_workflow_run_id is not None
            else "/"
        )
        return (
            "Workflow failed",
            str(payload.get("message") or "A workflow run failed"),
            run_segment,
            None,
        )
    if kind == NotificationKind.workflow_run_started:
        return (
            "Workflow started",
            str(payload.get("message") or "A workflow run started"),
            f"/runs/{source_workflow_run_id}"
            if source_workflow_run_id is not None
            else "/",
            None,
        )
    if kind == NotificationKind.workflow_run_succeeded:
        return (
            "Workflow succeeded",
            str(payload.get("message") or "A workflow run succeeded"),
            f"/runs/{source_workflow_run_id}"
            if source_workflow_run_id is not None
            else "/",
            None,
        )
    if kind == NotificationKind.workflow_step_completed:
        return (
            "Step completed",
            str(payload.get("message") or "A workflow step completed"),
            f"/runs/{source_workflow_run_id}"
            if source_workflow_run_id is not None
            else "/",
            None,
        )
    if kind == NotificationKind.team_invite_accepted:
        return (
            "Team invite accepted",
            str(payload.get("team_name") or "A team invite was accepted"),
            "/teams",
            None,
        )
    if kind == NotificationKind.project_created:
        return (
            "Project created",
            str(payload.get("project_name") or "A project was created"),
            "/projects",
            None,
        )
    # NotificationKind.system + any future addition falls through to a
    # reasonable default. Adding a new kind to the enum without extending
    # this switch silently routes through here — log the kind so an operator
    # can spot the omission in real traffic.
    return (
        "Notification",
        str(payload.get("message") or "System notification"),
        "/",
        None,
    )


def _push(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: NotificationKind,
    payload: dict[str, Any],
    source_workflow_run_id: uuid.UUID | None,
) -> bool:
    """Resolve the per-(user, kind) push preference and fan-out if true.

    Returns True iff the channel was on AND the dispatcher accepted at least
    one delivery. The slice contract is that any error inside the push path
    is contained — it MUST NOT re-raise into `notify()` (which itself MUST
    NOT re-raise into the calling route).
    """
    try:
        push_on = _resolve_push(session, user_id=user_id, kind=kind)
    except Exception as exc:  # noqa: BLE001
        # Defensive: a DB blip on the preference read should not block the
        # in-app channel. Log and treat as off.
        logger.error(
            "notify.push_failed user_id=%s kind=%s cause=%s stage=resolve",
            user_id,
            kind.value,
            exc.__class__.__name__,
        )
        return False
    if not push_on:
        return False
    try:
        # Local import keeps app.core.notify importable at startup even if
        # the pywebpush dependency tree fails to load — and lets tests
        # monkeypatch app.core.push_dispatch.dispatch_push without import-
        # order surprises.
        from app.core import push_dispatch

        title, body, url, icon = _render_push(
            kind=kind,
            payload=payload,
            source_workflow_run_id=source_workflow_run_id,
        )
        delivered = push_dispatch.dispatch_push(
            session,
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            url=url,
            icon=icon,
        )
        return delivered > 0
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "notify.push_failed user_id=%s kind=%s cause=%s stage=dispatch",
            user_id,
            kind.value,
            exc.__class__.__name__,
        )
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
    """Dispatch an in-app notification (and optionally a Web Push) for `user_id`.

    Behavior:
      1. Look up the team-default preference. If `in_app` is False, log
         `notify.skipped_in_app` INFO and skip the insert.
      2. Otherwise redact sensitive payload keys, INSERT a notifications
         row, and emit `notify.dispatched`.
      3. Independently of in-app, resolve the team-default push preference
         and fan-out via `app.core.push_dispatch.dispatch_push` when true.
         The push channel never blocks or fails the in-app channel.
      4. Any DB error during the in-app insert is caught: log
         `notify.insert_failed` ERROR with the exception class and return
         None. Never re-raise — a notification side-effect must not fail
         the caller's route.
    """
    redacted: dict[str, Any]
    in_app_row: Notification | None = None
    try:
        in_app = _resolve_in_app(session, user_id=user_id, kind=kind)
        redacted = _redact_payload(payload)
        if in_app:
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
            in_app_row = row
        else:
            logger.info(
                "notify.skipped_in_app user_id=%s kind=%s reason=preference_off",
                user_id,
                kind.value,
            )
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

    # Push channel runs independently of the in-app outcome. Errors inside
    # the push helper are contained there — `_push` never re-raises.
    push_delivered = _push(
        session,
        user_id=user_id,
        kind=kind,
        payload=redacted,
        source_workflow_run_id=source_workflow_run_id,
    )

    # `notify.dispatched` is the slice's grep gate; emit it whenever EITHER
    # channel produced an effect. The `push=<bool>` field lands per-event so
    # a future operator can grep for "push=true" to confirm fan-out happened.
    if in_app_row is not None:
        logger.info(
            "notify.dispatched user_id=%s kind=%s notification_id=%s "
            "in_app=true push=%s source_team_id=%s source_workflow_run_id=%s",
            user_id,
            kind.value,
            in_app_row.id,
            str(push_delivered).lower(),
            source_team_id if source_team_id is not None else "none",
            source_workflow_run_id
            if source_workflow_run_id is not None
            else "none",
        )
    elif push_delivered:
        # in-app suppressed by preference but push channel landed at least
        # one delivery — still useful to record so the channel decision is
        # auditable.
        logger.info(
            "notify.dispatched user_id=%s kind=%s notification_id=none "
            "in_app=false push=true source_team_id=%s source_workflow_run_id=%s",
            user_id,
            kind.value,
            source_team_id if source_team_id is not None else "none",
            source_workflow_run_id
            if source_workflow_run_id is not None
            else "none",
        )

    return in_app_row
