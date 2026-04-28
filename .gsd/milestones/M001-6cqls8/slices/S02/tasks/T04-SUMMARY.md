---
id: T04
parent: S02
milestone: M001-6cqls8
key_files:
  - backend/tests/api/routes/test_teams.py
  - backend/tests/api/routes/test_auth.py
key_decisions:
  - Used a local _signup helper returning a detached httpx.Cookies jar so cross-user isolation (test 8) and slug-collision (test 9) tests can authenticate two distinct users through the same module-scoped TestClient without Set-Cookie jar collisions — matches the MEM015 pattern.
  - Test 7 (501 stub) includes an explicit assertion message instructing future S03 executors that flipping to 200 means this test needs updating — the test is the designed handoff signal from S02 to S03.
  - Interpreted the plan's 'rg is_superuser must return zero' literally would be impossible because the S01 migration file is named 'drop is_superuser' and must reference the column; instead verified zero runtime references in app code + kept the legitimate migration/historical references. Same spirit, satisfiable check.
duration: 
verification_result: passed
completed_at: 2026-04-24T23:16:57.478Z
blocker_discovered: false
---

# T04: Integration tests for teams router (9 cases) + superuser personal-team bootstrap assertion; full suite 93/93 green

**Integration tests for teams router (9 cases) + superuser personal-team bootstrap assertion; full suite 93/93 green**

## What Happened

Added backend/tests/api/routes/test_teams.py covering all 9 slice-verification cases: (1) GET /teams without cookie → 401; (2) post-signup the caller sees exactly their personal team with role=admin and slug matching [a-z0-9-]+; (3) POST /teams {Widgets Inc} creates a non-personal admin team with slug prefix 'widgets-inc-' and the next GET returns 2 teams; (4) missing name → 422; (5) 256-char name → 422; (6) invite on personal team → 403 with 'Cannot invite to personal teams'; (7) invite on non-personal team → 501 stub (intentional red-flag bait for S03); (8) cross-user isolation — user B cannot see user A's team; (9) slug-collision — two users posting {name: 'Research'} both succeed with distinct suffixes.

Appended `test_superuser_bootstrap_has_personal_team` to test_auth.py — cross-checks that T02's init_db wiring actually ran by asserting the FIRST_SUPERUSER has exactly one TeamMember row with role=admin on an is_personal=True team.

Two implementation nuances worth recording: the `client` fixture is module-scoped and persists cookies across tests, so I wrote a `_signup` helper that returns a *detached* `httpx.Cookies` jar (and clears the TestClient's jar before the next signup). Tests 8 and 9 both sign up two users in one test — each needs its own cookie jar passed explicitly via `cookies=` to avoid Set-Cookie collisions. This matches the pattern in MEM015 (existing conftest cookie fixtures use the same snapshot approach). The 501 assertion in test 7 includes an explicit failure message instructing future S03 executors that flipping to 200 means this test needs updating — that's the designed handoff signal.

Self-audit results: Full suite `cd backend && uv run pytest tests/ -v` → 93 passed, 0 failed, 4.50s. `rg -n 'is_superuser' backend/app backend/tests` returns matches only in (a) S01 migration file itself (which is named "drop is_superuser" and must reference the column to drop it + restore it in downgrade), (b) the S01 migration tests that verify the drop/restore, (c) the initial e2412789c190 migration (historical baseline), and (d) test_auth.py's explicit negative assertion that `is_superuser` is NOT in the /users/me response body. Zero references in app runtime code — old auth is fully gone. `rg -n 'Bearer ' backend/app` returns zero matches. Git status confirms `.gsd/` is not staged (only backend/tests/api/routes/test_auth.py modified + test_teams.py untracked, both expected).

Slice must-haves satisfied by concrete tests: atomic personal team on signup → test_signup_creates_personal_team (T02) + test_get_teams_after_signup_returns_only_personal_team (T04). GET /teams returns only caller's teams with role → test 2 + test 8. POST /teams creates non-personal admin team → test 3 + test 9. POST /teams/{id}/invite returns 403 on personal teams → test 6. 422 validation → tests 4 & 5. Signup atomicity → test_signup_rolls_back_on_mid_transaction_failure (T02). S03 boundary contract → test 7 (501 stub).

## Verification

Ran `cd backend && uv run pytest tests/ -v` twice — both runs 93 passed, 0 failed. Ran diff-scan `rg -n 'is_superuser' backend/app backend/tests | rg -v '/alembic/versions/|/tests/migrations/|tests/api/routes/test_auth.py'` → zero matches (all surviving references are migration code or a negative assertion). Ran `rg -n 'Bearer ' backend/app` → zero matches. Verified `.gsd/` is not staged via `git status --short`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/ -v` | 0 | pass | 4500ms |
| 2 | `cd backend && uv run pytest tests/api/routes/test_teams.py -v` | 0 | pass | 1200ms |
| 3 | `rg -n 'Bearer ' backend/app` | 1 | pass | 50ms |
| 4 | `rg -n 'is_superuser' backend/app backend/tests | rg -v '/alembic/versions/|/tests/migrations/|tests/api/routes/test_auth.py'` | 1 | pass | 80ms |
| 5 | `git status --short (confirms no .gsd staged)` | 0 | pass | 30ms |

## Deviations

None of substance. Strict literal interpretation of the `is_superuser` diff-scan would fail on legitimate migration files (S01 upgrade/downgrade, baseline migration) and migration tests — see keyDecisions for the adjusted interpretation. No app runtime code references `is_superuser`.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/api/routes/test_teams.py`
- `backend/tests/api/routes/test_auth.py`
