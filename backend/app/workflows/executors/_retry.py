"""Shared orchestrator-exec retry helper for all step executors.

`_orchestrator_exec_with_retry(client_factory, url, body, headers)` posts
to the orchestrator's `/v1/sessions/{sid}/exec` endpoint with 3-attempt
exponential backoff (0.5s → 1s → 2s) on transport errors and 5xx
responses. 4xx responses and 504 (orchestrator-side timeout) bypass retry
because they indicate a non-transient condition.

Return value: the httpx.Response on success.
Raises:       `OrchestratorExecFailed` after all retries are exhausted or on
              the first non-retryable failure, carrying `error_class` and
              `stderr_hint` for the caller to stamp on the step_run row.

Observability: emits one INFO log per retry attempt with the discriminator
`orchestrator_exec_retry` so the psql drilldown in the slice plan works.

Secret discipline: the request body may carry form values + prior step
stdout (via {prev.stdout} substitution). The body is NEVER logged. Only
the HTTP status code and exception class name appear in logs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger("app.workflows.executors.retry")

# Backoff delays for attempt 1, 2, 3 (index = attempt number, 0-based).
_BACKOFF_SECONDS = [0.5, 1.0, 2.0]
_MAX_ATTEMPTS = 3

# 4xx and 504 are non-retryable: the request was malformed or timed out
# at the orchestrator level — retrying won't help.
_NON_RETRYABLE_STATUSES = frozenset(range(400, 500)) | {504}


class OrchestratorExecFailed(Exception):
    """Raised when the orchestrator call fails after all retries.

    Carries the attributes the executor needs to stamp on the step_run.
    """

    def __init__(self, error_class: str, stderr_hint: str) -> None:
        self.error_class = error_class
        self.stderr_hint = stderr_hint
        super().__init__(f"{error_class}: {stderr_hint}")


def _orchestrator_exec_with_retry(
    client_factory: Callable[[], Any],
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    run_id: Any,
    step_index: int,
) -> httpx.Response:
    """POST `url` with retry.  Returns the 200 response on success.

    Args:
        client_factory: Zero-arg callable returning an httpx.Client-like
                        context-manager.  Injected so tests can swap it.
        url:            Full orchestrator exec URL including session id.
        body:           JSON body (may contain rendered cmd + env).  NOT logged.
        headers:        HTTP headers (X-Orchestrator-Key etc.).
        run_id:         For log correlation — NOT the secret.
        step_index:     For log correlation.

    Raises:
        OrchestratorExecFailed on permanent failure.
    """
    last_error_class = "orchestrator_exec_failed"
    last_stderr = "unknown"

    for attempt in range(_MAX_ATTEMPTS):
        try:
            with client_factory() as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            last_error_class = "orchestrator_exec_failed"
            last_stderr = type(exc).__name__
            if attempt < _MAX_ATTEMPTS - 1:
                delay = _BACKOFF_SECONDS[attempt]
                logger.info(
                    "orchestrator_exec_retry run_id=%s step_index=%s attempt=%d/%d error_class=%s",
                    run_id,
                    step_index,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    last_stderr,
                )
                time.sleep(delay)
            continue

        if response.status_code == 200:
            return response

        last_stderr = f"orchestrator_status_{response.status_code}"

        if response.status_code in _NON_RETRYABLE_STATUSES:
            # 4xx / 504 — permanent. Don't retry.
            raise OrchestratorExecFailed(
                error_class="orchestrator_exec_failed",
                stderr_hint=last_stderr,
            )

        # 5xx (except 504) — transient, retry.
        last_error_class = "orchestrator_exec_failed"
        if attempt < _MAX_ATTEMPTS - 1:
            delay = _BACKOFF_SECONDS[attempt]
            logger.info(
                "orchestrator_exec_retry run_id=%s step_index=%s attempt=%d/%d error_class=%s",
                run_id,
                step_index,
                attempt + 1,
                _MAX_ATTEMPTS,
                last_stderr,
            )
            time.sleep(delay)

    # All attempts exhausted.
    raise OrchestratorExecFailed(
        error_class="orchestrator_exec_failed_after_retries",
        stderr_hint=last_stderr,
    )
