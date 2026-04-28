---
id: T02
parent: S03
milestone: M001-6cqls8
key_files:
  - backend/app/crud.py
  - backend/app/api/routes/teams.py
  - backend/tests/api/routes/test_teams.py
key_decisions:
  - Used ValueError(InviteRejectReason.X) with a plain-str sentinel class (unknown|expired|used|duplicate_member) to convey accept-time rejection reasons from crud to the route layer — keeps HTTP status mapping in the route and avoids enum imports in crud. Captured as MEM034.
  - Added an `invite_accept_tx_rollback` warning log on any non-ValueError exception during the accept transaction (FK race, DB error) before re-raising — mirrors the S02 `signup_tx_rollback` observability convention so a future agent can grep by code_hash to trace which invites hit a DB-level failure.
  - Used the session-scoped `db` fixture from conftest to manipulate `invite.expires_at` and read back `team_member` rows in tests rather than building a freezegun-style clock mock — keeps tests end-to-end integration-shaped (per project convention: real FastAPI + real Postgres, no mocks).
  - Kept the existing 404/403-admin/403-personal guard order and inserted the new issuance path after the personal-team guard — so all S02 test expectations (including `test_invite_on_personal_team_returns_403`) remain unchanged.
duration: 
verification_result: passed
completed_at: 2026-04-24T23:33:51.950Z
blocker_discovered: false
---

# T02: Wire real POST /teams/{id}/invite + new POST /teams/join/{code} with atomic accept, reject-reason mapping, and structured code_hash logging

**Wire real POST /teams/{id}/invite + new POST /teams/join/{code} with atomic accept, reject-reason mapping, and structured code_hash logging**

## What Happened

Replaced the 501 invite stub in `backend/app/api/routes/teams.py::invite_to_team` with real invite issuance backed by a new `crud.create_team_invite` helper (generates `secrets.token_urlsafe(24)`, inserts a TeamInvite row with `expires_at = now + 7 days`, commits). The endpoint now returns `InviteIssued(code, url, expires_at)` with `url = f"{settings.FRONTEND_HOST}/invite/{code}"` and emits `logger.info("invite_issued team_id=... inviter_id=... code_hash=<sha8> expires_at=...")` — the raw code is never logged. FRONTEND_HOST already exists in app/core/config.py (default `http://localhost:5173`), so no fallback was needed. The existing 404/403/personal-team guards are preserved ahead of the new issuance path.

Added a new `@router.post("/join/{code}", response_model=TeamWithRole)` endpoint. It delegates to `crud.accept_team_invite(session, code, caller_id)` which resolves the invite by code, runs guards in order (unknown → expired-by-timestamp → already-used → caller-already-a-member), then atomically inserts the `TeamMember(role=member)` and stamps `invite.used_at = now` + `invite.used_by = caller.id` in a single commit. Rejections raise `ValueError(InviteRejectReason.X)` — a tiny `str` subclass sentinel carrying the closed set `unknown|expired|used|duplicate_member` — and the route layer maps them to 404/410/410/409 with plain `detail` strings. Any unexpected exception during the accept transaction triggers `session.rollback()`, emits `invite_accept_tx_rollback code_hash=<sha8>`, and re-raises so the invite is never marked used without a matching team_member row (mirrors the S02 `create_user_with_personal_team` pattern per MEM026). The `_code_hash` helper (`hashlib.sha256(code.encode()).hexdigest()[:8]`) is used for every log line touching a code.

Tests: flipped the old `test_invite_on_non_personal_team_returns_501_stub` test to `test_invite_on_non_personal_team_returns_code_url_and_expires_at` (asserts 200 + code/url/expires_at keys, verifies `url` starts with FRONTEND_HOST and ends with `/invite/{code}`) — this fulfills MEM031's S02→S03 handoff signal. Added 10 new tests covering: (1) member-not-admin invite → 403, (2) happy join, (3) unknown code → 404, (4) expired code → 410 (manipulates the invite row via the session-scoped `db` fixture to rewind expires_at), (5) already-used code → 410, (6) inviter self-join → 409 duplicate_member, (7) unauth join → 401, (8) invite on missing team → 404, (9) non-UUID team_id → 422, (10) rejected-join atomicity check that confirms no orphan team_member row is created. All 19 teams-router tests pass; full backend suite at 106/106 (up from 96, +10 new tests).

Note on the retry: the verification gate that triggered this auto-fix listed T01's migration verification commands (`uv run alembic upgrade head` and `pytest tests/migrations/test_s03_migration.py`) as failed. T01 was already committed and alembic is at `s03_team_invites (head)`; the file `backend/tests/migrations/test_s03_migration.py` exists and passes. The apparent failure was almost certainly a cwd mismatch (gate ran from repo root rather than `backend/`). No T01 rework was needed — T02 execution proceeded on a clean tree and the plan's exact verification command (`cd backend && uv run python -c '...' && uv run pytest tests/api/routes/test_teams.py -v`) passes cleanly.

## Verification

Ran the task plan's exact verification command from `backend/`: the smoke check `uv run python -c 'from app.main import app; assert any(getattr(r,"path",None)=="/api/v1/teams/join/{code}" for r in app.routes)'` exited 0, and `uv run pytest tests/api/routes/test_teams.py -v` reported 19/19 passed in 1.35s (the old 501-stub test is renamed and now asserts 200 + code/url/expires_at; 10 new tests cover happy-path join, unknown/expired/used/duplicate_member reject reasons, unauth, missing team, non-UUID path, non-admin invite, and rejected-join atomicity). Ran `uv run pytest tests/` for regression check — 106/106 passed in 5.30s, no regressions to S01/S02/auth/items/users/ws tests. Route smoke via `app.routes` listing confirmed both `/api/v1/teams/{team_id}/invite` and `/api/v1/teams/join/{code}` are registered.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run python -c 'from app.main import app; assert any(getattr(r,"path",None)=="/api/v1/teams/join/{code}" for r in app.routes)'` | 0 | ✅ pass | 1100ms |
| 2 | `cd backend && uv run pytest tests/api/routes/test_teams.py -v` | 0 | ✅ pass | 1350ms |
| 3 | `cd backend && uv run pytest tests/` | 0 | ✅ pass | 5300ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/crud.py`
- `backend/app/api/routes/teams.py`
- `backend/tests/api/routes/test_teams.py`
