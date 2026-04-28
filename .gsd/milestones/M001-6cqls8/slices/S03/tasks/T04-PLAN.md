---
estimated_steps: 48
estimated_files: 3
skills_used: []
---

# T04: Integration tests for invites, join, PATCH role, DELETE member + full-suite self-audit

Add the full slice-level verification test set. Two new test files plus extensions to existing files. All tests run against real Postgres via the existing `client` + `db` fixtures (MEM029 cookie-per-user pattern for multi-user flows).

New file `backend/tests/api/routes/test_invites.py` (10 cases minimum):
1. `test_invite_returns_code_url_expires_at` — admin signs up, creates non-personal team, POST /{id}/invite → 200; body has `code` (string ≥ 32 chars), `url` (contains `/invite/`), `expires_at` (future ISO-8601 ≥ now + 6 days).
2. `test_invite_personal_team_returns_403` — existing behavior survives refactor; keep in teams.py test file but re-assert here too for defense-in-depth.
3. `test_invite_as_non_admin_returns_403` — user A creates team, user B signs up (not a member), user B POSTs /{A_team}/invite → 403.
4. `test_invite_as_member_not_admin_returns_403` — A creates team, A invites B (B accepts to become member), B tries to POST /{team}/invite → 403.
5. `test_join_valid_code_adds_member_and_marks_used` — A creates team, A issues invite, B signs up, B POSTs /teams/join/{code} → 200 with TeamWithRole(role=member); GET /teams as B now returns 2 teams (personal + joined); DB shows team_invite.used_at is set and used_by = B.id.
6. `test_join_unknown_code_returns_404` — B signs up, POSTs /teams/join/garbage → 404.
7. `test_join_expired_code_returns_410` — issue an invite, manually backdate `expires_at` via a direct DB UPDATE (use the `db` session fixture), attempt join → 410 'Invite expired'.
8. `test_join_used_code_returns_410` — A issues invite, B accepts, C tries same code → 410 'Invite already used'.
9. `test_join_duplicate_member_returns_409` — A issues invite, A (already admin) tries to accept own invite → 409 'Already a member'.
10. `test_join_atomicity_on_membership_insert_failure` — monkeypatch `crud.accept_team_invite` (or its internal TeamMember insert) to raise mid-transaction; assert response is 500 AND the invite's used_at is still NULL (rollback succeeded). Pattern: mirror `test_signup_rolls_back_on_mid_transaction_failure` from test_auth.py — use `TestClient(app, raise_server_exceptions=False)`.

New file `backend/tests/api/routes/test_members.py` (7 cases minimum):
1. `test_patch_role_promotes_member_to_admin` — A creates team, invites B, B joins. A PATCHes /teams/{t}/members/{B_id}/role with {role:'admin'} → 200; GET /teams as B shows role=admin.
2. `test_patch_role_demotes_admin_to_member` — after above, A PATCHes B back to member → 200; GET /teams as B shows role=member.
3. `test_patch_role_as_non_admin_returns_403` — B (member) tries to PATCH A's role → 403.
4. `test_patch_role_demoting_last_admin_returns_400` — A is sole admin, A PATCHes self to member → 400 'Cannot demote the last admin'.
5. `test_patch_role_unknown_target_returns_404` — A PATCHes a random UUID that is not a member → 404.
6. `test_patch_role_invalid_body_returns_422` — A PATCHes with {role:'owner'} → 422.
7. `test_delete_member_removes_row_returns_204` — A creates team, invites B, B joins. A DELETEs /teams/{t}/members/{B_id} → 204; GET /teams as B shows only personal team.
8. `test_delete_last_admin_returns_400` — A (sole admin) tries to DELETE self → 400 'Cannot remove the last admin'.
9. `test_delete_on_personal_team_returns_400` — A tries to DELETE self from their personal team → 400 'Cannot remove members from personal teams'.

Extension to existing `backend/tests/api/routes/test_teams.py`:
- The pre-existing `test_invite_on_non_personal_team_returns_501_stub` is modified in T02 to expect 200. Verify that modification landed; if T04 runs before T02 in a re-plan, add the assertion change here.

Self-audit step (MANDATORY — done in this task before claiming slice complete):
- Run `cd backend && uv run pytest tests/ -v` — record pass count. Expected: ≥ 93 (S02 baseline) + ≥ 19 new S03 tests = 112+ passing.
- `rg -n 'raw_code\|print.*code\|logger.*code=' backend/app` to ensure no raw invite codes leak into logs (excluding `code_hash=`).
- `rg -n '501' backend/app/api/routes/teams.py` returns zero — the stub is gone.
- Confirm `.gsd/` files are not staged for git commit.
- Walk through each S03 must-have from the slice plan and point at the specific test(s) that prove it — document in T04-SUMMARY.md.

Must-haves:
- At least 10 test_invites.py cases, 7 test_members.py cases, all passing.
- Cross-user isolation proven: the member-removal and role-change tests MUST involve at least two distinct users to catch any accidental drop of the admin check.
- Atomicity test proven via monkeypatch — not relied on via code reading.
- Full suite green.
- Uses `_signup` helper pattern from existing `test_teams.py` (copy or import it via a shared test util if the diff stays clean).

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| TestClient against app | Fail test with full response dump | N/A | N/A |
| db fixture | Fail test; db deadlock would show in MEM016 territory but these tests do not run alembic so risk is low | N/A | N/A |

Load Profile:
- Shared resources: TestClient cookie jar across tests (MEM015 — use `_signup` detached-jar pattern for multi-user tests).
- Per-operation cost: ≈10–30 HTTP requests per test via TestClient.
- 10x breakpoint: not applicable at test scope.

Negative Tests (already enumerated in the cases above): unknown, expired, used, duplicate_member, non-admin, last-admin, invalid role body, personal-team DELETE.

Observability Impact:
- No runtime signals added (pure test work). Future-agent inspection: each test name is self-documenting; `pytest tests/api/routes/test_invites.py::test_join_expired_code_returns_410 -v` targets one behavior without re-reading the file. On failure, `assert r.status_code == 200, r.text` dumps the body so a fresh agent sees the server's reason immediately.

## Inputs

- ``backend/app/api/routes/teams.py` — invite + join + PATCH + DELETE endpoints from T02 and T03`
- ``backend/app/crud.py` — create_team_invite + accept_team_invite helpers from T02`
- ``backend/app/models.py` — TeamInvite + InviteIssued + MemberRoleUpdate shapes from T01`
- ``backend/tests/api/routes/test_teams.py` — _signup helper + existing test scaffolding to copy or import`
- ``backend/tests/conftest.py` — client + db fixtures`
- ``backend/tests/utils/utils.py` — random_email + random_lower_string`

## Expected Output

- ``backend/tests/api/routes/test_invites.py` — 10+ cases covering issue, join, expired, used, unknown, duplicate, non-admin, atomicity`
- ``backend/tests/api/routes/test_members.py` — 7+ cases covering PATCH role, DELETE member, last-admin guard, personal-team guard`
- ``backend/tests/api/routes/test_teams.py` — handoff-test verification (should already be flipped in T02; re-verify here)`

## Verification

cd backend && uv run pytest tests/ -v && rg -n '501' backend/app/api/routes/teams.py

## Observability Impact

No runtime signals (pure test). Future agent: per-test granularity via `pytest -v` + informative assertion messages dumping response bodies on failure.
