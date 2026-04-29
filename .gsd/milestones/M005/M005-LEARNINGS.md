---
phase: M005
phase_name: "Workflow CRUD API + Dispatch Service + Frontend Editor"
project: perpetuity
generated: "2026-04-29T08:14:00Z"
counts:
  decisions: 7
  lessons: 5
  patterns: 3
  surprises: 2
missing_artifacts: []
---

### Decisions

- **Terminal cancellation state: write 'cancelled' directly, no intermediate 'cancelling'.** The DB CHECK constraint on workflow_run.status allows exactly 5 values (pending/running/succeeded/failed/cancelled). Adding an intermediate state would require an ALTER TABLE and makes the state machine more complex. Direct write is correct and simpler.
  Source: S03-SUMMARY.md/Key decisions

- **round_robin cursor atomicity via raw SQL UPDATE...RETURNING.** An ORM SELECT-then-UPDATE has a TOCTOU race under concurrent dispatch. Using raw `UPDATE workflows SET round_robin_cursor = (round_robin_cursor + 1) % :count WHERE id = :id RETURNING round_robin_cursor` is atomic at the Postgres level and requires no application-level locking.
  Source: S03-SUMMARY.md/Key decisions

- **resolve_target_user accepts both string and WorkflowScope enum.** After SQLModel deserialization, scope may arrive as a string rather than the enum instance. Guarding every comparison with dual forms (`scope == WorkflowScope.user or scope == "user"`) prevents silent misrouting.
  Source: S03-SUMMARY.md/Key decisions

- **system_owned check precedes team-admin gate on PUT/DELETE.** Checking system_owned=True and returning 403 before calling assert_caller_is_team_admin() gives non-admins a structured error naming the reason (system_owned) rather than a confusing auth-failure path.
  Source: S03-SUMMARY.md/Key decisions

- **form_schema validation returns 400 not 422.** Pydantic type errors return 422 automatically. Returning 400 with `{detail, reason}` for JSON Schema validation errors lets callers programmatically distinguish schema errors from type errors.
  Source: S03-SUMMARY.md/Key decisions

- **TargetUserNoMembershipError carries structured payload for 409.** Including workflow_id and target_user_id in the exception lets the API boundary produce a structured 409 with actionable context for the caller.
  Source: S03-SUMMARY.md/Key decisions

- **target_container column pre-landed in s13 for S04 forward compatibility.** Adding `target_container VARCHAR(32) CHECK IN ('user_workspace', 'team_mirror')` to workflow_steps now means S04 can use 'team_mirror' without an ALTER TABLE. Pre-landing schema columns for the next slice is a valid cost when it avoids a migration in a tight dependency chain.
  Source: S03-SUMMARY.md/Key decisions

### Lessons

- **pytest must be run from the backend/ subdirectory, not the project root.** The project root lacks the conftest.py and pyproject.toml that configure the test environment and path resolution. Invoking `python -m pytest tests/api/...` from the root produces path errors that look like code failures. Always: `cd backend && python -m pytest`.
  Source: S03-SUMMARY.md/Deviations

- **There is no backend/app/schemas.py — all models and DTOs are co-located in backend/app/models.py.** Task plans that reference schemas.py will break on import. Any new task touching DTOs must reference models.py.
  Source: S03-SUMMARY.md/Deviations

- **JSONB server_default in Alembic must use sa.text("'{}'::jsonb") not a Python dict or string literal.** PostgreSQL double-quotes a plain Python dict literal when it becomes the column default, producing `'{}'` (with escaped quotes) instead of `'{}'::jsonb`. Use `server_default=sa.text("'{}'::jsonb")`.
  Source: S03-SUMMARY.md/What Happened (T01)

- **Migration test files belong in backend/tests/migrations/, not backend/tests/api/.** All migration tests in this project follow this convention. Writing migration tests under tests/api/ will cause them to be missed by the migration-specific test runner invocation.
  Source: S03-SUMMARY.md/Deviations

- **round_robin atomicity under concurrent load is proven by code pattern only — no concurrent load test exists.** The UPDATE...RETURNING pattern is correct, but no pytest or load test exercises concurrent dispatch. If a race regression is introduced it will be invisible to the current test suite.
  Source: S03-SUMMARY.md/Known limitations

### Patterns

- **CRUD routes with system_owned guard pattern.** On any route that modifies or deletes a resource that might be system-owned, check system_owned=True and return 403 BEFORE calling the team-admin gate. This gives non-admins a structured, named rejection rather than a confusing auth error path. Applied in update_workflow() and delete_workflow() in workflows_crud.py.
  Source: S03-SUMMARY.md/Patterns established

- **Atomic step replacement: DELETE + flush + INSERT in a single transaction.** When replacing a one-to-many child collection (e.g., workflow steps), use `session.exec(delete(WorkflowStep).where(...))` + `session.flush()` + bulk INSERT of the new set — all within one transaction. This avoids partial state and constraint violations from naive ORM collection management.
  Source: S03-SUMMARY.md/Patterns established

- **Dispatch service fallback chain: scope routing → membership validation → live-workspace filter → triggering user fallback.** For team-scope routing, always validate membership first (raise structured error if invalid), then filter by live workspace, then fall back to the triggering user with a structured reason string. This pattern ensures every dispatch path produces an auditable reason even when the primary target is unavailable.
  Source: S03-SUMMARY.md/Patterns established

### Surprises

- **The auto-fix trigger was a working-directory false alarm, not a code bug.** The gate runner invoked pytest from the project root rather than the backend/ directory. The tests existed and passed — no code changes were required. The fix was purely operational (correct invocation), but the auto-fix pipeline treated it as a code failure and triggered a remediation loop.
  Source: S03-SUMMARY.md/Verification note

- **AC-8 (M005-CONTEXT.md depth-verification) was mechanically blocked and never unlocked.** The depth-verification gate was rejected by the harness and the user never manually unlocked it. All 7 functional ACs passed with full test evidence, but the procedural AC-8 left the validation verdict at needs-attention. No functional gap resulted, but it means this milestone has no formally-approved acceptance criteria document.
  Source: M005-VALIDATION.md/Success Criteria Checklist
