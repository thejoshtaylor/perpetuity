---
phase: M001-6cqls8
phase_name: Foundation & Teams
project: perpetuity
generated: 2026-04-25T03:30:00Z
counts:
  decisions: 7
  lessons: 9
  patterns: 10
  surprises: 5
missing_artifacts: []
---

# M001-6cqls8 Learnings — Foundation & Teams

### Decisions

- **httpOnly cookie sessions over localStorage Bearer tokens.** XSS-resistant and the only mechanism that flows through WebSocket upgrade requests automatically. Replaces template's localStorage JWT entirely.
  Source: M001-6cqls8-ROADMAP.md/Decisions (D001) + S01-SUMMARY.md/key_decisions

- **UserRole enum on User + TeamRole enum on TeamMember.** A user can be admin of one team and member of another — role must live on the membership join, not the user. System admin is user-level. Replaces is_superuser bool.
  Source: M001-6cqls8-ROADMAP.md/Decisions (D002) + S01-SUMMARY.md/key_decisions

- **`is_personal` boolean flag on Team; invite endpoints reject personal teams at API layer.** Simplest way to distinguish personal workspaces without a separate model; API enforcement prevents accidental sharing via invite.
  Source: DECISIONS.md (D003) + S02-SUMMARY.md/key_decisions

- **Read cookie via `request.cookies.get(settings.SESSION_COOKIE_NAME)` not `Cookie(alias=...)`.** FastAPI's Cookie dependency fixes the name at import time; dict lookup honors env overrides uniformly across HTTP and WS.
  Source: S01-SUMMARY.md/key_decisions (MEM018)

- **Cross-boundary error mapping via `ValueError(StrSubclassEnum.X)`.** crud raises sentinel str values from a closed reason set; route catches and maps to HTTP status. Keeps HTTP knowledge out of crud and avoids enum imports leaking into non-HTTP code.
  Source: S03-SUMMARY.md/key_decisions

- **Router-level `dependencies=[Depends(get_current_active_superuser)]` for the admin router.** Every admin endpoint shares the same gate; declaring it on the router prevents an ungated endpoint slipping in later. Bypass per-team helpers entirely in admin.py — system admin must inspect any team regardless of membership.
  Source: S05-SUMMARY.md/key_decisions

- **Auth-state truth source = React Query `['currentUser']` cache populated by route-guard `ensureQueryData`.** Public auth route allowlist gates the queryCache 401 redirect to prevent infinite redirect loop on the login page itself.
  Source: S04-SUMMARY.md/key_decisions

### Lessons

- **Migration test must release the session-scoped autouse db Session before alembic runs.** Open Session held an AccessShareLock on `user`, blocking `DROP COLUMN` indefinitely. Fix: autouse fixture commits/expires/closes session and `engine.dispose()` before alembic; second dispose in teardown.
  Source: S01-SUMMARY.md/Verification + MEM016

- **SQLModel enums land in Postgres with lowercase typname (`userrole`, `teamrole`).** Migration tests querying `pg_type` must use lowercase or the assertion silently passes a missing enum.
  Source: S01-SUMMARY.md/key_decisions (MEM020)

- **httpx test client `CookieConflict` from stale jar state.** Cookie-based fixtures must call `client.cookies.clear()` before logging in. Captured as MEM017.
  Source: S01-SUMMARY.md/patterns_established

- **WS auth dep must open its own short-lived `Session(engine)`.** FastAPI does not resolve `Depends(get_db)` for WS-parameter helpers invoked imperatively from a WS endpoint.
  Source: S01-SUMMARY.md/key_decisions (MEM022)

- **WS auth close must be called BEFORE `accept`.** Starlette converts a pre-accept close into a handshake rejection with the supplied code/reason — required for the 1008 contract.
  Source: S01-SUMMARY.md/key_decisions

- **`session.refresh(team)` required after `commit()` before `model_dump()`.** Otherwise the ORM instance is expired and `model_dump()` returns `{}` — silent Pydantic ValidationError when building response shapes.
  Source: S03-SUMMARY.md/What Happened (T03 PATCH bug)

- **TanStack Router file routes nest by default.** Use trailing-underscore opt-out (`teams_.$teamId.tsx`) when the parent has no `<Outlet/>`. URL stays `/teams/$teamId`.
  Source: S04-SUMMARY.md/key_decisions (MEM048)

- **React 18 StrictMode `useMutation` desync on `useEffect`-driven mutations.** Second mount's `useMutation` hook never advances past `isIdle` even though `useEffect+useRef` gate fires once. `onError` toast still fires. Production (no StrictMode) does not exhibit. Recommend hoisting to TanStack Router `loader`.
  Source: S04-SUMMARY.md/Known Limitations (MEM049)

- **Backend tests must be run from `backend/` (cwd matters for Settings/.env).** `pytest tests/` from repo root fails because Settings reads `backend/.env`. Verification gate scripts must use `cd backend &&` prefix.
  Source: S03-SUMMARY.md/Verification (MEM041)

### Patterns

- **Cookie-first auth for HTTP and WS via shared `SESSION_COOKIE_NAME` setting.** Read via `request.cookies.get` / `websocket.cookies.get`. The pattern for any new protected route in M001+.
  Source: S01-SUMMARY.md/patterns_established

- **Uniform 401 "Not authenticated" for any user-existence-adjacent failure.** Prevents account enumeration. 400 only when the user authenticated successfully but cannot proceed (inactive).
  Source: S01-SUMMARY.md/patterns_established (MEM019)

- **Transactional bootstrap pattern: flush User → build Team → flush → insert TeamMember → commit once at end.** Any exception → rollback + re-raise. Used in `create_user_with_personal_team` and reusable for invite-accept flows.
  Source: S02-SUMMARY.md/patterns_established

- **Dual-mode error handling in CRUD helpers via `raise_http_on_duplicate` flag.** Keeps FastAPI types out of non-HTTP callers (init_db, background workers).
  Source: S02-SUMMARY.md/key_decisions

- **Server-generated slugs with deterministic suffixes: `slugify(name) + '-' + 8-hex`.** User.id suffix for personal teams (deterministic per user); uuid4 for non-personal (lets one user create multiple same-named teams).
  Source: S02-SUMMARY.md/patterns_established

- **Single SELECT JOIN for collection endpoints spanning a membership table.** Both performance (no N+1) and security (WHERE clause is the boundary).
  Source: S02-SUMMARY.md/patterns_established

- **`_assert_caller_is_team_*` helper family with consistent 404→403 ordering.** member/admin variants. Single source of truth for team-mutation preconditions across invite/role/remove/list-members handlers.
  Source: S03-SUMMARY.md/patterns_established + S04-SUMMARY.md/patterns_established

- **Bearer tokens never logged raw — only `sha256(token)[:8]` hash digests.** Paired with structured machine-readable `reason=` tags on rejections so a future agent can grep-correlate an HTTP error to a single token end-to-end.
  Source: S03-SUMMARY.md/patterns_established (MEM028)

- **Last-admin / last-member protection as precondition aggregate count BEFORE the mutation.** `select(func.count())` is O(1) regardless of team size — race-safe and never compensates after.
  Source: S03-SUMMARY.md/patterns_established

- **Idempotent role-mutation endpoint pattern.** Read target → branch on current value → only write on change → always return 200 → log no-op flag in lowercase string form (`already_admin=true|false`) for grep-friendly observability.
  Source: S05-SUMMARY.md/patterns_established

### Surprises

- **S01 migration test session-lock hazard.** Pytest's session-scoped autouse db Session silently held an AccessShareLock blocking `DROP COLUMN`. Discovered only when alembic hung. Required new autouse fixture pattern (MEM016).
  Source: S01-SUMMARY.md/What Happened

- **T03 PATCH handler returned `{}` after commit because team ORM instance was expired.** Latent bug surfaced only by T04's role-update integration tests. Fixed by adding `session.refresh(team)` inside the commit block.
  Source: S03-SUMMARY.md/What Happened

- **TanStack Router silently fails to render `teams.$teamId.tsx` because parent had no `<Outlet/>`.** Trailing-underscore-opt-out (`teams_.$teamId.tsx`) was the fix — file-naming convention rather than runtime error. (MEM048)
  Source: S04-SUMMARY.md/Deviations

- **StrictMode's double-mount breaks `useMutation` lifecycle on `useEffect`-driven mutations.** Test had to assert on toast text rather than testid'd error card. Production (no StrictMode) is fine. (MEM049)
  Source: S04-SUMMARY.md/Known Limitations

- **Local Docker holds :8000 (`notifone-api-1`).** Perpetuity backend had to run on :8001 with per-test-invocation `VITE_API_URL` override. Pre-existing chromium-only tests rely on mailcatcher unavailable in this setup. (MEM046)
  Source: S04-SUMMARY.md/Known Limitations
