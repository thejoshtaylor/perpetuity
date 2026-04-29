---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Extend workflows + workflow_steps schema for form fields, scope target_user, round-robin cursor, and per-step target_container

S03 schema additions on top of S02's spine. Adds: `workflows.form_schema JSONB NOT NULL DEFAULT '{}'::jsonb` (renders the trigger form — initially `{fields: [{name, label, kind: 'string'|'text'|'number', required: bool}]}`), `workflows.target_user_id UUID NULL` (FK to user.id, ondelete SET NULL — only meaningful when scope='team_specific'), `workflows.round_robin_cursor BIGINT NOT NULL DEFAULT 0` (monotonic counter for round-robin pick), `workflow_steps.target_container VARCHAR(32) NOT NULL DEFAULT 'user_workspace'` (CHECK in {'user_workspace', 'team_mirror'} — team_mirror is reserved for S04 but the column lands now so S04 doesn't ALTER), `workflow_runs.cancelled_by_user_id UUID NULL` (FK user.id, ondelete SET NULL — audit on cancellation), `workflow_runs.cancelled_at TIMESTAMPTZ NULL`. New alembic revision `s13_workflow_crud_extensions.py` with up + down. Update SQLModel rows + Pydantic DTOs accordingly: extend Workflow with the 3 new columns, add `WorkflowFormField` + `WorkflowFormSchema` Pydantic models, extend WorkflowPublic / WorkflowWithStepsPublic with `form_schema`, `target_user_id`, `round_robin_cursor`, extend WorkflowStep + WorkflowStepPublic with `target_container` (typed as `WorkflowStepTargetContainer` enum so OpenAPI emits a string-literal union per MEM352), extend WorkflowRun + WorkflowRunPublic with `cancelled_by_user_id` + `cancelled_at`. Add new Create/Update DTOs: `WorkflowCreate` (name, description, scope, target_user_id, form_schema, steps: list[WorkflowStepCreate]), `WorkflowUpdate` (all fields optional except none), `WorkflowStepCreate` (step_index, action, config, target_container). The `system_owned` boolean MUST default False on all CRUD-created rows — only the seed helper writes True. Migration tests cover: column shape + defaults, FK cascade behavior (user delete sets target_user_id to NULL but does NOT cascade delete the workflow), CHECK rejection of unknown target_container values, downgrade restores prior schema.

## Inputs

- ``backend/app/alembic/versions/s10_workflows.py``
- ``backend/app/alembic/versions/s11_workflow_runs.py``
- ``backend/app/alembic/versions/s12_seed_direct_workflows.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/alembic/versions/s13_workflow_crud_extensions.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s13_workflow_crud_extensions_migration.py``

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s13_workflow_crud_extensions_migration.py -v

## Observability Impact

No runtime signals added — schema-only task. Inspection surface: `psql perpetuity_app -c "\d workflows"` shows new columns; `\d workflow_steps` shows target_container column with CHECK; `\d workflow_runs` shows cancellation audit columns.
