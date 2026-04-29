"""Shell step executor: run an arbitrary command in the user's workspace.

`run_shell_step(session, step_run_id)` drives a single `shell` step from
`running` to a terminal state.  The rendered config is expected to contain:

  config = {
      "cmd": [str, ...],   # argv list, already rendered by substitution
      "cwd":  str | None,  # optional working directory inside the container
      "env":  dict | None, # optional extra env vars (merged, not replacing)
  }

Target container support:
  * `user_workspace` — implemented in S03.
  * `team_mirror`    — reserved for S04; raises `unsupported_action_for_target`
                       here so the step fails cleanly with a meaningful
                       error_class rather than a worker crash.

HTTP transport retries via `_orchestrator_exec_with_retry` (0.5s, 1s, 2s
backoff, 3 attempts) on transport errors and 5xx responses.

Secret discipline: the rendered cmd/env may carry substituted form values
or prior step stdout. Neither is logged (MEM274 / MEM164).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from sqlmodel import Session

from app.core.config import settings
from app.models import StepRun, WorkflowRun, get_datetime_utc
from app.workflows.executors._retry import (
    OrchestratorExecFailed,
    _orchestrator_exec_with_retry,
)

logger = logging.getLogger("app.workflows.executors.shell")

_DEFAULT_TIMEOUT_SECONDS = 300
_HTTP_TIMEOUT = httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS + 30, connect=5.0)

_SESSION_ID_NAMESPACE = uuid.UUID("b4e1d2f7-2c36-5a3b-af4b-8e5f8d1c0012")


def _derive_session_id(
    target_user_id: uuid.UUID, team_id: uuid.UUID, run_id: uuid.UUID
) -> uuid.UUID:
    name = f"{target_user_id}:{team_id}:{run_id}"
    return uuid.uuid5(_SESSION_ID_NAMESPACE, name)


def _mark_failed(
    session: Session,
    step_run: StepRun,
    *,
    error_class: str,
    stderr: str,
    started_monotonic: float,
    exit_code: int | None = None,
) -> None:
    finished_at = get_datetime_utc()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    step_run.status = "failed"
    step_run.error_class = error_class
    step_run.stderr = stderr
    step_run.exit_code = exit_code
    step_run.finished_at = finished_at
    step_run.duration_ms = duration_ms
    session.add(step_run)
    session.commit()
    logger.info(
        "step_run_failed run_id=%s step_index=%s exit=%s error_class=%s duration_ms=%d",
        step_run.workflow_run_id,
        step_run.step_index,
        "none" if exit_code is None else exit_code,
        error_class,
        duration_ms,
    )


def _mark_succeeded(
    session: Session,
    step_run: StepRun,
    *,
    stdout: str,
    exit_code: int,
    started_monotonic: float,
) -> None:
    finished_at = get_datetime_utc()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    step_run.status = "succeeded"
    step_run.stdout = stdout
    step_run.exit_code = exit_code
    step_run.finished_at = finished_at
    step_run.duration_ms = duration_ms
    step_run.error_class = None
    step_run.stderr = ""
    session.add(step_run)
    session.commit()
    logger.info(
        "step_run_succeeded run_id=%s step_index=%s exit=%d duration_ms=%d",
        step_run.workflow_run_id,
        step_run.step_index,
        exit_code,
        duration_ms,
    )


def run_shell_step(session: Session, step_run_id: uuid.UUID) -> None:
    """Execute a single `shell` step.

    Reads the rendered snapshot.config from the step_run row (the runner
    stores the fully-substituted config before calling us), builds the
    orchestrator request, and drives the step to a terminal state.
    """
    step_run = session.get(StepRun, step_run_id)
    if step_run is None:
        logger.warning("shell_step_missing step_run_id=%s", step_run_id)
        return

    workflow_run = session.get(WorkflowRun, step_run.workflow_run_id)
    if workflow_run is None:
        logger.warning("shell_step_orphan_run step_run_id=%s", step_run_id)
        return

    snapshot = step_run.snapshot or {}
    config: dict[str, Any] = snapshot.get("config") or {}

    # Per-step target_container check. S03 only supports user_workspace.
    target_container = snapshot.get("target_container") or "user_workspace"
    if target_container == "team_mirror":
        _mark_failed(
            session,
            step_run,
            error_class="unsupported_action_for_target",
            stderr="team_mirror target not supported until S04",
            started_monotonic=time.monotonic(),
        )
        return

    cmd: list[str] = config.get("cmd") or []
    if not cmd:
        _mark_failed(
            session,
            step_run,
            error_class="orchestrator_exec_failed",
            stderr="shell step config missing cmd",
            started_monotonic=time.monotonic(),
        )
        return

    cwd: str | None = config.get("cwd")
    env: dict[str, str] = config.get("env") or {}

    target_user_id = workflow_run.target_user_id or workflow_run.triggered_by_user_id
    if target_user_id is None:
        _mark_failed(
            session,
            step_run,
            error_class="missing_target_user",
            stderr="workflow_run has no target_user_id",
            started_monotonic=time.monotonic(),
        )
        return

    session_id = _derive_session_id(
        target_user_id, workflow_run.team_id, workflow_run.id
    )

    body: dict[str, Any] = {
        "user_id": str(target_user_id),
        "team_id": str(workflow_run.team_id),
        "cmd": cmd,
        "env": env,
        "timeout_seconds": _DEFAULT_TIMEOUT_SECONDS,
        "action": "shell",
    }
    if cwd is not None:
        body["cwd"] = cwd

    headers = {"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY}
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    url = f"{base}/v1/sessions/{session_id}/exec"

    logger.info(
        "step_run_started run_id=%s step_index=%s action=shell",
        step_run.workflow_run_id,
        step_run.step_index,
    )

    started_monotonic = time.monotonic()

    def _client_factory() -> Any:
        return httpx.Client(timeout=_HTTP_TIMEOUT)

    try:
        response = _orchestrator_exec_with_retry(
            _client_factory,
            url,
            body,
            headers,
            run_id=step_run.workflow_run_id,
            step_index=step_run.step_index,
        )
    except OrchestratorExecFailed as exc:
        _mark_failed(
            session,
            step_run,
            error_class=exc.error_class,
            stderr=exc.stderr_hint,
            started_monotonic=started_monotonic,
        )
        return

    try:
        data = response.json()
    except ValueError:
        _mark_failed(
            session,
            step_run,
            error_class="orchestrator_exec_failed",
            stderr="orchestrator_returned_non_json",
            started_monotonic=started_monotonic,
        )
        return

    stdout = data.get("stdout") or ""
    exit_code = int(data.get("exit_code") or 0)

    if exit_code != 0:
        finished_at = get_datetime_utc()
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        step_run.status = "failed"
        step_run.error_class = "cli_nonzero"
        step_run.stdout = stdout
        step_run.stderr = ""
        step_run.exit_code = exit_code
        step_run.finished_at = finished_at
        step_run.duration_ms = duration_ms
        session.add(step_run)
        session.commit()
        logger.info(
            "step_run_failed run_id=%s step_index=%s exit=%d error_class=cli_nonzero duration_ms=%d",
            step_run.workflow_run_id,
            step_run.step_index,
            exit_code,
            duration_ms,
        )
        return

    _mark_succeeded(
        session,
        step_run,
        stdout=stdout,
        exit_code=exit_code,
        started_monotonic=started_monotonic,
    )
