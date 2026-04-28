"""Web Push channel — public VAPID-key endpoint + subscribe routes.

M005 / S03 / T01 + T03. The browser fetches the VAPID public key from
``GET /api/v1/push/vapid_public_key`` BEFORE any user is in scope (the
service worker registers a subscription with the upstream push service
using the public key as the application server identity). That's why this
endpoint is intentionally NOT auth-gated — it's the one route in the system
that has to be world-readable.

T03 lands the per-user subscribe routes:
  - POST   /api/v1/push/subscribe    — register or upsert this device's subscription
  - DELETE /api/v1/push/subscribe    — drop this device's subscription
  - GET    /api/v1/push/subscriptions — list the caller's subscriptions (hash-only)

Logging discipline (slice observability contract):
  INFO  push.vapid_public_key.served key_prefix=<first_4_b64>
  INFO  push.subscribe user_id=<uuid> endpoint_hash=<sha256:8> ua=<truncated_or_unknown>
  INFO  push.subscribe.upsert user_id=<uuid> endpoint_hash=<sha256:8> existing=true
  INFO  push.unsubscribe user_id=<uuid> endpoint_hash=<sha256:8> deleted=<bool>
  ERROR push.vapid_decrypt_failed key=vapid_private_key (reserved for future
        read-sites; T01 only exposes the public key, so the decrypt path
        cannot fire here yet).

Redaction: every log line that mentions an endpoint uses the 8-hex-char
sha256 prefix only — the raw push URL is treated as a bearer-style secret.
The User-Agent header is captured into the row but never logged in full.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    PushSubscription,
    PushSubscriptionCreate,
    PushSubscriptionDelete,
    PushSubscriptionPublic,
    PushSubscriptionsList,
    SystemSetting,
    VapidPublicKeyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


# Hard cap on the User-Agent value we'll persist. The PushSubscription model
# enforces max_length=500 on the column; truncating here keeps a long UA
# from triggering a 422 at validation time on an otherwise-valid subscribe.
_UA_MAX_LEN = 500


def _endpoint_hash(endpoint: str) -> str:
    """8-hex-char sha256 prefix of an endpoint URL. Mirrors push_dispatch."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:8]


def _truncated_ua(request: Request) -> str | None:
    raw = request.headers.get("user-agent")
    if not raw:
        return None
    return raw[:_UA_MAX_LEN]


def _to_public(row: PushSubscription) -> PushSubscriptionPublic:
    return PushSubscriptionPublic(
        id=row.id,
        endpoint_hash=_endpoint_hash(row.endpoint),
        user_agent=row.user_agent,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


@router.get("/vapid_public_key", response_model=VapidPublicKeyResponse)
def get_vapid_public_key(session: SessionDep) -> VapidPublicKeyResponse:
    """Return the configured VAPID public key.

    503 when the operator has not yet generated a keypair — the frontend
    treats this as "push channel not configured" and surfaces the operator
    runbook to the user, rather than triggering a permission prompt that
    would dead-end at subscribe time.

    Intentionally unauthenticated: every browser fetches this before any
    user is in scope, and the value is part of the W3C Push API contract
    (it identifies *us* to the upstream push service, not the user).
    """
    row = session.get(SystemSetting, "vapid_public_key")
    if row is None or not row.has_value or row.value is None:
        # 503 (not 404) signals "service not configured" — matches the
        # M004/S01 fail-loud posture for missing-config sensitive paths.
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "vapid_public_key_not_configured",
                "remediation": (
                    "operator must generate a keypair via"
                    " POST /admin/settings/vapid_keys/generate"
                ),
            },
        )

    public_key = str(row.value)
    logger.info(
        "push.vapid_public_key.served key_prefix=%s",
        public_key[:4],
    )
    return VapidPublicKeyResponse(public_key=public_key)


@router.post(
    "/subscribe",
    response_model=PushSubscriptionPublic,
)
def subscribe(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    request: Request,
    response: Response,
    body: PushSubscriptionCreate,
) -> PushSubscriptionPublic:
    """Register or upsert the caller's Web Push subscription for this device.

    Idempotent on (user_id, endpoint): a re-subscribe from the same browser
    refreshes the keys + last_seen_at and resets ``consecutive_failures``.
    Two distinct endpoints for the same user produce two rows (phone +
    laptop). The User-Agent header is captured into the row (truncated to
    500 chars) but never appears in logs in full.
    """
    endpoint = body.endpoint
    endpoint_hash = _endpoint_hash(endpoint)
    keys = body.keys.model_dump()
    user_agent = _truncated_ua(request)

    existing = session.exec(
        select(PushSubscription).where(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == endpoint,
        )
    ).one_or_none()

    if existing is not None:
        existing.keys = keys
        existing.user_agent = user_agent
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.consecutive_failures = 0
        existing.last_status_code = None
        session.add(existing)
        session.commit()
        session.refresh(existing)
        logger.info(
            "push.subscribe.upsert user_id=%s endpoint_hash=%s existing=true",
            current_user.id,
            endpoint_hash,
        )
        # 200 OK on the upsert path — distinguishable from 201 first-insert.
        return _to_public(existing)

    row = PushSubscription(
        user_id=current_user.id,
        endpoint=endpoint,
        keys=keys,
        user_agent=user_agent,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    # The UA is logged as a single token — truncate to first whitespace so a
    # long UA doesn't blow up grep lines. ``unknown`` when the header is
    # absent (e.g. server-side test client without a UA header set).
    ua_token = (
        user_agent.split(" ", 1)[0] if user_agent else "unknown"
    )
    logger.info(
        "push.subscribe user_id=%s endpoint_hash=%s ua=%s",
        current_user.id,
        endpoint_hash,
        ua_token,
    )
    response.status_code = 201
    return _to_public(row)


@router.delete(
    "/subscribe",
    status_code=204,
    response_class=Response,
)
def unsubscribe(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    body: PushSubscriptionDelete,
) -> Response:
    """Drop the caller's subscription for the given endpoint.

    Idempotent: deleting an unknown endpoint returns 204 with
    ``deleted=false`` in the audit log. This keeps the client side simple —
    it can blindly DELETE on permission-revoke without worrying about
    whether the row existed.
    """
    endpoint = body.endpoint
    endpoint_hash = _endpoint_hash(endpoint)

    existing = session.exec(
        select(PushSubscription).where(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == endpoint,
        )
    ).one_or_none()

    if existing is None:
        logger.info(
            "push.unsubscribe user_id=%s endpoint_hash=%s deleted=false",
            current_user.id,
            endpoint_hash,
        )
        return Response(status_code=204)

    session.delete(existing)
    session.commit()
    logger.info(
        "push.unsubscribe user_id=%s endpoint_hash=%s deleted=true",
        current_user.id,
        endpoint_hash,
    )
    return Response(status_code=204)


@router.get(
    "/subscriptions",
    response_model=PushSubscriptionsList,
)
def list_subscriptions(
    *,
    session: SessionDep,
    current_user: CurrentUser,
) -> PushSubscriptionsList:
    """List the caller's push subscriptions (hash-only projection).

    Powers the "this device + N others subscribed" hint in the Notifications
    settings tab. Never returns the raw endpoint URL.
    """
    rows = list(
        session.exec(
            select(PushSubscription)
            .where(PushSubscription.user_id == current_user.id)
            .order_by(PushSubscription.created_at)
        ).all()
    )
    data = [_to_public(r) for r in rows]
    return PushSubscriptionsList(data=data, count=len(data))
