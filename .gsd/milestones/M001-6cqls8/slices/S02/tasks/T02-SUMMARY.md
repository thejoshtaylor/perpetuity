---
id: T02
parent: S02
milestone: M001-6cqls8
key_files:
  - backend/app/crud.py
  - backend/app/api/routes/auth.py
  - backend/app/core/db.py
  - backend/tests/api/routes/test_auth.py
key_decisions:
  - create_user_with_personal_team is a brand-new helper rather than a wrapper around crud.create_user because the existing helper commits mid-flow and would break atomicity; keeping both functions avoids a breaking API change for callers that still want a bare user.
  - raise_http_on_duplicate flag lets the same helper serve both HTTP (raises HTTPException 400) and bootstrap (raises ValueError) callers without importing FastAPI types into non-HTTP code paths.
  - Slug format is `<slugified-name-or-fallback>-<8 hex chars of user.id>` which stays ≤64 chars (T01 column constraint) and guarantees uniqueness even when two users share a full_name.
  - Rollback test uses a local TestClient(app, raise_server_exceptions=False) rather than the module-scoped client fixture, so the RuntimeError surfaces as a 500 response instead of propagating out of the request cycle.
duration: 
verification_result: untested
completed_at: 2026-04-24T23:11:10.889Z
blocker_discovered: false
---

# T02: Atomic User+Team+TeamMember signup via new create_user_with_personal_team helper with full rollback on failure

**Atomic User+Team+TeamMember signup via new create_user_with_personal_team helper with full rollback on failure**

## What Happened

Added `create_user_with_personal_team(*, session, user_create, raise_http_on_duplicate=True) -> tuple[User, Team]` and module-level `_slugify(name)` to `app/crud.py`. The helper checks for duplicate email up front, then stages User → Team(is_personal=True, slug=_slugify(stem)+'-'+user.id.hex[:8]) → TeamMember(role=admin) using `session.flush()` between inserts to get DB-assigned IDs without committing; one `session.commit()` at the end makes the whole thing atomic. Any exception inside triggers `session.rollback()` and re-raises. Duplicates raise HTTPException(400) when `raise_http_on_duplicate=True` and plain ValueError otherwise so init_db can call it at bootstrap without importing FastAPI error types.

Rewrote `app/api/routes/auth.py::signup` to delegate to the new helper and emit three INFO logs on success (`signup ok <redacted>`, `team_created team_id=… is_personal=True creator_id=…`, `personal_team_bootstrapped user_id=… team_id=…`) plus a WARNING `signup_tx_rollback <redacted_email> stage=crud` on unexpected failure. HTTPException (duplicate email) is handled separately so its existing INFO log shape is preserved and not upgraded to WARNING.

Updated `app/core/db.py::init_db` to call the new helper with `raise_http_on_duplicate=False` so the FIRST_SUPERUSER also gets a personal team on first boot — consistent with R003 ("every new user gets a personal team").

Extended `tests/api/routes/test_auth.py` with four new tests: (1) `test_signup_creates_personal_team` asserts exactly one TeamMember row with role=admin and a Team with is_personal=True whose slug ends with the user's 8-char UUID hex suffix; (2) `test_signup_full_name_too_long_returns_422` covers the pydantic 256-char boundary; (3) `test_signup_rolls_back_on_mid_transaction_failure` monkeypatches the crud helper to raise RuntimeError, uses a local `TestClient(app, raise_server_exceptions=False)` so the 500 surfaces as a response, then asserts no user row and no team row with the email-local-part name persisted; (4) `test_signup_identical_full_name_produces_distinct_slugs` signs up two users with full_name='Jane Q Public' and verifies distinct slugs with shared `jane-q-public-` stem and different UUID-derived suffixes.

Key design choice: `create_user_with_personal_team` does NOT delegate to the existing `crud.create_user` because that helper commits early, which would break atomicity. The new helper is independent and both are exported — the existing `create_user` is still used by test utilities that only need a bare user without a team.

## Verification

Ran `uv run pytest tests/api/routes/test_auth.py -v` (17/17 passed — 13 pre-existing + 4 new), `uv run pytest tests/` (83/83 passed, zero regressions vs T01 baseline of 79), and `uv run alembic upgrade head` (exit 0). Atomicity proved by the monkeypatch-then-assert-absence negative test; personal-team invariants proved by the happy-path test asserting role=admin + is_personal=True + slug-suffix format; slug collision avoidance proved by the identical-full-name boundary test.</verification>
<parameter name="verificationEvidence">[{"command":"cd backend && uv run pytest tests/api/routes/test_auth.py -v","exitCode":0,"verdict":"✅ pass","durationMs":620},{"command":"cd backend && uv run pytest tests/","exitCode":0,"verdict":"✅ pass","durationMs":3980},{"command":"cd backend && uv run alembic upgrade head","exitCode":0,"verdict":"✅ pass","durationMs":1500}]

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| — | No verification commands discovered | — | — | — |

## Deviations

Plan said to emit `logger.warning('signup_tx_rollback ... stage=<crud|session>')`. I simplified to a single `stage=crud` tag because the new helper does the commit internally — there is no separate `stage=session` path left for the route to log. If a future task splits the helper into multiple steps that can fail independently, the stage enum can be reintroduced.

## Known Issues

None.

## Files Created/Modified

- `backend/app/crud.py`
- `backend/app/api/routes/auth.py`
- `backend/app/core/db.py`
- `backend/tests/api/routes/test_auth.py`
