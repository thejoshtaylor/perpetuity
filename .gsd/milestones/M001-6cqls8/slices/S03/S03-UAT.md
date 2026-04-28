# S03: Team invites + membership management — UAT

**Milestone:** M001-6cqls8
**Written:** 2026-04-24T23:46:17.190Z

# S03 — Team Invites + Membership Management UAT

## Scope

Validate the four new endpoints introduced by S03 against real Postgres via FastAPI TestClient. Every test below maps to a concrete pytest case already present in `backend/tests/api/routes/test_invites.py` or `backend/tests/api/routes/test_members.py`. UAT is fully automated — no human walkthrough required (slice plan declares "Human/UAT required: no").

## Preconditions

- Postgres reachable via `backend/.env` (`POSTGRES_SERVER`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` set).
- `cd backend && uv run alembic upgrade head` exits 0 and `alembic_version` table reports `s03_team_invites`.
- `backend/.env` includes `FRONTEND_HOST` (defaults to `http://localhost:5173`); the `url` field in invite responses interpolates this value.
- Test runs from `backend/` directory (`cd backend && uv run pytest tests/`); running from repo root fails with pydantic ValidationError because Settings cannot find `.env`.

## Test Suite

### Group A — Invite Issuance (`POST /api/v1/teams/{team_id}/invite`)

**A1. Happy path: admin issues invite on shared team**
1. Sign up user A → cookie set.
2. POST `/api/v1/teams` with `{name: "Acme"}` → 200, A becomes admin.
3. POST `/api/v1/teams/{acme_id}/invite` → expect 200.
4. Body has `code` (string, ≥ 32 chars), `url` (starts with `FRONTEND_HOST` and ends with `/invite/{code}`), `expires_at` (ISO-8601 ≥ now + 6 days).
   *Pytest: `test_invite_returns_code_url_expires_at`.*

**A2. Personal team rejection**
1. Sign up user A.
2. GET `/api/v1/teams` → personal team id `pid`.
3. POST `/api/v1/teams/{pid}/invite` → expect 403, detail "Cannot invite to personal teams".
   *Pytest: `test_invite_personal_team_returns_403`.*

**A3. Non-member cannot invite**
1. A creates team T. B signs up (no membership).
2. As B, POST `/api/v1/teams/{T}/invite` → expect 403.
   *Pytest: `test_invite_as_non_admin_returns_403`.*

**A4. Member-not-admin cannot invite**
1. A creates T, A invites B, B accepts → B is `member`.
2. As B, POST `/api/v1/teams/{T}/invite` → expect 403.
   *Pytest: `test_invite_as_member_not_admin_returns_403`.*

**A5. Unknown team**
1. A signed in.
2. POST `/api/v1/teams/{random-uuid}/invite` → expect 404.
   *Pytest: covered in `test_teams.py` invite-404 path.*

### Group B — Invite Acceptance (`POST /api/v1/teams/join/{code}`)

**B1. Happy path: B joins T via code**
1. A creates T, POST `/invite` → `{code}`.
2. B signs up.
3. As B, POST `/api/v1/teams/join/{code}` → expect 200, body is TeamWithRole with role=member.
4. As B, GET `/api/v1/teams` → 2 teams (B's personal + T as member).
5. DB: `team_invite.used_at` is set, `used_by = B.id`.
   *Pytest: `test_join_valid_code_adds_member_and_marks_used`.*

**B2. Unknown code returns 404**
1. B signed in.
2. POST `/api/v1/teams/join/garbage` → expect 404.
   *Pytest: `test_join_unknown_code_returns_404`.*

**B3. Expired code returns 410**
1. A creates T, A issues invite, code = X.
2. Direct DB UPDATE: rewind `team_invite.expires_at` to past.
3. As B, POST `/api/v1/teams/join/{X}` → expect 410, detail "Invite expired".
   *Pytest: `test_join_expired_code_returns_410`.*

**B4. Used code returns 410 (one-shot)**
1. A issues code X. B accepts. C signs up.
2. As C, POST `/api/v1/teams/join/{X}` → expect 410, detail "Invite already used".
   *Pytest: `test_join_used_code_returns_410`.*

**B5. Duplicate member returns 409**
1. A issues code X for T (A is already admin).
2. As A, POST `/api/v1/teams/join/{X}` → expect 409, detail "Already a member".
   *Pytest: `test_join_duplicate_member_returns_409`.*

**B6. Atomicity: rollback leaves invite unused**
1. A issues code X.
2. monkeypatch `teams_route.crud.accept_team_invite` to raise mid-transaction.
3. Use `TestClient(app, raise_server_exceptions=False)`.
4. As B, POST `/api/v1/teams/join/{X}` → expect 500.
5. DB: `team_invite.used_at` is still NULL; no `team_member` row exists for (B, T).
   *Pytest: `test_join_atomicity_on_membership_insert_failure`.*

**B7. Unauthenticated rejection**
1. POST `/api/v1/teams/join/{any}` with no cookie → expect 401.
   *Pytest: covered in `test_teams.py` join-401 path.*

### Group C — Role Management (`PATCH /api/v1/teams/{team_id}/members/{user_id}/role`)

**C1. Promote member to admin**
1. A creates T, invites B, B joins (member).
2. As A, PATCH `/api/v1/teams/{T}/members/{B}/role` body `{role: "admin"}` → expect 200, body is TeamWithRole(role=admin).
3. As B, GET `/api/v1/teams` → role on T is now admin.
   *Pytest: `test_patch_role_promotes_member_to_admin`.*

**C2. Demote admin to member**
1. After C1, as A PATCH `{role: "member"}` on B → expect 200.
2. As B, GET `/api/v1/teams` → role on T is back to member.
   *Pytest: `test_patch_role_demotes_admin_to_member`.*

**C3. Non-admin cannot PATCH role**
1. A creates T, invites B, B joins.
2. As B (member), PATCH A's role to "member" → expect 403.
   *Pytest: `test_patch_role_as_non_admin_returns_403`.*

**C4. Last-admin demotion blocked**
1. A is sole admin of T.
2. As A, PATCH `/api/v1/teams/{T}/members/{A}/role` `{role: "member"}` → expect 400, detail "Cannot demote the last admin".
   *Pytest: `test_patch_role_demoting_last_admin_returns_400`.*

**C5. Unknown target**
1. A is admin of T.
2. As A, PATCH `/api/v1/teams/{T}/members/{random-uuid}/role` → expect 404.
   *Pytest: `test_patch_role_unknown_target_returns_404`.*

**C6. Invalid role enum**
1. As A, PATCH `{role: "owner"}` on a valid member → expect 422 (FastAPI Pydantic validator).
   *Pytest: `test_patch_role_invalid_body_returns_422`.*

### Group D — Member Removal (`DELETE /api/v1/teams/{team_id}/members/{user_id}`)

**D1. Remove member returns 204**
1. A creates T, invites B, B joins.
2. As A, DELETE `/api/v1/teams/{T}/members/{B}` → expect 204, no body.
3. As B, GET `/api/v1/teams` → only personal team remains.
   *Pytest: `test_delete_member_removes_row_returns_204`.*

**D2. Cannot remove last admin**
1. A is sole admin of T.
2. As A, DELETE `/api/v1/teams/{T}/members/{A}` → expect 400, detail "Cannot remove the last admin".
   *Pytest: `test_delete_last_admin_returns_400`.*

**D3. Cannot remove from personal team**
1. A signed up, has personal team P.
2. As A, DELETE `/api/v1/teams/{P}/members/{A}` → expect 400, detail "Cannot remove members from personal teams".
   *Pytest: `test_delete_on_personal_team_returns_400`.*

### Group E — Migration Round-Trip

**E1. Forward migration creates table**
1. `alembic upgrade head` from `s02_team_columns`.
2. Confirm `team_invite` table exists with all 8 columns and correct nullability.
3. Confirm `pg_index.indisunique = true` for `ix_team_invite_code`.
4. Insert with bogus `team_id` raises FK IntegrityError.
   *Pytest: `test_s03_upgrade_creates_team_invite`, `test_s03_duplicate_code_fails_integrity`.*

**E2. Downgrade restores prior schema**
1. `alembic downgrade s02_team_columns`.
2. Confirm `team_invite` table and `ix_team_invite_code` are gone.
   *Pytest: `test_s03_downgrade_drops_team_invite`.*

## Run Command

```bash
cd backend && uv run pytest tests/ -v
```

Expected: **125 passed**, 0 failed. Of these, **19** are new in S03 (10 invites + 9 members) plus **3** migration tests.

## Pass Criteria

All 22 S03-introduced tests must pass. The S02 baseline (93 tests) must remain green — proving the `_assert_caller_is_team_admin` refactor of `invite_to_team` did not regress S02 behavior. Full suite green = slice ships.

## Edge Cases Already Covered

- **Self-promotion to admin:** blocked because only admins can call PATCH; a member calling PATCH on themselves gets 403 before the role-update logic.
- **Inviter joining own invite:** A invites for T (A is admin) → A POSTs `/teams/join/{code}` → 409 duplicate_member (A is already an admin row). Verified in B5.
- **Replay attack on invite URL:** TTL + one-shot `used_at` + 410 on second use blocks replay. Verified in B3 + B4.
- **Brute-force code enumeration:** uniform 404 on unknown codes; 32-char urlsafe (~190 bits entropy) makes guessing infeasible. No information leak between unknown vs expired vs used in HTTP body.
- **Lockout via self-demotion:** Last-admin guard. Verified in C4.
- **Race on concurrent accept:** Atomic single-transaction insert + mark-used; if the membership insert fails, the entire transaction rolls back so the invite stays usable. Verified in B6.

## Known Limitations

- `team_invite` rows are not garbage-collected — used and expired invites accumulate. A future cleanup slice (post-M001) should add a periodic prune job. Documented as a Known Limitation in T02-SUMMARY; not blocking S03 closure.
- Brute-force rate-limiting on `/teams/join/{code}` is not in scope for S03 — entropy alone provides the protection. If S05 or later observes abuse, add per-IP rate limiting at the FastAPI middleware layer.
