"""Per-action executors for the workflow runner.

Each executor is a callable `run_<action>_step(session, step_run_id) -> None`
that owns the lifecycle of a single `step_runs` row from `running` to
`succeeded` / `failed`. The runner in `tasks.py` dispatches by
`WorkflowStep.action`; new actions land here as new modules.
"""
