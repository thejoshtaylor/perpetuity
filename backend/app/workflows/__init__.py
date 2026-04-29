"""Workflow execution engine package (M005/S02).

`tasks.run_workflow` is the Celery entrypoint that the API layer enqueues
when a `WorkflowRun` is dispatched. It iterates the parent workflow's
steps in `step_index` order, opening one `step_runs` row per step and
delegating to the right action executor in `executors/`.

The engine is the single source of truth for `workflow_runs.status` and
`step_runs.status` transitions — the API layer only writes the initial
`pending` row.
"""
