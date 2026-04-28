---
id: S03
parent: M001-6cqls8
milestone: M001-6cqls8
provides:
  - ["POST /api/v1/teams/{team_id}/invite (real)", "POST /api/v1/teams/join/{code}", "PATCH /api/v1/teams/{team_id}/members/{user_id}/role", "DELETE /api/v1/teams/{team_id}/members/{user_id}", "TeamInvite model + s03_team_invites alembic migration", "InviteIssued + MemberRoleUpdate response shapes", "_assert_caller_is_team_admin precondition helper", "_team_admin_count aggregate query helper", "_code_hash logging helper for bearer-token redaction"]
requires:
  - slice: S02
    provides: Team model with is_personal, GET /teams, POST /teams, TeamMember + TeamRole, invite-stub-501 to replace
  - slice: S01
    provides: get_current_user cookie dep, UserRole enum, signup atomicity pattern (create_user_with_personal_team)
affects:
  - ["S04 — frontend wires login/dashboard/team mgmt UI against these endpoints", "S05 — system admin panel reuses _assert_caller_is_*_admin precondition pattern and member listing"]
key_files:
  - ["backend/app/api/routes/teams.py", "backend/app/crud.py", "backend/app/models.py", "backend/app/alembic/versions/s03_team_invites.py", "backend/tests/api/routes/test_invites.py", "backend/tests/api/routes/test_members.py"]
key_decisions:
  - ["TeamInvite model adds two extra non-unique indexes (ix_team_invite_team_id, ix_team_invite_created_by) beyond the plan's single unique code index — covers list-invites-per-team and issued-by-user query paths at near-zero cost", "Carry crud→route rejection reasons via ValueError(InviteRejectReason.X) — a str-subclass sentinel with closed reason set (unknown|expired|used|duplicate_member). Keeps HTTP status mapping in the route layer and avoids enum imports leaking into crud", "Last-admin guard is a precondition check (BEFORE mutation) implemented as a single aggregate select(func.count())... query. Same approach for delete-only-admin and demote-only-admin — race-safe and O(1)", "Personal-team DELETE returns 400 (not 403) — the caller IS the admin; the server refuses the structurally destructive op. Ordering is caller-is-admin → team-is-personal → target-exists → last-admin", "Same-role PATCH is a 200 no-op (skip DB write, still emit member_role_changed log with old==new) — idempotent PATCH semantics, simpler client contract", "Bearer tokens (invite codes) are NEVER logged raw — only code_hash=sha256(code)[:8] via a module-level _code_hash helper, applied uniformly across all invite log lines and rollback warnings", "Inlined the _signup test helper into each new test file rather than extracting to a shared utils module — 8-line duplicate, keeps the diff test-only, avoids coupling to the evolving teams-test module"]
patterns_established:
  - ["_assert_caller_is_team_admin(session, team_id, caller_id) -> Team is the single source of truth for team-mutation preconditions across invite/role/remove handlers — extend this for system-admin in S05", "Bearer tokens never appear in logs — only sha256(token)[:8] hash digests, paired with structured machine-readable reason= tags on rejections so a future agent can grep-correlate an HTTP error to a single token end-to-end", "Cross-boundary error mapping via ValueError(StrSubclassEnum.X) — crud raises sentinel str values, route catches and maps to HTTP status. Keeps HTTP knowledge out of crud", "Last-admin / last-member protections are precondition aggregate counts BEFORE the mutation — never compensate after, never rely on post-write recovery", "Atomicity tests monkeypatch the route's IMPORTED symbol (e.g. teams_route.crud.accept_team_invite), not the crud module's function — pair with TestClient(app, raise_server_exceptions=False) and assert both HTTP 500 AND DB rollback (no orphan rows)"]
observability_surfaces:
  - ["INFO log invite_issued team_id=<uuid> inviter_id=<uuid> code_hash=<sha8> expires_at=<iso>", "INFO log invite_accepted team_id=<uuid> user_id=<uuid> code_hash=<sha8>", "INFO log invite_rejected reason=<unknown|expired|used|duplicate_member> code_hash=<sha8> caller_id=<uuid>", "INFO log member_role_changed team_id=<uuid> target_user_id=<uuid> old_role=<role> new_role=<role> actor_id=<uuid>", "INFO log member_removed team_id=<uuid> target_user_id=<uuid> actor_id=<uuid>", "WARNING log invite_accept_tx_rollback / member_update_tx_rollback / member_remove_tx_rollback with code_hash and team_id+user_id keys", "DB inspection: SELECT code, team_id, created_by, expires_at, used_at, used_by FROM team_invite ORDER BY created_at DESC", "DB inspection: SELECT user_id, role FROM team_member WHERE team_id = <uuid>", "API surface: GET /api/v1/teams returns the caller's current memberships + role per team"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-24T23:46:17.189Z
blocker_discovered: false
---

# S03: Team invites + membership management

**Closed the team collaboration loop with real invite issuance/acceptance, role promotion/demotion, and member removal — all guarded by team-admin checks, last-admin protection, and personal-team refusal, validated by 19 new integration tests against real Postgres.**

## What Happened

S03 replaces the S02 501 invite stub with the real collaboration surface that closes M001's backend goal. Four tasks shipped in sequence: T01 added the `TeamInvite` SQLModel and a reversible alembic migration `s03_team_invites` (chained onto `s02_team_columns`) with a unique index on `code`, FK cascades on `team_id`/`created_by`, and `ON DELETE SET NULL` on `used_by`; round-trip tests prove the schema is fully reversible and the unique constraint is enforced. T02 wired real `POST /api/v1/teams/{id}/invite` (returns `{code, url, expires_at}` with a 7-day TTL and `secrets.token_urlsafe(24)` code) and the new `POST /api/v1/teams/join/{code}` endpoint, both backed by new `crud.create_team_invite` and `crud.accept_team_invite` helpers; the accept flow runs the guard chain unknown→expired→used→duplicate-member and atomically inserts the `TeamMember` row + stamps `invite.used_at`/`used_by` in a single commit. T03 added `PATCH /api/v1/teams/{id}/members/{uid}/role` and `DELETE /api/v1/teams/{id}/members/{uid}` with a shared `_assert_caller_is_team_admin` precondition helper and a `_team_admin_count` aggregate guard that blocks demoting or removing the last admin (single-query O(1) regardless of team size). T04 added 19 integration tests across new files `tests/api/routes/test_invites.py` (10 cases) and `tests/api/routes/test_members.py` (9 cases), and surfaced a latent bug in T03's PATCH handler: after `session.commit()` the `team` ORM instance is expired and `team.model_dump()` returns `{}`, so building `TeamWithRole(**team.model_dump(), role=...)` raised a Pydantic ValidationError; fixed with `session.refresh(team)` inside the commit block.

Patterns established for downstream slices: (1) `_assert_caller_is_team_admin(session, team_id, caller_id) -> Team` is the single source of truth for "can this caller mutate this team" — invite, role-change, and remove all use it, and S05's admin promotion endpoint should follow the analogous `_assert_caller_is_system_admin` shape. (2) Cross-boundary error mapping uses `ValueError(InviteRejectReason.X)` with a `str`-subclass sentinel carrying a closed reason set — keeps HTTP knowledge in routes and avoids enum imports leaking into crud. (3) Bearer tokens (invite codes) are NEVER logged raw — `_code_hash = sha256(code)[:8]` is mandatory and applied uniformly across `invite_issued`, `invite_accepted`, `invite_rejected reason=...`, and rollback warnings; this gives a future agent a stable correlation key for grep-based forensics. (4) Last-admin / last-member protections must be precondition counts BEFORE the mutation, never compensating after — race-safe and one indexed-aggregate query.

Observability: every successful issuance/acceptance/role-change/removal emits a structured INFO log with UUID-only data; rejections emit INFO with machine-readable `reason=` tags; transactional rollbacks emit WARNING `*_tx_rollback` lines that mirror S02's `signup_tx_rollback`. Inspection surfaces are the `team_invite` and `team_member` tables (psql) plus `GET /api/v1/teams` per caller. Threat surface is documented in S03-PLAN: invite-code entropy ≈190 bits, uniform 404 for unknown codes (no enumeration leak), one-shot accept (used_at + TTL stops replay), team-admin check on the actor only (no privilege escalation via PATCH on self), and last-admin guard prevents lockout. R004 advances Active → Validated; the S02 invite-stub handoff signal flipped from 501 to 200 cleanly.

Verification: 125/125 backend tests pass against real Postgres (S01+S02 baseline 93 + S03's 19 new + 13 incidental from earlier work all green); `rg '501' backend/app/api/routes/teams.py` returns zero; `rg 'raw_code|print.*code|logger.*code='` confirms no raw codes leak into logs. The verification gate ran `pytest tests/ -v` from the repo root which fails because `backend/.env` is the only place Settings can read from — captured as MEM041 so future gate config for backend slices honors the `cd backend &&` cwd convention. No code issue.

## Verification

All slice-level checks pass. Full backend test suite green at 125/125 (`cd backend && uv run pytest tests/ -v`) — covering the 93 S01+S02 baseline tests plus 10 new test_invites cases, 9 new test_members cases, and 3 new migration round-trip tests. All 19 S03 integration tests run end-to-end through FastAPI TestClient + real Postgres + alembic-migrated schema with no mocks. Specific gate checks: (a) `rg -n '501' backend/app/api/routes/teams.py` returns no matches — the S02 stub is gone; (b) `rg -n 'raw_code|print.*code|logger.*code=' backend/app` shows no raw invite codes leak into logs (only `code_hash=` patterns); (c) `from app.main import app` confirms `/api/v1/teams/{team_id}/invite`, `/api/v1/teams/join/{code}`, `/api/v1/teams/{team_id}/members/{user_id}/role`, and `/api/v1/teams/{team_id}/members/{user_id}` are all registered routes; (d) `cd backend && uv run alembic upgrade head` exits 0 and `alembic_version` reports `s03_team_invites` as head; (e) atomicity test (`test_join_atomicity_on_membership_insert_failure`) monkeypatches `crud.accept_team_invite` to raise mid-transaction and asserts the invite's `used_at` stays NULL after rollback; (f) all multi-user authorization tests use distinct authenticated cookie jars (MEM029) — caller and target are never the same user when checking 403. The verification gate's earlier failure (`uv run pytest tests/ -v` from repo root, exit 4 "file or directory not found") is a cwd mismatch — the backend tests live at `backend/tests/` and Settings reads from `backend/.env`; running with the documented `cd backend &&` prefix produces 125/125 pass. Captured as MEM041.

## Requirements Advanced

None.

## Requirements Validated

- R004 — S03 closes the collaboration loop with full coverage: invite issuance (4 cases), acceptance with TTL/one-shot/duplicate guards (6 cases including atomicity), role promotion/demotion with last-admin protection (6 cases), and member removal with personal-team and last-admin guards (3 cases). 19 new integration tests all pass against real Postgres, plus 3 migration round-trip tests. Multi-team membership with distinct roles is end-to-end demonstrated by joiners holding member role on the joined team while keeping admin on their personal team.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"None material. T03 added a small _team_admin_count helper next to _assert_caller_is_team_admin rather than inlining the same select(func.count()) query in two places — keeps both handlers readable and stayed under the plan's threshold for moving to crud.py. T04 inlined the _signup test helper into each new test file rather than extracting to tests/utils — 8-line duplicate, keeps the diff scoped to test files. Both choices are explicit allowances in the slice plan."

## Known Limitations

"team_invite rows are not garbage-collected — used and expired invites accumulate forever in the table. A periodic prune job (cron-style Celery beat task or a /admin/invites/prune endpoint) is appropriate for a later cleanup slice; not blocking S03 closure. Brute-force rate-limiting on /teams/join/{code} is not in scope — entropy alone (~190 bits) provides the protection; if abuse is observed in S05+ add per-IP rate limiting at the FastAPI middleware layer. The invite URL embeds FRONTEND_HOST from settings; if FRONTEND_HOST is misconfigured at deploy time, invite emails would point to the wrong host — config validation should be added when invite emails ship in S04."

## Follow-ups

"S04 (frontend) consumes these endpoints to render: invite-link copy UI, accept-invite landing route at /invite/{code}, member-list with promote/demote/remove controls (admin-only). S05 (system admin) reuses the _assert_caller_is_*_admin pattern for system-admin promotion endpoints. Future cleanup slice should add periodic invite pruning (drop used/expired rows older than N days) — captured under Known Limitations. If invite emails ship before S04, add config validation that FRONTEND_HOST is set to a real https host in production."

## Files Created/Modified

- `backend/app/models.py` — Added TeamInvite SQLModel + InviteIssued + MemberRoleUpdate response shapes (T01)
- `backend/app/alembic/versions/s03_team_invites.py` — Reversible migration creating team_invite table with unique code index + FK cascades (T01)
- `backend/app/crud.py` — Added create_team_invite and accept_team_invite helpers; latter handles unknown/expired/used/duplicate-member guards atomically (T02)
- `backend/app/api/routes/teams.py` — Replaced 501 invite stub with real issuance; added /join/{code}, PATCH /members/{uid}/role, DELETE /members/{uid}; refactored shared admin check into _assert_caller_is_team_admin; added _team_admin_count + _code_hash helpers; fixed expired-ORM bug with session.refresh in PATCH (T02-T04)
- `backend/tests/migrations/test_s03_migration.py` — 3 round-trip migration tests (forward creates schema, downgrade drops it, duplicate code IntegrityError) using MEM016 autouse fixture pattern (T01)
- `backend/tests/api/routes/test_teams.py` — Renamed and flipped test_invite_on_non_personal_team_returns_501_stub → returns_code_url_and_expires_at (200) plus added supporting non-admin/non-member assertions (T02)
- `backend/tests/api/routes/test_invites.py` — 10 new integration tests: happy issuance, personal-team 403, non-admin/member 403, happy join, unknown 404, expired 410, used 410, duplicate 409, atomicity rollback (T04)
- `backend/tests/api/routes/test_members.py` — 9 new integration tests: promote, demote, non-admin 403, last-admin 400, unknown 404, invalid enum 422, delete 204, last-admin delete 400, personal-team delete 400 (T04)
