---
estimated_steps: 1
estimated_files: 7
skills_used: []
---

# T03: Wire notify() at team_invite_accepted + project_created call sites and add /notifications/test admin trigger

Plug the helper into the live code paths so the bell has real content, and add a system-admin trigger so the seed-test path doesn't depend on a real invite/project flow. (1) `backend/app/api/routes/teams.py` line 245 area: right after the existing `invite_accepted` log line and AFTER `session.commit() + session.refresh(team)` (per MEM035 so the payload contains real refreshed values), call `notify(session, user_id=current_user.id, kind=NotificationKind.team_invite_accepted, payload={'team_id': str(team.id), 'team_name': team.name}, source_team_id=team.id)` — before the `return TeamWithRole(...)`. (2) `backend/app/api/routes/projects.py` line 296 area: after the `project_created` INFO log, before the `return ProjectPublic(...)`, fan out a notify per recipient. There is no `crud.list_team_members` helper, so inline the query: `recipients = session.exec(select(TeamMember).where(TeamMember.team_id == project.team_id).where(TeamMember.role.in_([TeamRole.admin, TeamRole.owner]))).all()`. For each recipient call `notify(session, user_id=recipient.user_id, kind=NotificationKind.project_created, payload={'project_id': str(project.id), 'project_name': project.name, 'team_id': str(project.team_id), 'repo': project.github_repo_full_name}, source_team_id=project.team_id, source_project_id=project.id)`. Wrap the loop in try/except logging at WARNING level only — must not break project creation if notify() somehow re-raises. (3) Add `POST /notifications/test` to `backend/app/api/routes/notifications.py` gated on `Depends(get_current_active_superuser)` (the existing dep at deps.py:55) — body: `{user_id: UUID | None = None, message: str = 'System test notification'}`. Resolve user_id default to current_user.id when None. Calls `notify(session, user_id=resolved_user_id, kind=NotificationKind.system, payload={'message': message})`. Returns the created NotificationPublic (or 500 if notify returned None due to in_app=False — that means the operator toggled system off, which is expected). (4) Tests: `backend/tests/api/routes/test_teams.py::test_invite_accept_creates_notification` posts the accept and asserts a notifications row exists for the accepter with kind=team_invite_accepted and payload contains team_name. `backend/tests/api/routes/test_projects.py::test_project_create_notifies_admins` creates a team with two admins + one member, creates a project, asserts exactly two notifications were inserted (one per admin) and zero for the non-admin member. `backend/tests/api/routes/test_notifications.py::test_notifications_test_endpoint_creates_system_kind` confirms a non-superuser gets 403 and a superuser creates a row visible in their list. (5) Regenerate the OpenAPI client.

## Inputs

- ``backend/app/api/routes/teams.py``
- ``backend/app/api/routes/projects.py``
- ``backend/app/api/routes/notifications.py``
- ``backend/app/api/deps.py``
- ``backend/app/core/notify.py``
- ``backend/app/models.py``
- ``backend/tests/api/routes/test_teams.py``
- ``backend/tests/api/routes/test_projects.py``
- ``backend/tests/api/routes/test_notifications.py``

## Expected Output

- ``backend/app/api/routes/teams.py``
- ``backend/app/api/routes/projects.py``
- ``backend/app/api/routes/notifications.py``
- ``backend/tests/api/routes/test_teams.py``
- ``backend/tests/api/routes/test_projects.py``
- ``backend/tests/api/routes/test_notifications.py``
- ``frontend/src/client/sdk.gen.ts``

## Verification

cd backend && set -a && source ../.env && set +a && uv run pytest tests/api/routes/test_teams.py::test_invite_accept_creates_notification tests/api/routes/test_projects.py::test_project_create_notifies_admins tests/api/routes/test_notifications.py::test_notifications_test_endpoint_creates_system_kind -x && bash scripts/generate-client.sh && grep -q 'notificationsTest\|notifications.test\|notifications_test' frontend/src/client/sdk.gen.ts

## Observability Impact

Signals added: invite-accept and project-create paths now emit `notify.dispatched` + `notify.skipped_in_app` per recipient (see T02's notify helper). The new `/notifications/test` endpoint emits `notifications.test_triggered actor_id=<uuid> target_user_id=<uuid>` so an operator can prove an admin-driven seed worked even when the recipient's in_app preference is off. How a future agent inspects this: `psql -c "SELECT count(*) FROM notifications WHERE kind='project_created' AND created_at > now() - interval '1 hour'"` after a project create proves the wiring; the test endpoint is grep-able in API logs by route name. Failure state exposed: notify call-site failures land as the standard `notify.insert_failed` ERROR — invite/project creation completes regardless.
