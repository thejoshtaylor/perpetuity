---
estimated_steps: 20
estimated_files: 2
skills_used: []
---

# T04: Integration tests for teams router + self-audit full-suite run

Add `backend/tests/api/routes/test_teams.py` with the slice-level verification checks and run the full suite to prove no regressions. This task is the slice's objective stopping condition — when these tests + S01's tests + T01/T02 tests all pass, S02 is demonstrably done.

**Test inventory (test_teams.py — all against real Postgres via existing `client` + `db` fixtures):**

1. `test_get_teams_without_cookie_returns_401` — client.cookies.clear(); GET /teams → 401 'Not authenticated'.

2. `test_get_teams_after_signup_returns_only_personal_team` — signup fresh user → GET /teams returns 1 item with is_personal=true, role=admin, name matches email-local-part derivation, slug non-empty and matches [a-z0-9-]+.

3. `test_post_teams_creates_non_personal_team_with_creator_as_admin` — signup → POST /teams {name: 'Widgets Inc'} → 200, response has is_personal=false, role='admin', slug starts with 'widgets-inc-'. Next GET /teams returns 2 teams.

4. `test_post_teams_missing_name_returns_422` — POST /teams with empty body → 422.

5. `test_post_teams_name_too_long_returns_422` — 256-char name → 422.

6. `test_invite_on_personal_team_returns_403` — signup → get personal team id via GET /teams → POST /teams/{personal_id}/invite → 403 with detail 'Cannot invite to personal teams'.

7. `test_invite_on_non_personal_team_returns_501_stub` — signup, POST /teams to make a non-personal team, POST /teams/{id}/invite → 501 (stub — S03 will change to 200). This test intentionally encodes the stub contract so the S03 executor sees it flip red when they wire real invites — that's the test telling them to update this assertion, which is the expected handoff signal.

8. `test_get_teams_does_not_leak_other_users_teams` — user A signs up, creates Team X. User B signs up (gets only their own personal team). GET /teams as B → only 1 team (personal), Team X is absent.

9. `test_slug_collision_on_identical_names_still_succeeds` — two users each POST /teams {name: 'Research'} → both succeed, both get admin role, slugs differ (suffixes).

**Also extend `test_auth.py`** (T02 already adds two tests; T04 adds a third cross-check):
- `test_superuser_bootstrap_has_personal_team` — after init_db runs (which happens in the session-scoped `db` fixture), the FIRST_SUPERUSER has exactly one TeamMember row with TeamRole.admin on an is_personal=True team. Proves T02's db.py change wired correctly.

**Self-audit step (MANDATORY — done in this task before claiming slice complete):**
- Walk through each must-have from the slice goal and point at a specific test that proves it.
- Run `cd backend && uv run pytest tests/ -v` — record pass count in T04's summary.
- Diff-scan: `rg -n 'is_superuser' backend/app backend/tests` must return zero. `rg -n 'Bearer ' backend/app` must return zero (old auth fully gone).
- Confirm `.gsd/` files are not staged for git commit.

**Negative Tests focus:**
Other-user isolation (test 8) is the security-critical assertion — it proves the GET /teams query's WHERE clause isn't accidentally removed during refactors. Slug collision (test 9) proves the suffix strategy works under realistic name duplication. Stub-501 (test 7) is intentional red-flag bait for S03.

## Inputs

- ``backend/app/api/routes/teams.py` — router under test (from T03).`
- ``backend/app/models.py` — TeamPublic, TeamWithRole shapes asserted in response bodies.`
- ``backend/tests/conftest.py` — existing `client`, `db`, `superuser_cookies`, `normal_user_cookies` fixtures (S01 — reuse).`
- ``backend/tests/utils/utils.py` — existing `random_email`, `random_lower_string`, `get_superuser_cookies` helpers.`
- ``backend/tests/api/routes/test_auth.py` — extend with superuser-bootstrap personal-team assertion.`

## Expected Output

- ``backend/tests/api/routes/test_teams.py` — new file with 9 test cases covering auth, personal-team visibility, team creation, 422 validation, 403 on personal invite, 501 stub on non-personal invite, cross-user isolation, slug collision.`
- ``backend/tests/api/routes/test_auth.py` — appended `test_superuser_bootstrap_has_personal_team` asserting init_db created personal team for FIRST_SUPERUSER.`

## Verification

cd backend && uv run pytest tests/ -v (expect all S01 tests + all T02 signup atomicity tests + all T04 team router tests passing; zero regressions) && rg -n 'is_superuser' backend/app backend/tests (expect no matches)

## Observability Impact

No runtime-side signals added in this task (it is pure test work). Future-agent inspection: test file names are self-documenting; `pytest tests/api/routes/test_teams.py -v` lists every test case, so a debug session can target an individual failing case without reading the file.
