"""GitHub webhook receiver (M004 / S05 / T02).

Single endpoint:

  - ``POST /api/v1/github/webhooks`` — public.

GitHub posts here for every install bound to the App. The route:

  1. Reads the raw request body BEFORE any JSON parsing — the HMAC must
     be computed over the exact bytes GitHub signed. ``request.body()``
     is the ASGI primitive; ``request.json()`` would re-encode and
     break verification on payloads that include any whitespace GitHub
     does not.
  2. Loads the ``github_app_webhook_secret`` system_settings row, decrypts
     it via ``decrypt_setting`` (Fernet). Decrypt failures bubble out as
     ``SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY)`` —
     the GLOBAL handler in ``app.main`` translates that to 503 + the
     ``system_settings_decrypt_failed`` ERROR log. The receiver MUST NOT
     catch this; the contract from S01 is single-handler decrypt failure.
  3. Computes ``hmac.new(secret, body, sha256).hexdigest()``, compares
     against the ``X-Hub-Signature-256`` header (``sha256=<hex>``) using
     ``hmac.compare_digest`` (constant time).
  4. On HMAC pass: parses the body as JSON, then
     ``INSERT ... ON CONFLICT (delivery_id) DO NOTHING`` into
     ``github_webhook_events``. If a row was actually inserted (rowcount
     == 1), invokes ``dispatch_github_event(event_type, payload)`` and
     emits the three INFO log lines (``webhook_received``,
     ``webhook_verified``, ``webhook_dispatched`` — the last comes from
     the dispatch helper itself). If the insert was a duplicate (GitHub's
     24h retry per D025), emits ``webhook_duplicate_delivery`` and skips
     dispatch; the response is still 200 — duplicates are idempotent at
     the API boundary, not errors.
  5. On HMAC fail: inserts a ``webhook_rejections`` audit row (delivery
     id, signature_present, signature_valid=false, source IP) and returns
     401 ``invalid_signature``. The body is NOT persisted on rejection —
     a bad-signature payload is untrusted and could be a probe.
  6. On absent ``X-Hub-Signature-256`` header: same as HMAC fail, but the
     audit row records ``signature_present=false``.
  7. On unconfigured secret (no row, or ``has_value`` is false): 503
     ``webhook_secret_not_configured`` + WARNING log. NOT a rejection —
     this is operator misconfiguration, not a bad-actor probe; persisting
     it would pollute the rejection audit trail.
  8. On valid signature + invalid JSON body: 400 ``invalid_json`` and
     no event row. The HMAC verified — the request is genuine — but the
     body is unusable, so we can't persist it as an event. Logged as
     INFO ``webhook_received`` then WARNING ``webhook_invalid_payload``
     (the HMAC step proved the request is signed; the JSON failure
     happens after).

Logging contract (slice S05):
    INFO  webhook_received delivery_id=<id> event_type=<type> source_ip=<ip>
    INFO  webhook_verified delivery_id=<id> event_type=<type>
    INFO  webhook_dispatched delivery_id=<id> event_type=<type>
          dispatch_status=noop  (emitted by app.services.dispatch)
    INFO  webhook_duplicate_delivery delivery_id=<id>
    WARN  webhook_signature_invalid delivery_id=<id|NA> source_ip=<ip>
          signature_present=<bool>
    WARN  webhook_secret_not_configured

The plaintext webhook secret NEVER appears in any log line or exception
message — it is read into a local, used for the HMAC, and discarded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep
from app.api.routes.admin import GITHUB_APP_WEBHOOK_SECRET_KEY
from app.core.encryption import SystemSettingDecryptError, decrypt_setting
from app.models import SystemSetting
from app.services.dispatch import dispatch_github_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github"])


# Header names. GitHub sends them as case-insensitive HTTP headers; FastAPI
# normalizes to lower-case on the Request.headers mapping but we keep the
# canonical capitalization here for readability.
_HDR_SIG = "X-Hub-Signature-256"
_HDR_EVENT = "X-GitHub-Event"
_HDR_DELIVERY = "X-GitHub-Delivery"
_HDR_INSTALL = "X-GitHub-Hook-Installation-Target-Id"

_SIG_PREFIX = "sha256="


def _source_ip(request: Request) -> str:
    """Best-effort source IP for the audit trail.

    ``request.client`` can be None when the ASGI server omits client info
    (TestClient + raw ASGI scopes occasionally do). The audit row's
    ``source_ip`` column is NOT NULL, so we fall back to ``"unknown"``
    rather than letting the INSERT fail and lose the rejection log.
    """
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def _parse_install_id(raw: str | None) -> int | None:
    """Coerce ``X-GitHub-Hook-Installation-Target-Id`` to int, tolerating noise.

    Returns None if the header is absent, non-numeric, or out of range.
    The FK on ``github_webhook_events.installation_id`` is nullable
    (``ON DELETE SET NULL`` from T01), so a missing/garbage header is
    persisted as NULL rather than blowing up the insert.
    """
    if raw is None:
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v


def _verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification of GitHub's signature header.

    Returns False on any of: missing header, malformed header (not
    ``sha256=<hex>``), or digest mismatch. Uses ``hmac.compare_digest`` so
    the comparison is timing-safe.
    """
    if header is None:
        return False
    if not header.startswith(_SIG_PREFIX):
        return False
    presented = header[len(_SIG_PREFIX) :]
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    # compare_digest tolerates equal-length inequality without leaking
    # which byte differed; it raises TypeError on bytes/str mismatch so
    # we keep both sides as str.
    return hmac.compare_digest(presented, expected)


def _insert_rejection(
    session: SessionDep,
    *,
    delivery_id: str | None,
    signature_present: bool,
    signature_valid: bool,
    source_ip: str,
) -> None:
    """Persist a rejection audit row.

    Body is intentionally NOT stored — a bad-signature payload is
    untrusted and may be a probe; logging only the metadata keeps the
    audit trail useful without giving an attacker a write surface for
    persisted bytes.
    """
    session.execute(
        text(
            """
            INSERT INTO webhook_rejections
                (id, delivery_id, signature_present, signature_valid,
                 source_ip, received_at)
            VALUES
                (:id, :did, :sp, :sv, :ip, NOW())
            """
        ),
        {
            "id": uuid.uuid4(),
            "did": delivery_id,
            "sp": signature_present,
            "sv": signature_valid,
            "ip": source_ip,
        },
    )
    session.commit()


def _insert_event(
    session: SessionDep,
    *,
    installation_id: int | None,
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
) -> bool:
    """INSERT ON CONFLICT DO NOTHING into github_webhook_events.

    Returns True if a row was actually inserted, False on duplicate
    delivery. The UNIQUE constraint on ``delivery_id`` (T01) is the
    storage-layer enforcement of GitHub's 24h-retry idempotency contract
    (D025 / MEM229). RETURNING is the cleanest way to detect a duplicate
    here — ``rowcount`` is unreliable across drivers; ``RETURNING id``
    yields one row on insert and zero rows on conflict.
    """
    stmt = text(
        """
        INSERT INTO github_webhook_events
            (id, installation_id, event_type, delivery_id, payload,
             received_at, dispatch_status)
        VALUES
            (:id,
             (SELECT installation_id FROM github_app_installations
              WHERE installation_id = :iid),
             :etype, :did, CAST(:payload AS JSONB),
             NOW(), 'noop')
        ON CONFLICT (delivery_id) DO NOTHING
        RETURNING id
        """
    )
    result = session.execute(
        stmt,
        {
            "id": uuid.uuid4(),
            "iid": installation_id,
            "etype": event_type,
            "did": delivery_id,
            "payload": json.dumps(payload),
        },
    )
    inserted = result.first() is not None
    session.commit()
    return inserted


@router.post("/github/webhooks")
async def receive_github_webhook(
    *,
    session: SessionDep,
    request: Request,
) -> Any:
    """Receive a GitHub webhook delivery.

    Returns 200 on accepted (verified) deliveries — including duplicates
    of a previously-accepted delivery_id, since GitHub will retry for 24h
    and we MUST be idempotent. Returns 401 on bad/missing signature, 400
    on valid-signature-but-malformed-JSON, and 503 when the secret is
    unconfigured. Decrypt failure on the secret raises
    ``SystemSettingDecryptError`` → 503 via the global handler in
    ``app.main`` (see the S01 contract). All structured logs follow the
    slice's logging contract — see this module's docstring.
    """
    # 1. Raw body BEFORE any JSON parsing — HMAC is over the exact bytes.
    body = await request.body()

    # 2. Headers we care about. FastAPI lower-cases the Request.headers
    #    mapping but is case-insensitive on lookup, so the canonical
    #    capitalization works.
    sig_header = request.headers.get(_HDR_SIG)
    event_type = request.headers.get(_HDR_EVENT) or "unknown"
    delivery_id = request.headers.get(_HDR_DELIVERY)
    install_target = _parse_install_id(request.headers.get(_HDR_INSTALL))
    source_ip = _source_ip(request)

    # 3. Load the webhook secret. Unconfigured → 503; decrypt failure
    #    bubbles out and the global handler logs + returns 503.
    secret_row = session.get(SystemSetting, GITHUB_APP_WEBHOOK_SECRET_KEY)
    if (
        secret_row is None
        or not secret_row.has_value
        or secret_row.value_encrypted is None
    ):
        logger.warning("webhook_secret_not_configured")
        return JSONResponse(
            status_code=503,
            content={"detail": "webhook_secret_not_configured"},
        )

    try:
        secret_plain = decrypt_setting(bytes(secret_row.value_encrypted))
    except SystemSettingDecryptError as exc:
        # Re-raise with the row key attached so the global handler can
        # log it. The receiver MUST NOT catch this beyond the re-raise.
        # (S01 contract: decrypt sites raise; never catch.)
        raise SystemSettingDecryptError(
            key=GITHUB_APP_WEBHOOK_SECRET_KEY
        ) from exc

    # 4. Signature verification. Header absent → signature_present=false
    #    audit row, otherwise signature_present=true with signature_valid
    #    reflecting the digest comparison.
    signature_present = sig_header is not None
    signature_valid = _verify_signature(secret_plain, body, sig_header)
    # secret_plain stays in scope only for the line above; we deliberately
    # do not log it, return it, or hand it to anything else.

    if not signature_valid:
        _insert_rejection(
            session,
            delivery_id=delivery_id,
            signature_present=signature_present,
            signature_valid=False,
            source_ip=source_ip,
        )
        logger.warning(
            "webhook_signature_invalid delivery_id=%s source_ip=%s"
            " signature_present=%s",
            delivery_id if delivery_id is not None else "NA",
            source_ip,
            "true" if signature_present else "false",
        )
        return JSONResponse(
            status_code=401, content={"detail": "invalid_signature"}
        )

    # 5. Signature is good — log webhook_received now (NOT before, so an
    #    unauthenticated probe doesn't get logged with delivery_id and
    #    event_type from header injection).
    logger.info(
        "webhook_received delivery_id=%s event_type=%s source_ip=%s",
        delivery_id if delivery_id is not None else "NA",
        event_type,
        source_ip,
    )

    # 6. Parse JSON. Body must be JSON for any GitHub event we care about;
    #    a malformed body on a verified request is a contract break we
    #    surface as 400 rather than persisting.
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        logger.warning(
            "webhook_invalid_payload delivery_id=%s event_type=%s",
            delivery_id if delivery_id is not None else "NA",
            event_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        )
    if not isinstance(payload, dict):
        logger.warning(
            "webhook_invalid_payload delivery_id=%s event_type=%s",
            delivery_id if delivery_id is not None else "NA",
            event_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        )

    # 7. Insert event. Duplicate delivery_id is idempotent (UNIQUE → DO
    #    NOTHING). delivery_id is required for the insert (the column is
    #    NOT NULL); if GitHub sent no header we generate a synthetic id
    #    so the insert path doesn't trip on NULL — but this is a
    #    degenerate case we still log as duplicate-safe.
    if delivery_id is None:
        # GitHub always sends X-GitHub-Delivery, so this branch is
        # essentially dead in production; the test suite still exercises
        # absent-signature with absent-delivery to confirm the rejection
        # path. By the time we get here, signature_valid was true — so
        # we have a verified request without a delivery id, which is
        # outside the contract. Treat it as 400 rather than synthesizing
        # an ID that breaks idempotency.
        logger.warning(
            "webhook_invalid_payload delivery_id=NA event_type=%s",
            event_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_delivery_id",
        )

    inserted = _insert_event(
        session,
        installation_id=install_target,
        event_type=event_type,
        delivery_id=delivery_id,
        payload=payload,
    )

    if not inserted:
        # Duplicate of a delivery we already accepted — idempotent path,
        # do NOT redispatch.
        logger.info(
            "webhook_duplicate_delivery delivery_id=%s",
            delivery_id,
        )
        return {"status": "ok", "duplicate": True}

    # 8. Verified + new — emit webhook_verified before dispatch so the
    #    dispatch log line follows in the contract order.
    logger.info(
        "webhook_verified delivery_id=%s event_type=%s",
        delivery_id,
        event_type,
    )
    await dispatch_github_event(
        event_type, payload, delivery_id=delivery_id, session=session
    )

    return {"status": "ok", "duplicate": False}
