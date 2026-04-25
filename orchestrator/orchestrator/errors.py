"""Orchestrator domain exceptions.

Each exception maps to a specific HTTP status via the FastAPI exception
handler registered in `main.py`. WS endpoints translate to a `close(code,
reason)` instead of a status — see `routes_ws.py` (T04) for that side.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base for orchestrator-domain errors."""


class RedisUnavailable(OrchestratorError):
    """Redis is unreachable or returned a connection-class error.

    Per D013 there is no in-memory fallback — propagating this to the HTTP
    layer as 503 is the contract: callers retry or surface the outage. The
    WARNING `redis_unreachable` log line is emitted at the call site so we
    can correlate the outage to a request_id.
    """


class DockerUnavailable(OrchestratorError):
    """Docker daemon is unreachable. Maps to 503.

    Health endpoint also flips `image_present` to False on this signal.
    """


class ImagePullFailed(OrchestratorError):
    """Workspace image pull failed at boot. Boot blocker per D018.

    Raised from the lifespan startup; orchestrator process exits 1 when
    this fires.
    """


class Unauthorized(OrchestratorError):
    """Shared-secret mismatch on HTTP. Maps to 401."""


class VolumeProvisionFailed(OrchestratorError):
    """Loopback-ext4 volume provisioning failed.

    `step` pins the failing subprocess so the next agent can re-run that
    exact command by hand from inside the orchestrator container. `reason`
    is the first line of stderr (or "timeout" / "unparseable_output" for
    the well-known non-stderr cases) — never the full stderr, since
    `losetup -a` can leak neighbor volumes' uuid-keyed paths.

    Mapped to 500 by the exception handler T03 registers in `main.py`:
    `{detail: "volume_provision_failed", step, reason}`.
    """

    def __init__(self, reason: str, step: str) -> None:
        super().__init__(f"{step}:{reason}")
        self.reason = reason
        self.step = step
