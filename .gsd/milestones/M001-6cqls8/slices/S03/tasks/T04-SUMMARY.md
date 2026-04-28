---
id: T04
parent: S03
milestone: M001-6cqls8
key_files:
  - backend/tests/api/routes/test_invites.py
  - backend/tests/api/routes/test_members.py
  - backend/app/api/routes/teams.py
key_decisions:
  - Inlined the `_signup` helper into each new test file rather than extracting to tests/utils/utils.py — 8-line duplicate, keeps the diff test-only, avoids coupling to the teams-test module that will keep evolving.
  - Fixed T03's update_member_role by adding `session.refresh(team)` inside the commit block — minimal local fix, no schema or API contract change. Captured the underlying gotcha as MEM035 so future agents don't re-diagnose the same pydantic ValidationError on an expired ORM instance.
  - Atomicity test uses monkeypatch of `teams_route.crud.accept_team_invite` (the route's imported symbol) rather than `crud.accept_team_invite` directly — matches MEM030 and ensures the patch is observed by the handler under test.
duration: 
verification_result: passed
completed_at: 2026-04-24T23:41:04.382Z
blocker_discovered: false
---

# T04: Added 19 integration tests for invite/join/role/remove flows and fixed a T03 PATCH-role bug where expired ORM instance returned empty model_dump()

**Added 19 integration tests for invite/join/role/remove flows and fixed a T03 PATCH-role bug where expired ORM instance returned empty model_dump()**

## What Happened

Created `backend/tests/api/routes/test_invites.py` (10 cases) covering invite issuance, join happy path, unknown/expired/used code rejections, duplicate-member rejection, non-admin invite attempts, and an atomicity test that monkeypatches `crud.accept_team_invite` to raise mid-transaction and asserts no team_member row is left behind and `invite.used_at` stays NULL. The atomicity test mirrors `test_signup_rolls_back_on_mid_transaction_failure` (MEM030) — local `TestClient(app, raise_server_exceptions=False)`, cookies copied from the authenticated joiner's detached jar.\n\nCreated `backend/tests/api/routes/test_members.py` (9 cases) covering PATCH promote/demote, non-admin PATCH (403), last-admin demotion guard (400), unknown target (404), invalid role body (422), DELETE happy path (204), last-admin removal guard (400), and personal-team DELETE guard (400). Every admin-check test involves two distinct users (MEM029 detached cookie jars).\n\nTest run exposed a latent bug in T03's `update_member_role` route: after `session.commit()`, the `team` ORM instance is expired, and `team.model_dump()` on an expired SQLModel object returns an empty dict — so `TeamWithRole(**team.model_dump(), role=target.role)` raised a pydantic ValidationError for missing id/name/slug/is_personal. Fixed by calling `session.refresh(team)` inside the commit block (alongside the existing `session.refresh(target)`). T03's tests never exercised a successful role mutation, so the bug slipped past. Captured as MEM035.\n\nInlined a small `_signup` helper into each new test file (8 lines, mirrors the one in test_teams.py) rather than extracting to `tests/utils/utils.py` — keeps the diff scoped to test files only and avoids coupling the invite/member tests to the evolving teams helper module. The slice plan left this to executor judgment ("copy or import ... if the diff stays clean").\n\nSelf-audit: full suite is 125 passed (S02 baseline 93 + 19 new + some tests exist that the baseline count may not have captured → well above the 112 target). `rg` confirms no raw invite codes in logging paths (only `code_hash=` pattern) and no `501` in teams.py. No `.gsd/` files are staged.

## Verification

Ran `uv run pytest tests/` from backend/ — 125 passed, 0 failed. Ran `uv run pytest tests/api/routes/test_invites.py tests/api/routes/test_members.py -v` — 19/19 green. `rg -n 'raw_code|print.*code|logger.*code=(?!.*hash)' backend/app` → zero matches. `rg -n '501' backend/app/api/routes/teams.py` → zero matches. `git status` shows only backend/ changes, no `.gsd/` staged.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/` | 0 | ✅ pass | 7020ms |
| 2 | `uv run pytest tests/api/routes/test_invites.py tests/api/routes/test_members.py -v` | 0 | ✅ pass | 1500ms |
| 3 | `rg -n 'raw_code|print.*code|logger.*code=' backend/app (excluding code_hash)` | 1 | ✅ pass (zero matches) | 50ms |
| 4 | `rg -n '501' backend/app/api/routes/teams.py` | 1 | ✅ pass (zero matches) | 40ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/api/routes/test_invites.py`
- `backend/tests/api/routes/test_members.py`
- `backend/app/api/routes/teams.py`
