"""Web Push fan-out dispatcher (M005 / S03 / T02).

`dispatch_push(session, *, user_id, kind, title, body, url, icon=None) -> int`
is the engine `notify()` calls when the resolved per-user preference for the
given kind has `push=True`. It SELECTs every PushSubscription row for the
user, encrypts + signs each delivery with the configured VAPID private key,
POSTs to the upstream endpoint via pywebpush, and self-prunes dead
subscriptions:

  - HTTP 410 from the upstream → DELETE the row (browser uninstalled / unsub).
  - 5 consecutive 5xx → DELETE the row (upstream is permanently rejecting).
  - 2xx → reset `consecutive_failures = 0`, bump `last_seen_at`, count toward
    the function's success return.

Redaction posture:
  - VAPID keys: server-side, decrypted from `system_settings.vapid_private_key`
    via `decrypt_setting`. The plaintext private key NEVER appears in logs.
  - Endpoint URLs: log lines that name an endpoint use `endpoint_hash` =
    sha256(endpoint).hexdigest()[:8] only. The raw URL is treated as a
    bearer-style secret on every log surface.

Failure modes:
  - VAPID keys missing or unreadable → return 0 deliveries, log
    `push.vapid_decrypt_failed key=vapid_private_key` ERROR. Caller path
    (`notify()`) is robust to a 0-delivery result.
  - Per-row `WebPushException` is contained: response.status_code is consulted
    to drive prune-vs-bump-failure-counter; non-WebPushException exceptions
    log `push.dispatch.send_failed` ERROR and do NOT prune (we can't tell
    whether the upstream rejected or our outbound transport blew up).
  - The function never re-raises — `notify()` callers must remain robust to
    the push channel being down (D025 fail-loud applies to operator-visible
    config errors, not to transient delivery failures).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from pywebpush import WebPushException, webpush
from sqlmodel import Session, select

from app.core.encryption import SystemSettingDecryptError, decrypt_setting
from app.models import (
    NotificationKind,
    PushSubscription,
    SystemSetting,
)

logger = logging.getLogger(__name__)


# Hardcoded `sub` claim email — RFC 8292 requires the VAPID JWT to carry a
# `sub` claim that identifies the application server. We don't surface
# operator emails at the system_settings level today; a generic mailbox
# reachable on the deployed domain is the contract.
_VAPID_SUB_CLAIM = "mailto:operator@perpetuity.invalid"

# Five consecutive 5xx responses prunes the subscription. A real client that
# is just briefly offline will recover well within five push attempts.
_MAX_CONSECUTIVE_FAILURES = 5

# RFC 8030 §5.2 — Time-To-Live in seconds. 1h is plenty for the slice's
# notify-of-failure semantics; longer TTLs increase upstream-storage load
# without buying us anything since the user-facing event is fresh.
_PUSH_TTL_SECONDS = 3600

# VAPID system_settings keys — duplicated as constants here rather than
# imported from app.api.routes.admin to avoid a route → core import edge.
_VAPID_PUBLIC_KEY = "vapid_public_key"
_VAPID_PRIVATE_KEY = "vapid_private_key"


def _endpoint_hash(endpoint: str) -> str:
    """Return the 8-hex-char sha256 prefix of a push endpoint URL.

    All log lines that mention an endpoint use this hash; the raw URL is
    treated as a bearer-style secret. 8 hex chars (32 bits) is plenty of
    entropy to distinguish a single user's two-or-three devices in the
    operator UI without leaking the URL itself.
    """
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:8]


def _load_vapid_private_key(session: Session) -> str | None:
    """Decrypt and return the configured VAPID private key, or None.

    Returns None and logs an ERROR when the row is missing, has no value, or
    fails to decrypt. The caller treats None as "push channel not
    configured" → return 0 deliveries.
    """
    row = session.get(SystemSetting, _VAPID_PRIVATE_KEY)
    if row is None or not row.has_value or row.value_encrypted is None:
        logger.error(
            "push.vapid_decrypt_failed key=%s reason=not_configured",
            _VAPID_PRIVATE_KEY,
        )
        return None
    try:
        return decrypt_setting(bytes(row.value_encrypted))
    except SystemSettingDecryptError:
        # The key on the encryption layer doesn't know which row it belongs
        # to; we own that mapping. Re-log with the key name attached so the
        # operator runbook entry can be matched directly.
        logger.error(
            "push.vapid_decrypt_failed key=%s",
            _VAPID_PRIVATE_KEY,
        )
        return None


def _build_payload(
    *,
    kind: NotificationKind,
    title: str,
    body: str,
    url: str,
    icon: str | None,
) -> bytes:
    """Render the JSON body the browser SW will receive on `push` event.

    RFC 8030 caps the encrypted payload at 4096 bytes; this shape stays well
    under it for every realistic title/body. The SW handler in S03/T05 reads
    `title`, `body`, `url`, `kind`, and (optional) `icon`.
    """
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "url": url,
        "kind": kind.value,
    }
    if icon is not None:
        payload["icon"] = icon
    return json.dumps(payload).encode("utf-8")


def dispatch_push(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: NotificationKind,
    title: str,
    body: str,
    url: str,
    icon: str | None = None,
) -> int:
    """Fan-out a Web Push notification to every subscription for `user_id`.

    Returns the count of deliveries the upstream accepted (2xx). Failed
    deliveries are bookkept in-place: 410 prunes immediately; 5xx bumps a
    per-row counter and prunes at 5 consecutive failures; other exceptions
    log ERROR but never prune (we can't disambiguate transport from
    upstream).

    All per-row state changes share a single `session.commit()` at the end so
    the on-disk snapshot is consistent (e.g. counter bumps for two
    sibling devices land together, not interleaved with the next dispatch's
    reads).
    """
    private_key = _load_vapid_private_key(session)
    if private_key is None:
        return 0

    rows = list(
        session.exec(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id
            )
        ).all()
    )

    logger.info(
        "push.dispatch.start user_id=%s kind=%s subscriptions=%s",
        user_id,
        kind.value,
        len(rows),
    )

    if not rows:
        return 0

    payload = _build_payload(
        kind=kind, title=title, body=body, url=url, icon=icon
    )
    vapid_claims = {"sub": _VAPID_SUB_CLAIM}

    delivered = 0
    rows_to_delete: list[PushSubscription] = []

    for row in rows:
        endpoint_hash = _endpoint_hash(row.endpoint)
        subscription_info = {
            "endpoint": row.endpoint,
            "keys": dict(row.keys),
        }
        try:
            response = webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
                ttl=_PUSH_TTL_SECONDS,
            )
            status_code = getattr(response, "status_code", None)
        except WebPushException as exc:
            status_code = (
                exc.response.status_code
                if exc.response is not None
                else None
            )
            if status_code == 410:
                rows_to_delete.append(row)
                logger.info(
                    "push.dispatch.pruned_410 user_id=%s endpoint_hash=%s",
                    user_id,
                    endpoint_hash,
                )
                continue
            if status_code is not None and 500 <= status_code < 600:
                row.consecutive_failures = (
                    row.consecutive_failures or 0
                ) + 1
                row.last_status_code = status_code
                if row.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    rows_to_delete.append(row)
                    logger.warning(
                        "push.dispatch.pruned_max_failures user_id=%s"
                        " endpoint_hash=%s",
                        user_id,
                        endpoint_hash,
                    )
                else:
                    logger.warning(
                        "push.dispatch.consecutive_failure user_id=%s"
                        " endpoint_hash=%s count=%s",
                        user_id,
                        endpoint_hash,
                        row.consecutive_failures,
                    )
                session.add(row)
                continue
            # Non-410, non-5xx WebPushException — log + record status, do
            # NOT prune (could be 4xx malformed-payload, network jitter,
            # etc.). The operator can decide via the inspection paths.
            row.last_status_code = status_code or 0
            session.add(row)
            logger.error(
                "push.dispatch.send_failed user_id=%s endpoint_hash=%s"
                " cause=%s status_code=%s",
                user_id,
                endpoint_hash,
                exc.__class__.__name__,
                status_code,
            )
            continue
        except Exception as exc:  # noqa: BLE001 — slice contract: never re-raise
            logger.error(
                "push.dispatch.send_failed user_id=%s endpoint_hash=%s"
                " cause=%s",
                user_id,
                endpoint_hash,
                exc.__class__.__name__,
            )
            continue

        # 2xx happy path. requests.Response.status_code is always set when
        # webpush() returns without raising; default to 201 only if the mock
        # in tests didn't supply one.
        status = status_code or 201
        row.consecutive_failures = 0
        row.last_status_code = status
        # last_seen_at lives on the model; use UTC-aware now() so the
        # column's TIMESTAMPTZ semantics line up with the rest of the
        # codebase.
        from app.models import get_datetime_utc

        row.last_seen_at = get_datetime_utc()
        session.add(row)
        delivered += 1
        logger.info(
            "push.dispatch.delivered user_id=%s endpoint_hash=%s kind=%s"
            " status=%s",
            user_id,
            endpoint_hash,
            kind.value,
            status,
        )

    for row in rows_to_delete:
        session.delete(row)

    session.commit()
    return delivered
