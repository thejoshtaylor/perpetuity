"""Workflow trigger + run-detail API (M005/S02/T04).

Three routes the dashboard calls to drive the AI executor end-to-end:

    POST /api/v1/workflows/{workflow_id}/run     — dispatch a workflow_run
    GET  /api/v1/workflow_runs/{run_id}          — poll run + step_runs
    GET  /api/v1/teams/{team_id}/workflows       — list workflows for team

The router is the HTTP boundary; the Celery task `app.workflows.tasks.run_workflow`
is what actually drives `pending → running → succeeded|failed`. POST inserts
the `workflow_runs` row in `pending` plus one `step_runs` row per workflow
step (snapshot frozen per R018), commits, then `.delay()`s the task. The
client polls GET to watch transitions.

Authorization: every route is gated on team membership via
`assert_caller_is_team_member` (no admin requirement — running an AI workflow
is read-shaped from the team's POV). For POST the team is the workflow's
team; for GET-run the team is joined through workflow → team; for the team
listing the team is the URL path parameter.

Error shape (slice plan locks the discriminators):
  - 404 `{detail: "workflow_not_found"}`         POST + GET-list-by-team
  - 404 `{detail: "workflow_run_not_found"}`     GET-run
  - 403 `{detail: "not_team_member"}`            all three
  - 400 `{detail: "missing_required_field", field: "prompt"}`
        POST when a `_direct_*` workflow is dispatched without `prompt`.
  - 503 `{detail: "task_dispatch_failed"}`       POST if Celery .delay() raises;
        the run is marked failed with `error_class='dispatch_failed'` first
        so the row carries the breadcrumb (R018) for ops drilldown.

Observability: INFO `workflow_run_dispatched run_id=<uuid>
workflow_id=<uuid> trigger_type=button triggered_by_user_id=<uuid>`. The
prompt body is NEVER logged (slice plan redaction constraint); the trigger
type is always `button` for this surface, but we log it explicitly so the
field is structured for future webhook/schedule sources (S04+).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import CurrentUser, SessionDep
from app.api.team_access import assert_caller_is_team_member
from app.models import (
    StepRun,
    StepRunPublic,
    Workflow,
    WorkflowFormSchema,
    WorkflowPublic,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunDispatched,
    WorkflowRunPublic,
    WorkflowRunStatus,
    WorkflowRunTriggerType,
    WorkflowStep,
    WorkflowsPublic,
    get_datetime_utc,
)
from app.services.workflow_dispatch import TargetUserNoMembershipError, resolve_target_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows"])


# Names that follow the system-owned `_direct_*` convention require a
# `prompt` key in the trigger payload. Held here (rather than walking the
# step config) because the API boundary is the right place to enforce
# user-input shape, and the closed set is small.
_DIRECT_AI_NAMES = frozenset({"_direct_claude", "_direct_codex"})


def _snapshot_step(step: WorkflowStep) -> dict[str, Any]:
    """Freeze a WorkflowStep into the JSONB snapshot stored on step_runs.

    Mirrors `app.workflows.tasks._snapshot_step` so the API-side pre-create
    of the step_run rows matches the worker-side shape exactly. R018: this
    snapshot is what the run page renders forever, even if the parent
    WorkflowStep row is later edited or deleted.
    """
    return {
        "id": str(step.id),
        "workflow_id": str(step.workflow_id),
        "step_index": step.step_index,
        "action": step.action,
        "config": step.config or {},
    }


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=WorkflowRunDispatched,
)
def dispatch_workflow_run(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    workflow_id: uuid.UUID,
    body: WorkflowRunCreate,
) -> Any:
    """Dispatch a workflow run. Returns `{run_id, status='pending'}`.

    Inserts the `workflow_runs` row plus one `step_runs` row per workflow
    step (snapshot-frozen at dispatch time). Commits. Then enqueues
    `run_workflow.delay(run_id)` on Celery. The Celery task takes ownership
    of the row from there.

    For `_direct_claude` / `_direct_codex` the trigger payload must carry
    a non-empty `prompt` string — the API enforces this at the boundary
    rather than letting the executor surface it as a step failure, because
    a malformed dispatch is a 400, not a `error_class='cli_nonzero'`.

    If Celery `.delay()` raises (e.g. broker unavailable), we mark the run
    failed with `error_class='dispatch_failed'` so the row carries a
    breadcrumb, then surface 503. The run is never left in `pending` with
    no task on the queue — that would be the worst inspection failure mode.
    """
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(
            status_code=404, detail={"detail": "workflow_not_found"}
        )

    # Membership boundary. 404→403 ordering is fine here because we already
    # confirmed the workflow exists; the team-not-found path on the helper
    # is unreachable for a well-formed FK row.
    try:
        assert_caller_is_team_member(
            session, workflow.team_id, current_user.id
        )
    except HTTPException as exc:
        # Translate the helper's free-text 403 into the slice-plan-locked
        # discriminator shape the dashboard expects.
        if exc.status_code == 403:
            raise HTTPException(
                status_code=403, detail={"detail": "not_team_member"}
            ) from exc
        raise

    # Direct-AI workflows require a non-empty `prompt` string in the trigger
    # payload. Non-direct workflows validate required form fields instead.
    if workflow.name in _DIRECT_AI_NAMES:
        prompt = body.trigger_payload.get("prompt") if body.trigger_payload else None
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "missing_required_field",
                    "field": "prompt",
                },
            )
    else:
        # Validate required form fields defined in the workflow's form_schema.
        raw_schema = workflow.form_schema or {}
        if raw_schema:
            try:
                schema = WorkflowFormSchema.model_validate(raw_schema)
            except Exception:
                schema = WorkflowFormSchema()
            payload = body.trigger_payload or {}
            for field in schema.fields:
                if field.required and not payload.get(field.name):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "detail": "missing_required_field",
                            "field": field.name,
                        },
                    )

    # Resolve target user via dispatch service (S03 scope semantics).
    try:
        target_user_id, _fallback_reason = resolve_target_user(
            session, workflow, current_user.id
        )
    except TargetUserNoMembershipError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "detail": "target_user_no_membership",
                "workflow_id": str(exc.workflow_id),
            },
        ) from exc

    # Steps in dense step_index order — same shape the worker iterates.
    steps = list(
        session.exec(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_id == workflow.id)
            .order_by(WorkflowStep.step_index)
        ).all()
    )

    workflow_run = WorkflowRun(
        workflow_id=workflow.id,
        team_id=workflow.team_id,
        trigger_type=WorkflowRunTriggerType.button.value,
        triggered_by_user_id=current_user.id,
        target_user_id=target_user_id,
        trigger_payload=dict(body.trigger_payload or {}),
        status=WorkflowRunStatus.pending.value,
    )
    session.add(workflow_run)
    session.flush()  # populate workflow_run.id for the step_runs FK

    for step in steps:
        session.add(
            StepRun(
                workflow_run_id=workflow_run.id,
                step_index=step.step_index,
                snapshot=_snapshot_step(step),
                status="pending",
            )
        )
    session.commit()
    session.refresh(workflow_run)

    # Dispatch. Imported lazily so the route module stays importable in
    # environments without the celery package installed (e.g. lint-only
    # contexts) — the worker process always has it.
    from app.workflows.tasks import run_workflow

    try:
        run_workflow.delay(str(workflow_run.id))
    except Exception as exc:  # broker down, kombu serialization, etc.
        # Mark the run failed with the discriminator BEFORE bubbling the
        # 503 — R018: an inspector pulling step_runs by run_id later must
        # see why dispatch never produced a step_run.
        workflow_run.status = WorkflowRunStatus.failed.value
        workflow_run.error_class = "dispatch_failed"
        workflow_run.finished_at = get_datetime_utc()
        session.add(workflow_run)
        session.commit()
        logger.error(
            "workflow_run_dispatch_failed run_id=%s workflow_id=%s",
            workflow_run.id,
            workflow.id,
        )
        raise HTTPException(
            status_code=503, detail={"detail": "task_dispatch_failed"}
        ) from exc

    logger.info(
        "workflow_run_dispatched run_id=%s workflow_id=%s "
        "trigger_type=button triggered_by_user_id=%s",
        workflow_run.id,
        workflow.id,
        current_user.id,
    )

    return WorkflowRunDispatched(
        run_id=workflow_run.id,
        status=WorkflowRunStatus.pending,
    )


@router.get(
    "/workflow_runs/{run_id}",
    response_model=WorkflowRunPublic,
)
def get_workflow_run(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
) -> Any:
    """Return the WorkflowRun with its ordered step_runs.

    Membership gate is on the run's team (joined through workflow.team_id;
    the WorkflowRun row also carries team_id directly). Missing run → 404
    `workflow_run_not_found`. The dashboard polls this every 1.5s while
    `status in {pending, running}`.
    """
    workflow_run = session.get(WorkflowRun, run_id)
    if workflow_run is None:
        raise HTTPException(
            status_code=404, detail={"detail": "workflow_run_not_found"}
        )

    try:
        assert_caller_is_team_member(
            session, workflow_run.team_id, current_user.id
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=403, detail={"detail": "not_team_member"}
            ) from exc
        raise

    step_rows = list(
        session.exec(
            select(StepRun)
            .where(StepRun.workflow_run_id == run_id)
            .order_by(StepRun.step_index)
        ).all()
    )

    return WorkflowRunPublic(
        id=workflow_run.id,
        workflow_id=workflow_run.workflow_id,
        team_id=workflow_run.team_id,
        trigger_type=WorkflowRunTriggerType(workflow_run.trigger_type),
        triggered_by_user_id=workflow_run.triggered_by_user_id,
        target_user_id=workflow_run.target_user_id,
        trigger_payload=workflow_run.trigger_payload,
        status=WorkflowRunStatus(workflow_run.status),
        error_class=workflow_run.error_class,
        started_at=workflow_run.started_at,
        finished_at=workflow_run.finished_at,
        duration_ms=workflow_run.duration_ms,
        last_heartbeat_at=workflow_run.last_heartbeat_at,
        created_at=workflow_run.created_at,
        step_runs=[StepRunPublic.model_validate(row, from_attributes=True) for row in step_rows],
    )


@router.get(
    "/teams/{team_id}/workflows",
    response_model=WorkflowsPublic,
)
def list_team_workflows(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """List workflows for `team_id` (registry shape).

    Used by T05's dashboard to look up the `_direct_claude` / `_direct_codex`
    workflow ids. Returns both system-owned and user-owned workflows;
    callers that only want one cohort filter on `system_owned`. Membership
    gate is on the URL `team_id`.
    """
    try:
        assert_caller_is_team_member(session, team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 404:
            # Mirror the route discriminator shape — the helper raises a
            # plain "Team not found" string which the dashboard would have
            # to special-case.
            raise HTTPException(
                status_code=404, detail={"detail": "team_not_found"}
            ) from exc
        if exc.status_code == 403:
            raise HTTPException(
                status_code=403, detail={"detail": "not_team_member"}
            ) from exc
        raise

    rows = list(
        session.exec(
            select(Workflow)
            .where(Workflow.team_id == team_id)
            .order_by(Workflow.name)
        ).all()
    )
    data = [WorkflowPublic.model_validate(row, from_attributes=True) for row in rows]
    return WorkflowsPublic(data=data, count=len(data))
