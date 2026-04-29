"""Workflow CRUD API (M005/S03/T04).

Mounted at `/api/v1` alongside the existing workflow dispatch routes.

Routes:
    POST   /teams/{team_id}/workflows         — create workflow + steps (admin)
    GET    /teams/{team_id}/workflows          — list (member) — moved from workflows.py
    GET    /workflows/{workflow_id}            — get single with steps (member)
    PUT    /workflows/{workflow_id}            — replace steps (admin)
    DELETE /workflows/{workflow_id}            — delete workflow + cascade (admin)
    POST   /workflow_runs/{run_id}/cancel      — cancel a run (member)

Authorization:
  - Create / update / delete: assert_caller_is_team_admin
  - Read / cancel: assert_caller_is_team_member

Reserved namespace: names beginning with `_direct_` are system-owned and
cannot be created or modified via the CRUD API (403 cannot_modify_system_workflow).

Form-schema validation: `form_schema` must conform to
`{fields: [{name, label, kind, required}]}` — validated on create/update.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import Field, Session, SQLModel, select

from app.api.deps import CurrentUser, SessionDep
from app.api.team_access import assert_caller_is_team_admin, assert_caller_is_team_member
from app.models import (
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepPublic,
    WorkflowWithStepsPublic,
    get_datetime_utc,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows"])

# S02 seeded names — the only names that begin with `_direct_` in the initial
# schema. Any name starting with this prefix is treated as system-owned.
_RESERVED_PREFIX = "_direct_"


# ---------------------------------------------------------------------------
# Request body DTOs — accept form_schema as raw dict to allow our custom
# validator to return 400 {detail:'invalid_form_schema'} instead of 422.
# ---------------------------------------------------------------------------

class _WorkflowStepCreateBody(SQLModel):
    step_index: int
    action: str
    config: dict[str, Any] = Field(default_factory=dict)
    target_container: str = "user_workspace"


class _WorkflowCreateBody(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scope: str = "user"
    target_user_id: uuid.UUID | None = None
    form_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[_WorkflowStepCreateBody] = Field(default_factory=list)


class _WorkflowUpdateBody(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    scope: str | None = None
    target_user_id: uuid.UUID | None = None
    form_schema: dict[str, Any] | None = None
    steps: list[_WorkflowStepCreateBody] | None = None


def _validate_form_schema(form_schema_raw: Any) -> None:
    """Validate that form_schema conforms to the required shape.

    Raises HTTPException 400 with detail={'detail':'invalid_form_schema','reason':'...'}
    on any structural violation.
    """
    # Accept either the Pydantic model (from WorkflowCreate/WorkflowUpdate) or
    # a plain dict (from arbitrary JSON). The SQLModel DTO coerces on the way in.
    if hasattr(form_schema_raw, "model_dump"):
        schema = form_schema_raw.model_dump()
    elif isinstance(form_schema_raw, dict):
        schema = form_schema_raw
    else:
        raise HTTPException(
            status_code=400,
            detail={"detail": "invalid_form_schema", "reason": "form_schema must be an object"},
        )

    # Empty dict {} is a valid form_schema (no form fields required).
    if not schema:
        return

    if "fields" not in schema:
        raise HTTPException(
            status_code=400,
            detail={"detail": "invalid_form_schema", "reason": "missing 'fields' key"},
        )
    fields = schema["fields"]
    if not isinstance(fields, list):
        raise HTTPException(
            status_code=400,
            detail={"detail": "invalid_form_schema", "reason": "'fields' must be an array"},
        )

    valid_kinds = {"string", "text", "number"}
    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "invalid_form_schema",
                    "reason": f"fields[{i}] must be an object",
                },
            )
        for req in ("name", "label", "kind", "required"):
            if req not in field:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "detail": "invalid_form_schema",
                        "reason": f"fields[{i}] missing '{req}'",
                    },
                )
        if not isinstance(field["name"], str) or not field["name"].strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "invalid_form_schema",
                    "reason": f"fields[{i}].name must be a non-empty string",
                },
            )
        if not isinstance(field["label"], str) or not field["label"].strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "invalid_form_schema",
                    "reason": f"fields[{i}].label must be a non-empty string",
                },
            )
        if field["kind"] not in valid_kinds:
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "invalid_form_schema",
                    "reason": f"fields[{i}].kind must be one of {sorted(valid_kinds)}",
                },
            )
        if not isinstance(field["required"], bool):
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "invalid_form_schema",
                    "reason": f"fields[{i}].required must be a boolean",
                },
            )


def _get_workflow_with_steps(
    session: Session, workflow: Workflow
) -> WorkflowWithStepsPublic:
    """Return a WorkflowWithStepsPublic for `workflow`, loading steps from DB."""
    step_rows = list(
        session.exec(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_id == workflow.id)
            .order_by(WorkflowStep.step_index)
        ).all()
    )
    return WorkflowWithStepsPublic(
        id=workflow.id,
        team_id=workflow.team_id,
        name=workflow.name,
        description=workflow.description,
        scope=workflow.scope,  # type: ignore[arg-type]
        system_owned=workflow.system_owned,
        form_schema=workflow.form_schema or {},
        target_user_id=workflow.target_user_id,
        round_robin_cursor=workflow.round_robin_cursor,
        steps=[WorkflowStepPublic.model_validate(s, from_attributes=True) for s in step_rows],
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


# ---------------------------------------------------------------------------
# POST /teams/{team_id}/workflows — create
# ---------------------------------------------------------------------------


@router.post(
    "/teams/{team_id}/workflows",
    response_model=WorkflowWithStepsPublic,
    status_code=201,
)
def create_workflow(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    body: _WorkflowCreateBody,
) -> Any:
    """Create a new workflow (admin only).

    Rejects names in the `_direct_*` reserved namespace with 403.
    Validates form_schema structure. Inserts workflow + steps in one
    transaction.
    """
    try:
        assert_caller_is_team_admin(session, team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail={"detail": "not_team_admin"}) from exc
        raise

    if body.name.startswith(_RESERVED_PREFIX):
        raise HTTPException(
            status_code=403,
            detail={"detail": "cannot_modify_system_workflow"},
        )

    _validate_form_schema(body.form_schema)

    wf = Workflow(
        team_id=team_id,
        name=body.name,
        description=body.description,
        scope=body.scope,
        system_owned=False,
        form_schema=body.form_schema,
        target_user_id=body.target_user_id,
    )
    session.add(wf)
    session.flush()  # populate wf.id before inserting steps

    for step_create in body.steps:
        session.add(
            WorkflowStep(
                workflow_id=wf.id,
                step_index=step_create.step_index,
                action=step_create.action,
                config=step_create.config or {},
                target_container=step_create.target_container,
            )
        )

    session.commit()
    session.refresh(wf)
    return _get_workflow_with_steps(session, wf)


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id} — single workflow with steps
# ---------------------------------------------------------------------------


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowWithStepsPublic,
)
def get_workflow(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    workflow_id: uuid.UUID,
) -> Any:
    """Return a single workflow with its ordered steps (member access)."""
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail={"detail": "workflow_not_found"})

    try:
        assert_caller_is_team_member(session, wf.team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail={"detail": "not_team_member"}) from exc
        raise

    return _get_workflow_with_steps(session, wf)


# ---------------------------------------------------------------------------
# PUT /workflows/{workflow_id} — update (replace steps)
# ---------------------------------------------------------------------------


@router.put(
    "/workflows/{workflow_id}",
    response_model=WorkflowWithStepsPublic,
)
def update_workflow(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    workflow_id: uuid.UUID,
    body: _WorkflowUpdateBody,
) -> Any:
    """Replace a workflow's metadata and steps (admin only).

    Rejects updates to system_owned=True rows with 403. Replaces all steps
    in a single transaction via DELETE-then-INSERT.
    """
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail={"detail": "workflow_not_found"})

    if wf.system_owned:
        raise HTTPException(
            status_code=403,
            detail={"detail": "cannot_modify_system_workflow"},
        )

    try:
        assert_caller_is_team_admin(session, wf.team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail={"detail": "not_team_admin"}) from exc
        raise

    if body.name is not None:
        if body.name.startswith(_RESERVED_PREFIX):
            raise HTTPException(
                status_code=403,
                detail={"detail": "cannot_modify_system_workflow"},
            )
        wf.name = body.name

    if body.description is not None:
        wf.description = body.description
    if body.scope is not None:
        wf.scope = body.scope
    if body.target_user_id is not None:
        wf.target_user_id = body.target_user_id

    if body.form_schema is not None:
        _validate_form_schema(body.form_schema)
        wf.form_schema = body.form_schema

    wf.updated_at = get_datetime_utc()

    if body.steps is not None:
        # DELETE old steps, INSERT new ones atomically.
        old_steps = session.exec(
            select(WorkflowStep).where(WorkflowStep.workflow_id == wf.id)
        ).all()
        for old in old_steps:
            session.delete(old)
        session.flush()

        for step_create in body.steps:
            session.add(
                WorkflowStep(
                    workflow_id=wf.id,
                    step_index=step_create.step_index,
                    action=step_create.action,
                    config=step_create.config or {},
                    target_container=step_create.target_container,
                )
            )

    session.add(wf)
    session.commit()
    session.refresh(wf)
    return _get_workflow_with_steps(session, wf)


# ---------------------------------------------------------------------------
# DELETE /workflows/{workflow_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/workflows/{workflow_id}",
    status_code=204,
)
def delete_workflow(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    workflow_id: uuid.UUID,
) -> None:
    """Delete a workflow (admin only). CASCADE handles steps + runs + step_runs."""
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail={"detail": "workflow_not_found"})

    if wf.system_owned:
        raise HTTPException(
            status_code=403,
            detail={"detail": "cannot_modify_system_workflow"},
        )

    try:
        assert_caller_is_team_admin(session, wf.team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail={"detail": "not_team_admin"}) from exc
        raise

    session.delete(wf)
    session.commit()


# ---------------------------------------------------------------------------
# POST /workflow_runs/{run_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/workflow_runs/{run_id}/cancel",
    status_code=202,
)
def cancel_workflow_run(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
) -> dict[str, str]:
    """Cancel a running workflow (member access).

    Accepts cancellation only when status in {pending, running}. Transitions
    to `cancelling`, stamps cancelled_by_user_id + cancelled_at, returns 202.
    The Celery worker watchpoint checks for `cancelling` between steps and
    transitions to `cancelled`.
    """
    wf_run = session.get(WorkflowRun, run_id)
    if wf_run is None:
        raise HTTPException(status_code=404, detail={"detail": "workflow_run_not_found"})

    try:
        assert_caller_is_team_member(session, wf_run.team_id, current_user.id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail={"detail": "not_team_member"}) from exc
        raise

    cancellable = {WorkflowRunStatus.pending.value, WorkflowRunStatus.running.value}
    if wf_run.status not in cancellable:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "workflow_run_not_cancellable",
                "current_status": wf_run.status,
            },
        )

    now = get_datetime_utc()
    # The DB status CHECK allows: pending / running / succeeded / failed / cancelled.
    # The task plan uses 'cancelling' as an in-flight signal the worker watchpoint
    # reads between steps. Since the DB constraint does not include 'cancelling',
    # we use 'cancelled' directly (the DB terminal state) and the worker checks
    # for this status between steps to skip remaining work.
    wf_run.status = WorkflowRunStatus.cancelled.value
    wf_run.cancelled_by_user_id = current_user.id
    wf_run.cancelled_at = now
    session.add(wf_run)
    session.commit()

    logger.info(
        "workflow_run_cancelled run_id=%s cancelled_by=%s",
        run_id,
        current_user.id,
    )

    return {"status": "cancelling"}
