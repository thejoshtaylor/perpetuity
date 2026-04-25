"""WS frame protocol shared between orchestrator and backend (T04+T05, MEM097).

NOTE: This file is a verbatim copy of `orchestrator/orchestrator/protocol.py`
(T05 plan: "Copy ... for type sharing — short-term duplication is fine; a
later milestone can extract a shared package"). Keep both files in lock-step
until the shared-package extraction lands.

Frame shape locked at end of S01 — downstream slices MUST NOT change shape.

Server → client frames:
  - {type:"attach",   scrollback: <base64 utf-8 of buffer, 100KB cap>}
  - {type:"data",     bytes:      <base64 of raw exec stdout chunk>}
  - {type:"exit",     code:       <int>}                   (shell exited)
  - {type:"detach",   reason:     "client_close"|"orchestrator_shutdown"|...}
  - {type:"error",    code:       <str>, message: <str>}   (rare — protocol-level)

Client → server frames:
  - {type:"input",    bytes:      <base64 of bytes to write to stdin>}
  - {type:"resize",   cols: <int>, rows: <int>}

Encoding rules:
  - JSON-encoded UTF-8 over WS text frames.
  - `bytes` and `scrollback` payloads are base64-encoded raw bytes. This
    handles binary, locale, and ANSI escape sequences cleanly without
    UTF-8 corruption (the alternative — sending raw bytes as UTF-8 strings
    with `errors="replace"` — would silently mangle CSI sequences and
    multi-byte chars split across chunk boundaries). The ~33% size overhead
    is accepted; control frames are tiny and `data` chunks are typically
    < 1 KB anyway.
  - Unknown frame types are ignored by both sides (forward-compat hook).
  - Malformed JSON closes the WS with code 1003 (unsupported data).

This module is the single source of truth for the protocol. Backend imports
the same types in T05 — keeping them here avoids a long-lived "which side
defines the schema" coordination problem.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal, TypedDict

# ----- TypedDicts (the canonical schema) -----------------------------------


class AttachFrame(TypedDict):
    """Server → client: sent first on every successful attach.

    `scrollback` is base64 of the captured pane bytes (≤ 100 KB raw).
    Empty string when there's nothing to replay (fresh session).
    """

    type: Literal["attach"]
    scrollback: str


class DataFrame(TypedDict):
    """Server → client: a chunk of stdout bytes from the exec stream.

    `bytes` is base64 of the raw chunk. Multiple data frames per command
    are normal — the chunking matches whatever the kernel produced.
    """

    type: Literal["data"]
    bytes: str


class ExitFrame(TypedDict):
    """Server → client: the shell exited (or the exec stream EOF'd)."""

    type: Literal["exit"]
    code: int


class DetachFrame(TypedDict):
    """Server → client: orchestrator is letting go of this WS.

    Sent before close(1000) when the orchestrator initiated the disconnect
    (e.g. shutdown). The tmux session is intentionally left running.
    """

    type: Literal["detach"]
    reason: str


class ErrorFrame(TypedDict):
    """Server → client: a protocol-level error before normal close.

    Rare. Most errors map to a close code+reason instead.
    """

    type: Literal["error"]
    code: str
    message: str


class InputFrame(TypedDict):
    """Client → server: bytes to write to the exec stdin.

    `bytes` is base64 of the raw input. The orchestrator decodes and writes
    directly to the docker exec stdin — no interpretation, no buffering.
    """

    type: Literal["input"]
    bytes: str


class ResizeFrame(TypedDict):
    """Client → server: resize the tmux pane to (cols, rows).

    The orchestrator calls `tmux refresh-client -C cols,rows` on the named
    session; default tmux semantics (smallest attached client wins) apply.
    """

    type: Literal["resize"]
    cols: int
    rows: int


# ----- close codes (matches RFC 6455 + our extensions) ---------------------

CLOSE_NORMAL = 1000
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED_DATA = 1003          # malformed JSON frame
CLOSE_POLICY_VIOLATION = 1008          # auth fail / unknown session
CLOSE_INTERNAL_ERROR = 1011            # docker exec stream failure


# ----- close reason strings (machine-stable) -------------------------------

REASON_UNAUTHORIZED = "unauthorized"
REASON_SESSION_NOT_FOUND = "session_not_found"
REASON_EXEC_STREAM_ERROR = "docker_exec_stream_error"
REASON_MALFORMED_FRAME = "malformed_frame"
REASON_CLIENT_CLOSE = "client_close"
REASON_EXEC_EOF = "exec_eof"
REASON_ORCHESTRATOR_SHUTDOWN = "orchestrator_shutdown"


# ----- helpers -------------------------------------------------------------


def encode_bytes(raw: bytes) -> str:
    """Encode raw bytes for the `bytes`/`scrollback` field as base64 UTF-8.

    `base64.b64encode` returns bytes; we decode to ascii (always safe — base64
    output is ascii by construction) so the field round-trips through json.
    """
    return base64.b64encode(raw).decode("ascii")


def decode_bytes(field: str) -> bytes:
    """Inverse of `encode_bytes`. Raises ValueError on malformed input.

    Used by the orchestrator on every `input` frame and by the backend on
    every `data` frame in T05. Strict validation: trailing whitespace,
    URL-safe alphabet, and other base64 dialects all fail loudly.
    """
    return base64.b64decode(field, validate=True)


def make_attach(scrollback_raw: bytes) -> AttachFrame:
    return {"type": "attach", "scrollback": encode_bytes(scrollback_raw)}


def make_data(chunk: bytes) -> DataFrame:
    return {"type": "data", "bytes": encode_bytes(chunk)}


def make_exit(code: int) -> ExitFrame:
    return {"type": "exit", "code": int(code)}


def make_detach(reason: str) -> DetachFrame:
    return {"type": "detach", "reason": reason}


def encode_frame(frame: dict[str, Any]) -> str:
    """Serialize a frame to a JSON string for transmission over WS.

    Centralizes the json dumping so the wire format is consistent (no extra
    whitespace, deterministic separators).
    """
    return json.dumps(frame, separators=(",", ":"))


def decode_frame(text: str) -> dict[str, Any]:
    """Decode a client text frame into a dict.

    Returns the parsed dict on success. Raises `ValueError` on malformed
    JSON or non-object root — caller should map both to a close(1003).
    """
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("frame root must be a JSON object")
    return obj
