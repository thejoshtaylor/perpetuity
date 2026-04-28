"""GitHub webhook dispatch hook (M004/S05/T02 — stub for M005).

The receiver route in ``app.api.routes.github_webhooks`` calls
``dispatch_github_event`` AFTER it has HMAC-verified the request, parsed
the JSON body, and inserted the event row with idempotent ON CONFLICT
DO NOTHING semantics. M004 ships only the no-op stub: the dispatch hook
exists so the slice's wire shape is testable end-to-end (see the
``webhook_dispatched`` log line in the slice contract) and so M005 has a
single, stable import target to fill in.

Contract (the receiver route depends on this surface — DO NOT change
without bumping the slice plan):

  * Signature: ``dispatch_github_event(event_type: str, payload: dict) -> None``
  * Side effect today: emit one INFO ``webhook_dispatched`` log line and
    return ``None``. NO exception is raised — this is a stub, not a
    "not yet implemented" guard. Raising would force the receiver route
    to grow defensive try/except blocks just to swallow the exception
    until M005 lands, which is exactly the kind of churn the no-op
    avoids.
  * The route, not this function, is responsible for emitting the
    ``webhook_received`` and ``webhook_verified`` log lines. This
    function only owns the third leg of the contract:
    ``webhook_dispatched delivery_id=<id> event_type=<type>
    dispatch_status=<noop>``.

M005 marker — when the real dispatch fires (delivery to per-installation
workers, push-rule evaluation, etc.) the body of this function changes
and ``dispatch_status`` flips off ``"noop"``. The no-op log line is the
seam M005 will replace.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def dispatch_github_event(
    event_type: str, payload: dict, *, delivery_id: str | None = None
) -> None:
    """No-op GitHub webhook dispatch — emits the ``webhook_dispatched`` log.

    Args:
        event_type: GitHub's ``X-GitHub-Event`` value (e.g. ``"push"``,
            ``"pull_request"``, ``"ping"``).
        payload: Parsed webhook JSON body. Currently inspected for nothing
            beyond ensuring callers actually parsed before dispatch — the
            real M005 implementation will route on payload contents.
        delivery_id: GitHub's ``X-GitHub-Delivery`` value, threaded through
            for log-line correlation. Optional only because the route owns
            the call site and may pass ``None`` defensively; the receiver
            route always passes a non-None delivery_id today.

    Returns:
        None. Today this is a stub; M005 fills in real dispatch.

    Raises:
        Nothing. The receiver route relies on this not raising — the M004
        contract is "no-op". DO NOT raise NotImplementedError; M005 owns
        the body change, the route does NOT need a temporary catch.
    """
    # Touch payload to suppress a future linter warning about an unused
    # arg before M005 wires real consumption — the parameter is part of
    # the public contract and must remain even though today's body
    # ignores it.
    _ = payload

    logger.info(
        "webhook_dispatched delivery_id=%s event_type=%s dispatch_status=%s",
        delivery_id if delivery_id is not None else "NA",
        event_type,
        "noop",
    )
    # M005: replace the no-op above with the real dispatch (per-install
    # worker handoff, push-rule evaluation, etc.). The route relies on
    # this function returning None — keep that invariant when filling in.
    return None
