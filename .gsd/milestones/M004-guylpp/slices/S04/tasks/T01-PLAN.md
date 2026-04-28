---
estimated_steps: 1
estimated_files: 6
skills_used: []
---

# T01: Add projects + project_push_rules schema, models, and team-scoped CRUD endpoints

Lay the persistence layer and the thin REST surface for projects, with no orchestrator interaction yet. Adds alembic revision `s06d_projects_and_push_rules` creating two tables: `projects` (id UUID PK, team_id UUID FK→team CASCADE, installation_id BIGINT FK→github_app_installations.installation_id RESTRICT, github_repo_full_name VARCHAR(512) NOT NULL, name VARCHAR(255) NOT NULL, last_push_status VARCHAR(32) NULL, last_push_error TEXT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(team_id, name)) and `project_push_rules` (project_id UUID PK FK→projects CASCADE, mode VARCHAR(32) NOT NULL CHECK IN ('auto','rule','manual_workflow'), branch_pattern VARCHAR(255) NULL, workflow_id VARCHAR(255) NULL, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()). Adds matching SQLModels: `Project` / `ProjectPublic` / `ProjectCreate` / `ProjectUpdate` / `ProjectPushRule` / `ProjectPushRulePublic` / `ProjectPushRulePut`. Wires a new `backend/app/api/routes/projects.py` router with: GET /api/v1/teams/{team_id}/projects (member-gated, list); POST /api/v1/teams/{team_id}/projects (admin-gated, create — validates installation_id belongs to the team, creates a default push_rule row with mode=manual_workflow); GET /api/v1/projects/{project_id} (member-gated via project's team); PATCH /api/v1/projects/{project_id} (admin-gated — name only); DELETE /api/v1/projects/{project_id} (admin-gated); GET /api/v1/projects/{project_id}/push-rule (member-gated); PUT /api/v1/projects/{project_id}/push-rule (admin-gated — accepts mode + optional branch_pattern + optional workflow_id; rejects unknown modes with 422; for mode=rule, branch_pattern is required; for mode=manual_workflow, workflow_id is required; for mode=auto, both extra fields are stored as NULL). Registers the router in `backend/app/api/main.py`. Does NOT call orchestrator yet — `POST /open` lives in T03. Logs `project_created`, `project_deleted`, `project_push_rule_updated` with team_id + actor_id. Tests: migration round-trip (upgrade-shape, FK CASCADE on team delete, FK CASCADE on project delete cascading to push_rule, UNIQUE (team_id, name), CHECK on mode); 12+ endpoint tests covering team-admin gating (403 non-admin, 403 non-member, 404 missing team), unknown-installation rejection (404 installation_not_in_team), happy path with default push_rule, push-rule PUT for all three modes plus mode-specific field validation. This task delivers nothing user-visible by itself but is the persistence substrate every other S04 task reads from.

## Inputs

- ``backend/app/alembic/versions/s06c_team_mirror_volumes.py` — prior alembic revision (down_revision target for the new s06d migration)`
- ``backend/app/models.py` — existing SQLModel definitions for Team, GitHubAppInstallation, TeamMirrorVolume (we add the new Project / ProjectPushRule classes alongside)`
- ``backend/app/api/routes/teams.py` — reference team-admin gate pattern (`_assert_caller_is_team_admin` + 404-before-403 ordering, MEM047)`
- ``backend/app/api/routes/github.py` — reference shape for team-scoped + per-row endpoints (UPSERT pattern, response_model usage)`
- ``backend/app/api/team_access.py` — `assert_caller_is_team_admin` / `assert_caller_is_team_member` helpers we'll reuse`
- ``backend/app/api/main.py` — router registration site`

## Expected Output

- ``backend/app/alembic/versions/s06d_projects_and_push_rules.py` — new alembic revision creating both tables; idempotent upgrade + reversible downgrade; down_revision = `s06c_team_mirror_volumes``
- ``backend/app/models.py` — adds Project (table=True), ProjectPublic, ProjectCreate, ProjectUpdate, ProjectPushRule (table=True), ProjectPushRulePublic, ProjectPushRulePut SQLModels`
- ``backend/app/api/routes/projects.py` — new router exposing the seven endpoints listed in the description`
- ``backend/app/api/main.py` — appends `api_router.include_router(projects.router)``
- ``backend/tests/migrations/test_s06d_projects_migration.py` — migration tests for both tables (round-trip identity, FK CASCADE, UNIQUE on (team_id,name), CHECK on mode)`
- ``backend/tests/api/routes/test_projects.py` — endpoint tests covering admin/member gating, unknown-installation rejection, happy-path create with default push_rule, three-mode PUT push-rule`

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06d_projects_migration.py tests/api/routes/test_projects.py -v

## Observability Impact

INFO log keys added: `project_created project_id=<uuid> team_id=<uuid> actor_id=<uuid> repo=<owner/repo>`, `project_deleted project_id=<uuid> team_id=<uuid> actor_id=<uuid>`, `project_push_rule_updated project_id=<uuid> mode=<auto|rule|manual_workflow> actor_id=<uuid>`. Inspection surface: `psql -c 'SELECT id, team_id, github_repo_full_name, last_push_status, last_push_error FROM projects'` and the corresponding push_rules query. Failure visibility: 404 `project_not_found` (cross-team enumeration safe), 404 `installation_not_in_team` (installation belongs to a different team), 422 with field-specific detail for push-rule mode/field mismatches.
