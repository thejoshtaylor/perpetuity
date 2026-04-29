"""Unit tests for `app.workflows.executors._retry._orchestrator_exec_with_retry`.

Covers:
  * Happy path — first attempt succeeds, returns response.
  * Transport error retried 3x then succeeds — returns response on attempt 3.
  * Transport error exhausted (3 attempts all fail) →
    raises OrchestratorExecFailed with error_class='orchestrator_exec_failed_after_retries'.
  * 5xx response retried 3x then succeeds.
  * 5xx exhausted → OrchestratorExecFailed after_retries.
  * 4xx (non-retryable) → OrchestratorExecFailed on first attempt, no retry.
  * 504 (non-retryable) → OrchestratorExecFailed on first attempt, no retry.
  * Retry log lines emit `orchestrator_exec_retry` discriminator with correct attempt count.
  * Backoff sleep is called between retries (mocked so tests are fast).
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from app.workflows.executors._retry import (
    OrchestratorExecFailed,
    _orchestrator_exec_with_retry,
)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _make_client_factory(*responses: "_FakeResponse | Exception") -> tuple[Any, list]:
    """Return a client_factory callable that yields scripted responses in order.

    Also returns a list that accumulates each `post` call for inspection.
    """
    calls: list[dict] = []
    response_iter = iter(responses)

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

        def post(self, url: str, *, json: Any = None, headers: Any = None) -> "_FakeResponse":
            calls.append({"url": url, "json": json, "headers": headers})
            resp = next(response_iter)
            if isinstance(resp, Exception):
                raise resp
            return resp

    def _factory() -> _FakeClient:
        return _FakeClient()

    return _factory, calls


_BASE_ARGS = dict(url="http://orch/exec", body={}, headers={}, run_id="run-1", step_index=0)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_first_attempt_succeeds() -> None:
    factory, calls = _make_client_factory(_FakeResponse(200))
    with patch("app.workflows.executors._retry.time.sleep"):
        result = _orchestrator_exec_with_retry(factory, **_BASE_ARGS)
    assert result.status_code == 200
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Transport error retries
# ---------------------------------------------------------------------------


def test_transport_error_retried_succeeds_on_third_attempt(caplog: pytest.LogCaptureFixture) -> None:
    """Two ConnectErrors then success → returns response, 2 retry log lines."""
    factory, calls = _make_client_factory(
        httpx.ConnectError("a"),
        httpx.ConnectError("b"),
        _FakeResponse(200),
    )
    with patch("app.workflows.executors._retry.time.sleep") as mock_sleep:
        with caplog.at_level(logging.INFO, logger="app.workflows.executors.retry"):
            result = _orchestrator_exec_with_retry(factory, **_BASE_ARGS)

    assert result.status_code == 200
    assert len(calls) == 3
    # Two retries → two sleep calls.
    assert mock_sleep.call_count == 2
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert log_text.count("orchestrator_exec_retry") == 2


def test_transport_error_exhausted_raises_after_retries(caplog: pytest.LogCaptureFixture) -> None:
    """Three ConnectErrors → OrchestratorExecFailed with after_retries discriminator."""
    factory, calls = _make_client_factory(
        httpx.ConnectError("a"),
        httpx.ConnectError("b"),
        httpx.ConnectError("c"),
    )
    with patch("app.workflows.executors._retry.time.sleep"):
        with caplog.at_level(logging.INFO, logger="app.workflows.executors.retry"):
            with pytest.raises(OrchestratorExecFailed) as exc_info:
                _orchestrator_exec_with_retry(factory, **_BASE_ARGS)

    assert exc_info.value.error_class == "orchestrator_exec_failed_after_retries"
    assert len(calls) == 3
    # Two retry log lines (first attempt not a retry, last attempt not logged).
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "orchestrator_exec_retry" in log_text


# ---------------------------------------------------------------------------
# 5xx retries
# ---------------------------------------------------------------------------


def test_5xx_retried_succeeds() -> None:
    factory, calls = _make_client_factory(
        _FakeResponse(503),
        _FakeResponse(503),
        _FakeResponse(200),
    )
    with patch("app.workflows.executors._retry.time.sleep"):
        result = _orchestrator_exec_with_retry(factory, **_BASE_ARGS)
    assert result.status_code == 200
    assert len(calls) == 3


def test_5xx_exhausted_raises_after_retries() -> None:
    factory, calls = _make_client_factory(
        _FakeResponse(503),
        _FakeResponse(503),
        _FakeResponse(503),
    )
    with patch("app.workflows.executors._retry.time.sleep"):
        with pytest.raises(OrchestratorExecFailed) as exc_info:
            _orchestrator_exec_with_retry(factory, **_BASE_ARGS)
    assert exc_info.value.error_class == "orchestrator_exec_failed_after_retries"
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# Non-retryable responses
# ---------------------------------------------------------------------------


def test_4xx_does_not_retry() -> None:
    factory, calls = _make_client_factory(
        _FakeResponse(400),
        _FakeResponse(200),  # should never be reached
    )
    with patch("app.workflows.executors._retry.time.sleep") as mock_sleep:
        with pytest.raises(OrchestratorExecFailed) as exc_info:
            _orchestrator_exec_with_retry(factory, **_BASE_ARGS)

    assert exc_info.value.error_class == "orchestrator_exec_failed"
    assert len(calls) == 1  # No retry.
    assert mock_sleep.call_count == 0


def test_504_does_not_retry() -> None:
    factory, calls = _make_client_factory(
        _FakeResponse(504),
        _FakeResponse(200),
    )
    with patch("app.workflows.executors._retry.time.sleep") as mock_sleep:
        with pytest.raises(OrchestratorExecFailed):
            _orchestrator_exec_with_retry(factory, **_BASE_ARGS)
    assert len(calls) == 1
    assert mock_sleep.call_count == 0


# ---------------------------------------------------------------------------
# Backoff timing
# ---------------------------------------------------------------------------


def test_backoff_delays_match_spec() -> None:
    """Delays must be 0.5s, 1.0s for first two retries (before the 3rd attempt)."""
    factory, _ = _make_client_factory(
        httpx.ConnectError("a"),
        httpx.ConnectError("b"),
        httpx.ConnectError("c"),
    )
    with patch("app.workflows.executors._retry.time.sleep") as mock_sleep:
        with pytest.raises(OrchestratorExecFailed):
            _orchestrator_exec_with_retry(factory, **_BASE_ARGS)

    delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert delays == [0.5, 1.0]
