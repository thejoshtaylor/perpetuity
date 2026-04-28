"""Web Push channel — public VAPID-key endpoint + (in T03) subscribe routes.

M005 / S03 / T01. The browser fetches the VAPID public key from
``GET /api/v1/push/vapid_public_key`` BEFORE any user is in scope (the
service worker registers a subscription with the upstream push service
using the public key as the application server identity). That's why this
endpoint is intentionally NOT auth-gated — it's the one route in the system
that has to be world-readable.

Subsequent task wiring:
  - T03 lands POST /push/subscribe + DELETE /push/subscribe.
  - T04 lands the dispatcher invoked from app.core.notify().

Logging discipline:
  INFO  push.vapid_public_key.served key_prefix=<first_4_b64>
  ERROR push.vapid_decrypt_failed key=vapid_private_key (reserved for future
        read-sites; T01 only exposes the public key, so the decrypt path
        cannot fire here yet).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep
from app.models import SystemSetting, VapidPublicKeyResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


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
