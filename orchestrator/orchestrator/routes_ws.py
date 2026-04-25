"""WS bridge: bidirectional bytes between a WebSocket and a tmux exec stream (T04).

Single endpoint:
  - `WS /v1/sessions/{session_id}/stream?key=<shared-secret>`

Lifecycle:

  1. Authenticate the upgrade against the orchestrator shared secret
     (`?key=` query param, T02 pattern). Bad key → close 1008 'unauthorized'
     **before** accept.
  2. After accept, look up the session record in Redis. Missing → close 1008
     'session_not_found'.
  3. Capture the current tmux scrollback (≤ 100 KB hard cap, T03's
     `capture_scrollback`) and send `{type:"attach", scrollback: <b64>}`.
  4. Open a `docker exec` into the workspace container running
     `tmux attach-session -t <session_id>`, with stdin+stdout+stderr+tty.
     The exec stream becomes the live byte pipe.
  5. Spawn two pumps:
       - exec → WS: read raw bytes from the exec stream, frame as
         `{type:"data", bytes:<b64>}` and send_text.
       - WS → exec: receive_text loop; for each frame:
           - `input`  → write decoded bytes to exec stdin.
           - `resize` → call orchestrator `resize_tmux_session(...)`.
           - other    → log + ignore (forward-compat).
       Whichever pump finishes first triggers cancellation of the other.
  6. On exec EOF (shell exited): inspect exec for ExitCode, send
     `{type:"exit", code:<n>}`, close 1000.
  7. On WS client close: cancel both pumps but do NOT kill the tmux session —
     that's the entire point of the tmux-as-pty-owner choice (D012/MEM092).
     The tmux session keeps the shell alive for the next reconnect.
  8. Update Redis `last_activity` on every input frame (heartbeat the S04
     idle reaper depends on).

Failure modes:
  - aiodocker exec stream raises mid-stream → log WARNING
    `docker_exec_stream_error`, close 1011.
  - Malformed JSON client frame → log WARNING, close 1003.
  - Unknown client frame type → log WARNING, ignore.
  - Redis unreachable while reading session record → close 1011 with
    reason='redis_unreachable'. (We treat this as transient; client should
    retry with backoff.)

Locked frame protocol (see `protocol.py`) — JSON UTF-8, base64-encoded byte
fields. Locked at end of S01 per MEM097.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiodocker
from aiodocker.exceptions import DockerError
from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from orchestrator.auth import authenticate_websocket
from orchestrator.errors import RedisUnavailable
from orchestrator.protocol import (
    CLOSE_INTERNAL_ERROR,
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    CLOSE_UNSUPPORTED_DATA,
    REASON_CLIENT_CLOSE,
    REASON_EXEC_EOF,
    REASON_EXEC_STREAM_ERROR,
    REASON_MALFORMED_FRAME,
    REASON_SESSION_NOT_FOUND,
    decode_bytes,
    decode_frame,
    encode_frame,
    make_attach,
    make_data,
    make_exit,
)
from orchestrator.redis_client import get_registry
from orchestrator.sessions import (
    TmuxCommandFailed,
    capture_scrollback,
    resize_tmux_session,
)

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/sessions", tags=["ws"])


# Maximum size of a chunk read from the exec stream before flushing as a
# `data` frame. The aiodocker FlowControlDataQueue limit is 64 KB, which
# is plenty — we accept whatever the parser hands us per call.
# Documented here so future tuning has a single home.
_EXEC_READ_CHUNK_HINT = 64 * 1024  # informational only — used by no code path


@router.websocket("/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: str) -> None:
    """Bridge a WS to a `tmux attach-session -t <session_id>` exec stream.

    `session_id` is taken as a string (no UUID validation here — Redis
    lookup will yield None for malformed ids, which maps to
    'session_not_found'). Validation at the path-param level would force a
    422 body that WS clients can't read; the policy-violation close is the
    correct shape.
    """
    if not await authenticate_websocket(websocket):
        # `authenticate_websocket` already closed 1008 'unauthorized'.
        return

    await websocket.accept()

    # ----- look up the session record ------------------------------------
    try:
        registry = get_registry()
        record = await registry.get_session(session_id)
    except RedisUnavailable:
        logger.error(
            "orchestrator_ws_redis_unreachable session_id=%s", session_id
        )
        await _safe_close(
            websocket, code=CLOSE_INTERNAL_ERROR, reason="redis_unreachable"
        )
        return

    if record is None:
        logger.warning(
            "orchestrator_ws_session_not_found session_id=%s", session_id
        )
        await _safe_close(
            websocket, code=CLOSE_POLICY_VIOLATION, reason=REASON_SESSION_NOT_FOUND
        )
        return

    container_id = str(record["container_id"])
    docker: aiodocker.Docker | None = getattr(websocket.app.state, "docker", None)
    if docker is None:
        logger.error(
            "orchestrator_ws_no_docker_handle session_id=%s", session_id
        )
        await _safe_close(
            websocket, code=CLOSE_INTERNAL_ERROR, reason="docker_unavailable"
        )
        return

    # ----- send the attach frame ------------------------------------------
    try:
        scrollback = await capture_scrollback(docker, container_id, session_id)
    except TmuxCommandFailed as exc:
        # Treat as orphaned — degrade to empty scrollback rather than fail.
        # The slice observability taxonomy logs this at the route layer in
        # the HTTP path; mirror it here.
        logger.warning(
            "tmux_session_orphaned session_id=%s container_id=%s reason=%s",
            session_id,
            container_id[:12],
            exc.output.strip()[:120] if exc.output else "<no output>",
        )
        scrollback = ""

    attach_frame = make_attach(scrollback.encode("utf-8"))
    try:
        await websocket.send_text(encode_frame(attach_frame))
    except (WebSocketDisconnect, RuntimeError):
        # Client gone before we even attached. No teardown needed beyond
        # the WS itself, and starlette has already cleaned that up.
        return

    logger.info(
        "session_attached session_id=%s container_id=%s",
        session_id,
        container_id[:12],
    )

    # ----- open the exec stream ------------------------------------------
    # `tmux attach-session -t <sid>` takes over the pty. -d would detach
    # other clients first; we want cooperative attach so multiple WS panes
    # can share (R008). `bash` fallback if attach fails would be wrong —
    # the tmux session was created in T03 and absent only on programmer
    # error, which a 1011 close is the right signal for.
    detach_reason: str = REASON_CLIENT_CLOSE
    exit_code: int | None = None
    exec_inst: aiodocker.execs.Exec | None = None
    stream: Any | None = None
    try:
        container = await docker.containers.get(container_id)
        exec_inst = await container.exec(
            cmd=["tmux", "attach-session", "-t", session_id],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
        )
        stream = exec_inst.start(detach=False)
        # Enter the stream context to initialize the upgraded socket. The
        # __aenter__ does the HTTP-upgrade dance.
        await stream.__aenter__()
    except DockerError as exc:
        logger.error(
            "docker_exec_start_failed session_id=%s container_id=%s reason=%s",
            session_id,
            container_id[:12],
            f"{exc.status}:{exc.message}",
        )
        await _safe_close(
            websocket,
            code=CLOSE_INTERNAL_ERROR,
            reason=REASON_EXEC_STREAM_ERROR,
        )
        return
    except OSError as exc:
        logger.error(
            "docker_exec_start_failed session_id=%s container_id=%s reason=%s",
            session_id,
            container_id[:12],
            type(exc).__name__,
        )
        await _safe_close(
            websocket,
            code=CLOSE_INTERNAL_ERROR,
            reason=REASON_EXEC_STREAM_ERROR,
        )
        return

    assert stream is not None
    assert exec_inst is not None

    # ----- pump tasks ----------------------------------------------------
    # Two coroutines race: whichever finishes first cancels the other and
    # carries forward the close reason / exit code.
    pump_state: dict[str, Any] = {
        "exit_code": None,
        "exec_eof": False,
        "exec_error": None,
    }

    async def _pump_exec_to_ws() -> None:
        """Forward exec stdout chunks to the WS as `data` frames."""
        try:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    pump_state["exec_eof"] = True
                    return
                # `msg.data` is the raw bytes chunk. With tty=True there is
                # no stdout/stderr multiplexing — both arrive on stream 1.
                data_frame = make_data(bytes(msg.data))
                if websocket.client_state != WebSocketState.CONNECTED:
                    return
                await websocket.send_text(encode_frame(data_frame))
        except (WebSocketDisconnect, RuntimeError):
            # WS closed under us — let the WS pump's exception path drive
            # the close reason. We don't set exec_error here because the
            # exec stream is healthy; the client just left.
            return
        except (DockerError, OSError, asyncio.IncompleteReadError) as exc:
            pump_state["exec_error"] = f"{type(exc).__name__}:{exc}"
            logger.warning(
                "docker_exec_stream_error session_id=%s err=%s",
                session_id,
                type(exc).__name__,
            )
            return

    async def _pump_ws_to_exec() -> None:
        """Read client frames; route input/resize, ignore unknown."""
        nonlocal detach_reason
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    frame = decode_frame(text)
                except ValueError:
                    logger.warning(
                        "ws_malformed_frame session_id=%s", session_id
                    )
                    detach_reason = REASON_MALFORMED_FRAME
                    await _safe_close(
                        websocket,
                        code=CLOSE_UNSUPPORTED_DATA,
                        reason=REASON_MALFORMED_FRAME,
                    )
                    return

                ftype = frame.get("type")
                if ftype == "input":
                    raw_field = frame.get("bytes")
                    if not isinstance(raw_field, str):
                        logger.warning(
                            "ws_malformed_input session_id=%s", session_id
                        )
                        continue
                    try:
                        raw = decode_bytes(raw_field)
                    except ValueError:
                        logger.warning(
                            "ws_malformed_input_b64 session_id=%s", session_id
                        )
                        continue
                    try:
                        await stream.write_in(raw)
                    except (DockerError, OSError, RuntimeError) as exc:
                        pump_state["exec_error"] = (
                            f"{type(exc).__name__}:{exc}"
                        )
                        logger.warning(
                            "docker_exec_write_error session_id=%s err=%s",
                            session_id,
                            type(exc).__name__,
                        )
                        return
                    # Heartbeat: bump last_activity. Best-effort — a Redis
                    # blip is non-fatal here; the next input frame will try
                    # again. The reaper tolerates a missed heartbeat.
                    try:
                        await registry.update_last_activity(session_id)
                    except RedisUnavailable:
                        logger.warning(
                            "redis_unreachable op=heartbeat session_id=%s",
                            session_id,
                        )
                elif ftype == "resize":
                    cols = frame.get("cols")
                    rows = frame.get("rows")
                    if not (
                        isinstance(cols, int)
                        and isinstance(rows, int)
                        and 0 < cols <= 1000
                        and 0 < rows <= 1000
                    ):
                        logger.warning(
                            "ws_malformed_resize session_id=%s", session_id
                        )
                        continue
                    try:
                        await resize_tmux_session(
                            docker, container_id, session_id, cols, rows
                        )
                    except TmuxCommandFailed as exc:
                        # Resize failures are non-fatal; log and continue.
                        # If the underlying tmux session disappeared the
                        # exec pump will EOF and drive the normal close.
                        logger.warning(
                            "tmux_resize_failed session_id=%s reason=%s",
                            session_id,
                            exc.output.strip()[:120] if exc.output else "?",
                        )
                else:
                    # Unknown frame type — forward-compat: log, ignore.
                    logger.warning(
                        "ws_unknown_frame_type session_id=%s type=%r",
                        session_id,
                        ftype,
                    )
        except WebSocketDisconnect:
            detach_reason = REASON_CLIENT_CLOSE
            return

    exec_task = asyncio.create_task(_pump_exec_to_ws(), name="ws-exec-pump")
    ws_task = asyncio.create_task(_pump_ws_to_exec(), name="ws-input-pump")

    done, pending = await asyncio.wait(
        {exec_task, ws_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    # Drain cancelled tasks so we don't leak Task warnings on shutdown.
    for task in pending:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # ----- determine close reason ----------------------------------------
    if pump_state["exec_eof"]:
        # Shell exited (or tmux attach returned). Pull exit code via
        # inspect; aiodocker leaves it in info["ExitCode"] or 0.
        try:
            info = await exec_inst.inspect()
            exit_code = int(info.get("ExitCode") or 0)
        except (DockerError, OSError) as exc:
            logger.warning(
                "docker_exec_inspect_failed session_id=%s err=%s",
                session_id,
                type(exc).__name__,
            )
            exit_code = 0
        detach_reason = REASON_EXEC_EOF
        # Send the exit frame before closing; ignore disconnect errors.
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(encode_frame(make_exit(exit_code)))
        except (WebSocketDisconnect, RuntimeError):
            pass
        await _safe_close(websocket, code=CLOSE_NORMAL, reason=REASON_EXEC_EOF)
    elif pump_state["exec_error"] is not None:
        await _safe_close(
            websocket,
            code=CLOSE_INTERNAL_ERROR,
            reason=REASON_EXEC_STREAM_ERROR,
        )
    else:
        # Client disconnected (most common path). The tmux session stays
        # alive; only the exec stream gets closed.
        await _safe_close(websocket, code=CLOSE_NORMAL, reason=detach_reason)

    # ----- tear down the exec stream -------------------------------------
    # Always close the stream — it owns an upgraded TCP socket. We do NOT
    # kill the tmux session: that's D012's whole point.
    try:
        await stream.__aexit__(None, None, None)
    except Exception as exc:  # noqa: BLE001
        # Best-effort cleanup; log and continue.
        logger.warning(
            "docker_exec_stream_close_error session_id=%s err=%s",
            session_id,
            type(exc).__name__,
        )

    logger.info(
        "session_detached session_id=%s container_id=%s reason=%s exit_code=%s",
        session_id,
        container_id[:12],
        detach_reason,
        exit_code if exit_code is not None else "-",
    )


async def _safe_close(
    websocket: WebSocket, *, code: int, reason: str
) -> None:
    """Close the WS without raising if it's already closed.

    Starlette raises RuntimeError on close-after-close. We've layered enough
    paths into the WS lifecycle (auth fail, missing session, exec error,
    client disconnect) that double-close is plausible — guard once at the
    helper rather than try/except in every branch.
    """
    if websocket.client_state == WebSocketState.DISCONNECTED:
        return
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError:
        # Already closed under us — fine.
        return
