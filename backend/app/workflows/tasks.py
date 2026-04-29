"""Celery tasks for the M005 workflow runner (T03).

`run_workflow(run_id)` is the only task M005/S02 ships. The API layer
(T04) inserts a `workflow_runs` row in `pending` status, then enqueues
this task with the row id; the worker takes ownership of the row and
drives it to a terminal state.

Lifecycle:
  1. Load the WorkflowRun.
  2. Idempotency guard: if status != 'pending', log and return. Celery's
     `task_acks_late` + `task_reject_on_worker_lost` mean a crashed worker
     re-delivers; the guard prevents a re-delivery from clobbering an
     already-running or already-finished row.
  3. Transition pending → running, stamp `started_at` and
     `last_heartbeat_at`. Log `workflow_run_started`.
  4. For each WorkflowStep in step_index order:
       * Snapshot the step row (frozen JSONB on `step_runs.snapshot`).
       * Insert step_run with status='pending'.
       * Transition step_run pending → running.
       * Dispatch by action:
           - claude / codex → `run_ai_step`
           - shell / git    → reserved for S03 → mark step failed with
             `error_class='unsupported_action'` (NOT NotImplementedError —
             we don't want a future seed to crash the whole worker).
       * After the executor returns, re-load the step_run; if its terminal
         status is 'failed', propagate the error_class to the parent run
         and stop iterating.
  5. After the last step (or after a failure), stamp the run's terminal
     status, finished_at, duration_ms. Log `workflow_run_succeeded` or
     `workflow_run_failed`.
  6. Any unhandled exception escapes the task body so Celery's
     `task_reject_on_worker_lost` semantics kick in. Before re-raising
     we DO try to stamp `error_class='worker_crash'` on the run so the
     next agent inspecting Postgres sees the discriminator. The re-raise
     is what surfaces the crash to Celery; the Postgres stamp is a
     best-effort breadcrumb.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.celery_app import celery_app
from app.core.db import engine
from app.models import (
    StepRun,
    WorkflowRun,
    WorkflowStep,
    get_datetime_utc,
)
from app.workflows.executors.ai import run_ai_step

logger = logging.getLogger("app.workflows.tasks")

_AI_ACTIONS = frozenset({"claude", "codex"})


def _snapshot_step(step: WorkflowStep) -> dict[str, Any]:
    """Freeze a WorkflowStep into the JSONB snapshot stored on step_runs.

    Per R018: this snapshot is what the run page renders forever, even if
    the parent WorkflowStep row is later edited or deleted. We capture
    every field a downstream executor or UI might want; the JSONB column
    swallows any future additions without a migration.
    """
    return {
        "id": str(step.id),
        "workflow_id": str(step.workflow_id),
        "step_index": step.step_index,
        "action": step.action,
        "config": step.config or {},
    }


def _execute_one_step(
    session: Session,
    workflow_run: WorkflowRun,
    step: WorkflowStep,
) -> StepRun:
    """Transition the pending step_run row to `running`, dispatch the
    executor, return the row.

    The dispatch route in ``app.api.routes.workflows.dispatch_workflow_run``
    pre-creates one ``step_runs`` row per workflow step in `pending` status
    at commit-of-the-trigger time so the run-detail GET endpoint can render
    the full step list before the worker has picked up the task. The
    ``UNIQUE (workflow_run_id, step_index)`` constraint means the worker
    must NOT insert a fresh row — instead we look up the pending row and
    flip it to `running` in place. If no pending row exists (older runs
    pre-dating the API-side pre-create, or a manual DB tweak) we fall back
    to creating one.

    Returns the (refreshed) StepRun so the caller can read `status` /
    `error_class` to decide whether to keep iterating.
    """
    existing = session.exec(
        select(StepRun)
        .where(StepRun.workflow_run_id == workflow_run.id)
        .where(StepRun.step_index == step.step_index)
    ).first()
    if existing is not None:
        existing.snapshot = _snapshot_step(step)
        existing.status = "running"
        existing.started_at = get_datetime_utc()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        step_run = existing
    else:
        step_run = StepRun(
            workflow_run_id=workflow_run.id,
            step_index=step.step_index,
            snapshot=_snapshot_step(step),
            status="running",
            started_at=get_datetime_utc(),
        )
        session.add(step_run)
        session.commit()
        session.refresh(step_run)

    if step.action in _AI_ACTIONS:
        run_ai_step(session, step_run.id)
    else:
        # shell / git → S03. Mark the step failed inline (without raising)
        # so the parent run gets a clean failed status with a meaningful
        # error_class.
        finished_at = get_datetime_utc()
        duration_ms = 0
        step_run.status = "failed"
        step_run.error_class = "unsupported_action"
        step_run.stderr = f"action {step.action!r} not supported in S02"
        step_run.finished_at = finished_at
        step_run.duration_ms = duration_ms
        session.add(step_run)
        session.commit()
        logger.info(
            "step_run_failed run_id=%s step_index=%s exit=none error_class=unsupported_action duration_ms=%d",
            workflow_run.id,
            step.step_index,
            duration_ms,
        )

    # Re-load so we see whatever the executor wrote.
    session.refresh(step_run)
    return step_run


def _drive_run(session: Session, run_id: uuid.UUID) -> None:
    """Engine body — separated from the Celery task wrapper for testability.

    Tests call this directly with a real Session; the Celery `run_workflow`
    task wrapper opens its own Session against `engine` and delegates here.
    """
    workflow_run = session.get(WorkflowRun, run_id)
    if workflow_run is None:
        logger.warning("workflow_run_missing run_id=%s", run_id)
        return

    if workflow_run.status != "pending":
        # Idempotency: re-deliver, manual replay, or a duplicated dispatch
        # all hit this branch. The first-write-wins semantics keep the
        # original run's history intact.
        logger.info(
            "workflow_run_skipped_not_pending run_id=%s status=%s",
            run_id,
            workflow_run.status,
        )
        return

    started_monotonic = time.monotonic()
    now = get_datetime_utc()
    workflow_run.status = "running"
    workflow_run.started_at = now
    workflow_run.last_heartbeat_at = now
    session.add(workflow_run)
    session.commit()

    logger.info(
        "workflow_run_started run_id=%s workflow_id=%s",
        workflow_run.id,
        workflow_run.workflow_id,
    )

    # Steps in dense step_index order. Empty workflow → falls through to
    # success branch.
    steps = list(
        session.exec(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_id == workflow_run.workflow_id)
            .order_by(WorkflowStep.step_index)
        ).all()
    )

    failed_error_class: str | None = None
    for step in steps:
        step_run = _execute_one_step(session, workflow_run, step)
        if step_run.status == "failed":
            failed_error_class = step_run.error_class or "unknown"
            break

    finished_at = get_datetime_utc()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    # Re-fetch under the same session to make sure we're writing on a
    # row with no stale in-memory state (the executors commit between).
    session.refresh(workflow_run)

    if failed_error_class is not None:
        workflow_run.status = "failed"
        workflow_run.error_class = failed_error_class
        workflow_run.finished_at = finished_at
        workflow_run.duration_ms = duration_ms
        session.add(workflow_run)
        session.commit()
        logger.info(
            "workflow_run_failed run_id=%s duration_ms=%d error_class=%s",
            workflow_run.id,
            duration_ms,
            failed_error_class,
        )
        return

    workflow_run.status = "succeeded"
    workflow_run.finished_at = finished_at
    workflow_run.duration_ms = duration_ms
    workflow_run.error_class = None
    session.add(workflow_run)
    session.commit()
    logger.info(
        "workflow_run_succeeded run_id=%s duration_ms=%d",
        workflow_run.id,
        duration_ms,
    )


@celery_app.task(
    name="app.workflows.run_workflow",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_workflow(self: Any, run_id: str) -> None:  # noqa: ARG001
    """Celery task wrapper. Opens its own Session, delegates to `_drive_run`.

    Any unhandled exception inside `_drive_run` lands here. We do a
    best-effort `error_class='worker_crash'` stamp on the run before
    re-raising so the row carries the discriminator even if Celery's
    `task_reject_on_worker_lost` re-queues the message and the next
    worker re-runs (the idempotency guard there will see status='running'
    and skip).
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        logger.warning("workflow_run_bad_run_id run_id=%s", run_id)
        return

    try:
        with Session(engine) as session:
            _drive_run(session, run_uuid)
    except Exception:
        # Worker crash path. Open a FRESH session for the breadcrumb write
        # because the original session may be in an aborted-transaction
        # state after the unhandled exception.
        try:
            with Session(engine) as session:
                wf_run = session.get(WorkflowRun, run_uuid)
                if wf_run is not None and wf_run.status == "running":
                    wf_run.status = "failed"
                    wf_run.error_class = "worker_crash"
                    wf_run.finished_at = get_datetime_utc()
                    session.add(wf_run)
                    session.commit()
                    logger.error(
                        "workflow_run_failed run_id=%s error_class=worker_crash",
                        run_uuid,
                    )
        except Exception:
            # Postgres unavailable too — let the original raise carry.
            pass
        raise
