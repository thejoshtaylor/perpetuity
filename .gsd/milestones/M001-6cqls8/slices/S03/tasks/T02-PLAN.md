---
estimated_steps: 45
estimated_files: 3
skills_used: []
---

# T02: Replace 501 invite stub with real POST /teams/{id}/invite + add POST /teams/join/{code} with atomic accept

Wire real invite issuance and acceptance on top of the T01 TeamInvite model. Replace the existing 501 body in `backend/app/api/routes/teams.py::invite_to_team` with real invite creation, and add a new `POST /api/v1/teams/join/{code}` endpoint in the same router. Add two CRUD helpers to `backend/app/crud.py`: `create_team_invite(*, session, team_id, created_by, ttl_seconds=604800) -> TeamInvite` and `accept_team_invite(*, session, code, user) -> tuple[Team, TeamMember]` that handles code lookup, expiry check, used-check, duplicate-membership check, and atomic insert + mark-used in one transaction (follow the S02 `create_user_with_personal_team` atomic-flush-then-commit pattern — see MEM026).

Behavioral contract (POST /api/v1/teams/{team_id}/invite):
- 404 if team missing.
- 403 if caller is not a member, or is a member but not an admin.
- 403 with detail 'Cannot invite to personal teams' if team.is_personal (retain S02 behavior + exact error string; the existing test expects this).
- 200 with body `{code: str, url: str, expires_at: ISO-8601}` otherwise. `code = secrets.token_urlsafe(24)` (≈32 chars); `url = f'{settings.FRONTEND_HOST}/invite/{code}'`; `expires_at = now + 7 days`.
- Structured log: `logger.info('invite_issued team_id=%s inviter_id=%s code_hash=%s expires_at=%s', ...)` where `code_hash = hashlib.sha256(code.encode()).hexdigest()[:8]` — NEVER log the raw code.

Behavioral contract (POST /api/v1/teams/join/{code}):
- Requires auth cookie via `CurrentUser` dep.
- 404 uniform if code unknown — do NOT distinguish unknown from expired in the HTTP response (unknown returns 404, expired/used returns 410 only for valid but spent codes, so attackers can't probe for unknown-vs-expired directly). Actually: resolve code → if none, 404; if expires_at < now, 410 'Invite expired'; if used_at is not None, 410 'Invite already used'; if caller is already a member of invite.team_id, 409 'Already a member'; otherwise atomically `session.add(TeamMember(user_id=caller.id, team_id=invite.team_id, role=TeamRole.member))`, set `invite.used_at = now()` and `invite.used_by = caller.id`, `session.commit()`. Return TeamWithRole for the joined team.
- Rejection logs: `logger.info('invite_rejected reason=%s code_hash=%s caller_id=%s', reason, code_hash, caller.id)` with reason ∈ {unknown, expired, used, duplicate_member}.
- Success log: `logger.info('invite_accepted team_id=%s user_id=%s code_hash=%s', team.id, caller.id, code_hash)`.

Atomicity: the accept flow MUST be a single transaction. If the TeamMember insert fails (e.g. FK violation or race with a concurrent accept by the same user), `session.rollback()` and re-raise — do NOT leave the invite marked used without a corresponding membership. Mirror the try/except in `create_user_with_personal_team`.

Must-haves:
- `_code_hash(code)` helper in `app/api/routes/teams.py` — sha256 hex first 8 chars; use wherever logging.
- `import secrets` and `import hashlib` at top of teams.py.
- `settings.FRONTEND_HOST` — reuse existing config (verify it exists; it is used elsewhere for email links). If missing, fall back to `settings.SERVER_HOST` or hardcode `http://localhost:5173` but leave a comment referencing the S04 frontend host for review.
- TTL constant `INVITE_TTL_SECONDS = 7 * 24 * 60 * 60` at module scope.
- Use `datetime.now(timezone.utc)` (match project convention — see `models.get_datetime_utc`).
- Register the new `/join/{code}` endpoint on the existing `teams` router (path: `/join/{code}` — the router prefix handles `/teams`).
- All HTTP error responses carry a plain `detail` string — no dict leakage of internals.

Steps:
1. In `app/crud.py` add `create_team_invite(*, session, team_id, created_by, ttl_seconds=INVITE_TTL_SECONDS)` that inserts TeamInvite, commits once, returns the refreshed row.
2. In `app/crud.py` add `accept_team_invite(*, session, code, caller_id)` that resolves invite by code, runs guards (expired/used/duplicate_member), inserts TeamMember, marks used_at/used_by, commits once; on failure rolls back and re-raises a typed exception so the route layer can map to HTTP. Use a small module-level sentinel enum `InviteRejectReason` or pass through via raised `ValueError('reason')` — pick whichever is simpler and document in the docstring.
3. In `app/api/routes/teams.py` replace the 501 branch in `invite_to_team` with: `invite = crud.create_team_invite(session=session, team_id=team.id, created_by=current_user.id)`; log `invite_issued`; return `InviteIssued(code=invite.code, url=f'{settings.FRONTEND_HOST}/invite/{invite.code}', expires_at=invite.expires_at)`.
4. In `app/api/routes/teams.py` add `@router.post('/join/{code}')` that calls `crud.accept_team_invite` and maps exceptions to 404/410/409. Return TeamWithRole.
5. Manual smoke: `cd backend && uv run python -c 'from app.main import app; print(sorted({r.path for r in app.routes if "teams" in getattr(r,"path","")}))'` lists both /api/v1/teams/{team_id}/invite and /api/v1/teams/join/{code}.
6. Update `backend/tests/api/routes/test_teams.py::test_invite_on_non_personal_team_returns_501_stub` — rename to `test_invite_on_non_personal_team_returns_code_url_and_expires_at` and change assertion: `assert r2.status_code == 200` plus `assert 'code' in r2.json() and 'url' in r2.json() and 'expires_at' in r2.json()`. Keep the personal-team 403 test unchanged. This flip fulfills MEM031's handoff.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| Postgres (insert TeamInvite/TeamMember) | rollback + re-raise → 500 via FastAPI; log `invite_issue_tx_rollback` or `invite_accept_tx_rollback` with code_hash | N/A (synchronous) | N/A |
| settings.FRONTEND_HOST missing | fall back to hardcoded dev host with comment; TODO noted for S04 | N/A | N/A |

Load Profile:
- Shared resources: DB connection pool, `team_invite` table (grows unboundedly if never garbage-collected — noted as a Known Limitation for a later cleanup slice).
- Per-operation cost: 1 insert per issue; 1 read + 1 insert + 1 update per accept, one transaction.
- 10x breakpoint: at thousands of outstanding invites per team, GET-all-invites (not exposed yet) would need pagination — not a concern for S03.

Negative Tests:
- Malformed inputs: non-UUID team_id in path → 422 (FastAPI validator). Unknown code in /join/{code} → 404. Empty body on invite → accepted (no body required).
- Error paths: accept when invite is expired (expires_at < now) → 410. Accept when invite.used_at is set → 410. Accept when caller is already a TeamMember for invite.team_id → 409. Invite to a team the caller is not an admin of → 403.
- Boundary conditions: invite to a team where caller is a member but role=member → 403. Self-accept by inviter (they are already an admin) → 409 duplicate_member — intentional, inviter should not re-join their own team.

Observability Impact:
- Signals added: `invite_issued`, `invite_accepted`, `invite_rejected reason=<...>`, `invite_issue_tx_rollback`, `invite_accept_tx_rollback`.
- Future agent inspects this via: `SELECT code, team_id, created_by, expires_at, used_at, used_by FROM team_invite ORDER BY created_at DESC LIMIT 20;` in psql, or by grepping the structured log for `code_hash=<prefix>` to trace a single invite end-to-end.
- Failure state exposed: the `team_invite` row alone shows issuance status; the presence/absence of a paired `team_member` row proves whether acceptance closed atomically.

## Inputs

- ``backend/app/models.py` — TeamInvite + InviteIssued shapes from T01`
- ``backend/app/alembic/versions/s03_team_invites.py` — migration head from T01`
- ``backend/app/api/routes/teams.py` — existing invite_to_team stub to replace`
- ``backend/app/crud.py` — prior helpers (create_team_with_admin, create_user_with_personal_team) for atomic-transaction pattern`
- ``backend/tests/api/routes/test_teams.py` — existing 501 stub test to flip to 200`
- ``backend/app/core/config.py` — settings for FRONTEND_HOST lookup`

## Expected Output

- ``backend/app/crud.py` — create_team_invite + accept_team_invite helpers`
- ``backend/app/api/routes/teams.py` — real POST /{id}/invite body + new POST /join/{code} route + structured logging`
- ``backend/tests/api/routes/test_teams.py` — 501 stub test flipped to assert 200 with code/url/expires_at`

## Verification

cd backend && uv run python -c 'from app.main import app; assert any(getattr(r,"path",None)=="/api/v1/teams/join/{code}" for r in app.routes)' && uv run pytest tests/api/routes/test_teams.py -v

## Observability Impact

INFO `invite_issued team_id=<uuid> inviter_id=<uuid> code_hash=<sha8> expires_at=<iso>` on issue; INFO `invite_accepted team_id=<uuid> user_id=<uuid> code_hash=<sha8>` on success; INFO `invite_rejected reason=<unknown|expired|used|duplicate_member> code_hash=<sha8> caller_id=<uuid>`; WARNING `invite_issue_tx_rollback` / `invite_accept_tx_rollback code_hash=<sha8>` on DB failure. Future agent inspects via psql on `team_invite` + `team_member` tables and log grep by code_hash prefix.
