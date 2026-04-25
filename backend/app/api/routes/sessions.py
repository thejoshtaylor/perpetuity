"""Public session router (M002 / S01 / T05).

Endpoints:
  - `POST   /api/v1/sessions`                 — create a session for a team
  - `GET    /api/v1/sessions`                 — list caller's sessions
  - `DELETE /api/v1/sessions/{session_id}`    — kill a session the caller owns
  - `WS     /api/v1/ws/terminal/{session_id}` — proxy a terminal session

The HTTP routes are thin proxies to the orchestrator at `ORCHESTRATOR_BASE_URL`
authenticated by `ORCHESTRATOR_API_KEY` (D016, MEM096). The backend enforces
ownership before forwarding — orchestrator is trusted to obey the
shared-secret boundary but does not enforce per-user policy on its own (T03).

The WS route is a verbatim frame-level proxy between the browser and the
orchestrator's `WS /v1/sessions/{sid}/stream`. We do NOT decode or re-encode
JSON payloads — just forward text frames as-is so the protocol contract from
`app.api.ws_protocol` (locked at end of S01 per MEM097) stays a single
schema, not three subtly-different shapes.

Error handling is shaped to avoid existence enumeration:
  - DELETE on a session the caller doesn't own → 404 (same as a missing sid).
  - WS attach to a missing or unowned sid → close 1008 reason='session_not_owned'
    (identical close for both — see CONTEXT error-handling section).

Observability:
  INFO  session_proxy_open   user_id=<uuid> session_id=<uuid>
  INFO  session_proxy_close  session_id=<uuid> reason=<client|orch|exit> code=<int>
  WARN  orchestrator_unavailable url=<base>

Logs use only `current_user.id` UUID — never email or full_name (slice
observability rule).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, status
from httpx_ws import HTTPXWSException, aconnect_ws
from httpx_ws import WebSocketDisconnect as HXWSDisconnect
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.deps import CurrentUser, SessionDep, get_current_user_ws
from app.api.team_access import assert_caller_is_team_member
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


# ----- request/response models --------------------------------------------


# We keep response shaping in plain dicts for now — backend doesn't store
# sessions anywhere (Redis is the orchestrator's domain), so a SQLModel
# response_model would force a fake table. The integration tests in T05
# verify the wire-shape directly.


# ----- orchestrator client helpers ----------------------------------------

# Default per-call timeout. Generous because the orchestrator boots a
# container on first call (image pull + container create + tmux start can
# take a few seconds in cold cache). Connect timeout is short — if the
# orchestrator is down we want fast 503, not a hanging request.
_ORCH_TIMEOUT = httpx.Timeout(30.0, connect=3.0)


def _orch_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY}


def _orch_unavailable_503(detail: str = "orchestrator_unavailable") -> HTTPException:
    """Construct a 503 the same way every place we catch httpx errors does.

    Centralized so the response shape is consistent and the log line stays
    correlated to a single string the integration tests can grep for.
    """
    logger.warning(
        "orchestrator_unavailable url=%s detail=%s",
        settings.ORCHESTRATOR_BASE_URL,
        detail,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


async def _orch_get_session_record(session_id: str) -> dict[str, Any] | None:
    """Fetch a session record from orchestrator. Returns None on 404.

    The orchestrator's GET /v1/sessions endpoint is `?user_id=&team_id=` —
    it does not have a "by id" lookup. We list and filter rather than adding
    a new orchestrator endpoint just for ownership checks. The list size is
    bounded by per-user session count (small in practice), so this is fine
    until M003 grows the model.

    To avoid making the backend learn the (user, team) of a session before
    it knows whether it owns the session, we list ALL sessions visible to
    the caller (the WS path passes user_id only when it knows it from the
    cookie). For ownership-by-sid we rely on the dedicated lookup endpoint
    we add via a post-listing scan: the orchestrator stores user_id on the
    record, so a single lookup over the caller's own sessions is enough.
    """
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    url = f"{base}/v1/sessions/by-id/{session_id}"
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.get(url, headers=_orch_headers())
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        # Re-raise as a marker the caller can map to a WS close or HTTP 503.
        raise

    if r.status_code == 404:
        return None
    if r.status_code != 200:
        # Treat as transient orchestrator issue — caller decides whether to
        # 503 (HTTP path) or 1011 (WS path).
        raise httpx.HTTPStatusError(
            f"orchestrator returned {r.status_code} on {url}",
            request=r.request,
            response=r,
        )
    return r.json()


# ----- HTTP routes --------------------------------------------------------


@router.post("/sessions")
async def create_session(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create a fresh terminal session for `team_id` (caller must be a member).

    Request: `{"team_id": "<uuid>"}`
    Response: `{"session_id": "<uuid>", "team_id": "<uuid>", "created_at": "<iso>"}`

    Orchestrator unreachable → 503. Team not found → 404. Caller not a
    member of `team_id` → 403.
    """
    raw_team_id = body.get("team_id")
    if not isinstance(raw_team_id, str):
        raise HTTPException(status_code=422, detail="team_id is required")
    try:
        team_uuid = uuid.UUID(raw_team_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="team_id must be a UUID")

    # Membership guard. Raises 404/403 with the existing teams.py shape so
    # the response is indistinguishable from a teams endpoint reject — keeps
    # error fingerprinting hard for an attacker.
    assert_caller_is_team_member(session, team_uuid, current_user.id)

    session_id = uuid.uuid4()
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    payload = {
        "session_id": str(session_id),
        "user_id": str(current_user.id),
        "team_id": str(team_uuid),
    }
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.post(
                f"{base}/v1/sessions", json=payload, headers=_orch_headers()
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")

    if r.status_code >= 500:
        raise _orch_unavailable_503(
            f"orchestrator_status_{r.status_code}"
        )
    if r.status_code != 200:
        # Pass orchestrator validation errors through with a sanitized body.
        # We don't surface orchestrator internals — just the status code.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="orchestrator_rejected_create",
        )

    created_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "session_created session_id=%s user_id=%s team_id=%s",
        session_id,
        current_user.id,
        team_uuid,
    )
    return {
        "session_id": str(session_id),
        "team_id": str(team_uuid),
        "created_at": created_at,
    }


@router.get("/sessions")
async def list_sessions(
    *, current_user: CurrentUser, team_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """List sessions belonging to the caller.

    `team_id` query param is optional — if omitted the backend asks the
    orchestrator for all sessions belonging to the caller (across teams).
    Backend never trusts the orchestrator's record without verifying
    `record.user_id == current_user.id` after the fact.
    """
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    params: dict[str, str] = {"user_id": str(current_user.id)}
    if team_id is not None:
        params["team_id"] = str(team_id)
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.get(
                f"{base}/v1/sessions",
                params=params,
                headers=_orch_headers(),
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")

    if r.status_code != 200:
        raise _orch_unavailable_503(
            f"orchestrator_status_{r.status_code}"
        )
    records = r.json() if isinstance(r.json(), list) else []
    # Defense-in-depth: even though we passed user_id to orchestrator, drop
    # any rows whose user_id doesn't match the caller. The orchestrator is
    # trusted, but a router bug should never leak another user's session.
    filtered = [
        rec for rec in records if str(rec.get("user_id")) == str(current_user.id)
    ]
    return {"data": filtered, "count": len(filtered)}


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_session(
    *, current_user: CurrentUser, session_id: uuid.UUID
) -> dict[str, Any]:
    """Tear down a session the caller owns.

    Per the no-enumeration rule: a missing record AND a record owned by
    another user both return 404 with the same body. The caller cannot tell
    "doesn't exist" from "exists but isn't yours".
    """
    sid_str = str(session_id)
    try:
        record = await _orch_get_session_record(sid_str)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")
    except httpx.HTTPStatusError:
        raise _orch_unavailable_503("orchestrator_lookup_failed")

    if record is None or str(record.get("user_id")) != str(current_user.id):
        # Same shape regardless — no existence enumeration.
        raise HTTPException(status_code=404, detail="Session not found")

    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.delete(
                f"{base}/v1/sessions/{sid_str}", headers=_orch_headers()
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")

    if r.status_code != 200:
        raise _orch_unavailable_503(
            f"orchestrator_status_{r.status_code}"
        )
    logger.info(
        "session_deleted session_id=%s user_id=%s",
        session_id,
        current_user.id,
    )
    return {"session_id": sid_str, "deleted": True}


@router.get("/sessions/{session_id}/scrollback")
async def get_session_scrollback(
    *, current_user: CurrentUser, session_id: uuid.UUID
) -> dict[str, Any]:
    """Fetch the current tmux scrollback for a session the caller owns.

    Backend exposes this as GET (it is a read in the public API surface);
    the orchestrator endpoint stays POST per S01 plan to keep the locked
    frame-protocol stable. The asymmetry is intentional — the WS attach
    frame is the only place that base64-encodes scrollback; this proxy
    returns the raw UTF-8 string the orchestrator yielded.

    Per the no-enumeration rule (MEM113/MEM123): a missing record AND a
    record owned by another user both return 404 with an identical body.
    Orchestrator unreachable on either the lookup or the scrollback fetch
    surfaces as 503.
    """
    sid_str = str(session_id)
    try:
        record = await _orch_get_session_record(sid_str)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")
    except httpx.HTTPStatusError:
        raise _orch_unavailable_503("orchestrator_lookup_failed")

    if record is None or str(record.get("user_id")) != str(current_user.id):
        # No-enumeration: identical body for "missing" and "not yours".
        raise HTTPException(status_code=404, detail="Session not found")

    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.post(
                f"{base}/v1/sessions/{sid_str}/scrollback",
                headers=_orch_headers(),
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _orch_unavailable_503("orchestrator_unavailable")

    if r.status_code != 200:
        raise _orch_unavailable_503(
            f"orchestrator_status_{r.status_code}"
        )
    body = r.json()
    if not isinstance(body, dict) or "scrollback" not in body:
        # Schema drift on the orchestrator side — surface as 503 instead of
        # crashing with KeyError so the user sees a known error shape.
        raise _orch_unavailable_503("orchestrator_unavailable")
    scrollback = body["scrollback"]
    if not isinstance(scrollback, str):
        raise _orch_unavailable_503("orchestrator_unavailable")

    logger.info(
        "session_scrollback_proxied session_id=%s user_id=%s bytes=%d",
        session_id,
        current_user.id,
        len(scrollback.encode("utf-8")),
    )
    return {"session_id": sid_str, "scrollback": scrollback}


# ----- WebSocket bridge ---------------------------------------------------


@router.websocket("/ws/terminal/{session_id}")
async def ws_terminal(websocket: WebSocket, session_id: str) -> None:
    """Proxy a browser WS to the orchestrator's session-stream WS.

    Lifecycle:
      1. Cookie auth via `get_current_user_ws` (close-before-accept on fail —
         MEM081 / MEM022).
      2. Look up the session record on the orchestrator. Missing OR not
         owned → close 1008 reason='session_not_owned' (identical close —
         CONTEXT error-handling rule).
      3. Open the orchestrator-side WS with `?key=<API_KEY>`. Connect
         failure → close 1011 reason='orchestrator_unavailable'.
      4. Two pumps proxy text frames in both directions verbatim. Backend
         does NOT decode/re-encode JSON — the frame-protocol contract from
         `app.api.ws_protocol` lives at the endpoints, not in the middle.
      5. Either side closing tears down the other.
    """
    # ----- auth -----------------------------------------------------------
    try:
        user = await get_current_user_ws(websocket)
    except WebSocketDisconnect:
        # `get_current_user_ws` already closed with the right code/reason.
        return

    # ----- ownership check -----------------------------------------------
    try:
        record = await _orch_get_session_record(session_id)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        # Orchestrator unreachable on lookup is a 1011 — the connection
        # would have worked logically but the dependency is down. Distinct
        # from session_not_owned which is a policy-violation 1008.
        logger.warning(
            "orchestrator_unavailable url=%s phase=lookup",
            settings.ORCHESTRATOR_BASE_URL,
        )
        await websocket.close(code=1011, reason="orchestrator_unavailable")
        return
    except httpx.HTTPStatusError:
        # 5xx from orchestrator on lookup — also unavailable.
        await websocket.close(code=1011, reason="orchestrator_unavailable")
        return

    if record is None or str(record.get("user_id")) != str(user.id):
        # No-enumeration: identical close for "missing" and "not yours".
        # Pre-accept close yields a 403 on the upgrade; clients see code
        # 1008 / reason 'session_not_owned'.
        logger.info(
            "session_proxy_reject session_id=%s user_id=%s reason=session_not_owned",
            session_id,
            user.id,
        )
        await websocket.close(code=1008, reason="session_not_owned")
        return

    # ----- accept the browser WS -----------------------------------------
    await websocket.accept()
    logger.info(
        "session_proxy_open user_id=%s session_id=%s",
        user.id,
        session_id,
    )

    # ----- open the orchestrator-side WS ---------------------------------
    base_http = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    if base_http.startswith("https://"):
        ws_base = "wss://" + base_http[len("https://"):]
    else:
        # http:// or anything else falls through to ws://
        scheme = base_http.split("://", 1)
        ws_base = "ws://" + (scheme[1] if len(scheme) == 2 else base_http)
    orch_ws_url = (
        f"{ws_base}/v1/sessions/{session_id}/stream"
        f"?key={settings.ORCHESTRATOR_API_KEY}"
    )

    close_reason = "client"
    close_code = 1000
    try:
        async with aconnect_ws(orch_ws_url) as orch_ws:
            close_code, close_reason = await _proxy_frames(websocket, orch_ws)
    except (HTTPXWSException, httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Orchestrator unavailable on the WS upgrade. Note: this catches
        # the upgrade-failure path; once we're inside the `async with` the
        # _proxy_frames helper handles disconnect-mid-stream.
        logger.warning(
            "orchestrator_unavailable url=%s phase=ws_connect err=%s",
            settings.ORCHESTRATOR_BASE_URL,
            type(exc).__name__,
        )
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=1011, reason="orchestrator_unavailable")
        return
    finally:
        logger.info(
            "session_proxy_close session_id=%s reason=%s code=%s",
            session_id,
            close_reason,
            close_code,
        )

    # If we got here without raising, _proxy_frames already closed the
    # browser WS with the appropriate code (mapped from the orchestrator).


async def _proxy_frames(
    browser_ws: WebSocket,
    orch_ws: Any,
) -> tuple[int, str]:
    """Bidirectional verbatim text-frame proxy.

    Returns (close_code, close_reason_label) for observability. The label is
    one of {'client', 'orch', 'exit', 'error'} per the slice plan's
    Observability Impact section. The actual close code propagated to the
    browser is whatever the orchestrator sent (or 1011 on internal error).
    """
    import asyncio

    close_state: dict[str, Any] = {
        "code": 1000,
        "reason_label": "client",
        "orch_close_code": None,
        "orch_close_reason": None,
    }

    async def _pump_browser_to_orch() -> None:
        try:
            while True:
                text = await browser_ws.receive_text()
                await orch_ws.send_text(text)
        except WebSocketDisconnect:
            close_state["reason_label"] = "client"
        except (HXWSDisconnect, HTTPXWSException) as exc:
            # Orchestrator side closed mid-write — orchestrator pump will
            # carry the real close info.
            close_state["reason_label"] = "orch"
            close_state["orch_close_code"] = getattr(exc, "code", None)
            close_state["orch_close_reason"] = getattr(exc, "reason", None) or ""

    async def _pump_orch_to_browser() -> None:
        try:
            while True:
                text = await orch_ws.receive_text()
                if browser_ws.client_state != WebSocketState.CONNECTED:
                    return
                await browser_ws.send_text(text)
        except (HXWSDisconnect, HTTPXWSException) as exc:
            # Orchestrator told us we're done. Capture its close code/reason
            # so we can mirror them to the browser side.
            close_state["reason_label"] = "orch"
            close_state["orch_close_code"] = getattr(exc, "code", None)
            close_state["orch_close_reason"] = getattr(exc, "reason", None) or ""
        except WebSocketDisconnect:
            close_state["reason_label"] = "client"

    bp = asyncio.create_task(_pump_browser_to_orch(), name="browser-to-orch")
    op = asyncio.create_task(_pump_orch_to_browser(), name="orch-to-browser")
    done, pending = await asyncio.wait(
        {bp, op}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # Map orchestrator close to browser close.
    if close_state["reason_label"] == "client":
        # Browser left first — close orch from the context-manager exit and
        # avoid touching browser_ws (already gone from its side).
        return (1000, "client")

    # Reason label == 'orch' (or 'error'). Mirror the orchestrator's close
    # code+reason 1:1 onto the browser WS so the locked protocol contract
    # (close codes ARE the contract for terminal/auth/protocol errors)
    # surfaces unchanged.
    code = close_state.get("orch_close_code") or 1000
    reason = close_state.get("orch_close_reason") or ""
    if browser_ws.client_state != WebSocketState.DISCONNECTED:
        try:
            await browser_ws.close(code=int(code), reason=str(reason))
        except RuntimeError:
            pass
    close_state["code"] = int(code)
    return (int(code), close_state["reason_label"])
