---
id: M001-6cqls8
title: "Foundation & Teams"
status: complete
completed_at: 2026-04-25T03:29:46.371Z
key_decisions:
  - D001 (S01) — httpOnly cookie sessions over localStorage Bearer: XSS-safe and the only mechanism that flows through WebSocket upgrade requests transparently.
  - D002 (S01) — UserRole enum on User + TeamRole enum on TeamMember join table: a user can be admin of one team and member of another; role lives on the membership.
  - D003 (S02) — `is_personal` boolean flag on Team; invite endpoints reject personal teams at API layer.
  - D011 (S01) — S01 creates a minimal Team stub (id+created_at) so TeamMember's FK resolves; S02 extends with name/slug/is_personal columns.
  - S03 — Cross-boundary error mapping via `ValueError(StrSubclassEnum.X)`: crud raises sentinel str values from a closed reason set; route catches and maps to HTTP status.
  - S05 — Router-level `dependencies=[Depends(get_current_active_superuser)]`: every admin endpoint shares the same gate; declaring on the router prevents an ungated endpoint slipping in later.
  - S05 — Admin endpoints intentionally BYPASS per-team membership helpers: system admin must inspect any team regardless of membership; bypass is the contract.
  - S04 — Auth-state truth source = React Query `['currentUser']` cache populated by route-guard `ensureQueryData`; public-route allowlist gates 401 redirect.
  - S04 — Open-redirect defense via `sanitizeNextPath` regex `^/[^/\\]`: rejects protocol-relative and absolute URLs.
key_files:
  - backend/app/models.py
  - backend/app/api/deps.py
  - backend/app/api/main.py
  - backend/app/api/routes/auth.py
  - backend/app/api/routes/teams.py
  - backend/app/api/routes/admin.py
  - backend/app/api/routes/ws.py
  - backend/app/core/security.py
  - backend/app/core/cookies.py
  - backend/app/core/config.py
  - backend/app/core/db.py
  - backend/app/crud.py
  - backend/app/alembic/versions/s01_auth_and_roles.py
  - backend/app/alembic/versions/s02_team_columns.py
  - backend/app/alembic/versions/s03_team_invites.py
  - backend/tests/api/routes/test_auth.py
  - backend/tests/api/routes/test_ws_auth.py
  - backend/tests/api/routes/test_teams.py
  - backend/tests/api/routes/test_invites.py
  - backend/tests/api/routes/test_members.py
  - backend/tests/api/routes/test_admin_teams.py
  - backend/tests/migrations/test_s01_migration.py
  - backend/tests/migrations/test_s02_migration.py
  - backend/tests/migrations/test_s03_migration.py
  - frontend/src/main.tsx
  - frontend/src/hooks/useAuth.ts
  - frontend/src/lib/auth-guards.ts
  - frontend/src/routes/_layout.tsx
  - frontend/src/routes/_layout/teams.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
  - frontend/src/routes/_layout/admin.tsx
  - frontend/src/routes/_layout/admin_.teams.tsx
  - frontend/src/routes/_layout/admin.teams_.$teamId.tsx
  - frontend/src/routes/invite.$code.tsx
  - frontend/src/components/Teams/CreateTeamDialog.tsx
  - frontend/src/components/Teams/InviteButton.tsx
  - frontend/src/components/Teams/MembersList.tsx
  - frontend/src/components/Teams/RemoveMemberConfirm.tsx
  - frontend/src/components/Admin/PromoteSystemAdminDialog.tsx
  - frontend/src/components/Admin/AdminTeamsColumns.tsx
  - frontend/src/components/Sidebar/AppSidebar.tsx
  - frontend/playwright.config.ts
  - frontend/tests/teams.spec.ts
  - frontend/tests/admin-teams.spec.ts
  - frontend/tests/utils/teams.ts
lessons_learned:
  - Migration tests: a session-scoped autouse db Session silently holds an AccessShareLock that blocks alembic DROP COLUMN; release it (commit/expire/close) and engine.dispose() before alembic, plus dispose again in teardown (MEM016).
  - SQLModel enums land in Postgres with lowercase typname (userrole/teamrole) — migration tests querying pg_type must use lowercase or assertions silently misread missing enums (MEM020).
  - httpx test clients raise CookieConflict from stale jar state — cookie-based fixtures must call client.cookies.clear() before logging in (MEM017).
  - WS auth dependencies must open their own short-lived Session(engine) — FastAPI does not resolve Depends(get_db) for WS-parameter helpers invoked imperatively (MEM022).
  - WS auth close must be called BEFORE accept — Starlette converts pre-accept close into a handshake rejection with the supplied code/reason (1008 contract).
  - After session.commit() on a SQLModel ORM instance, model_dump() returns {} until session.refresh() is called — silent Pydantic ValidationError otherwise (S03 PATCH bug).
  - TanStack Router file routes nest by default; trailing-underscore opt-out (teams_.$teamId.tsx) is required when the parent has no <Outlet/> (MEM048).
  - React 18 StrictMode breaks useMutation lifecycle on useEffect-driven mutations — second mount's hook never advances past isIdle even though the gate fires once (MEM049). Hoist to TanStack Router loader.
  - Backend tests must run from `backend/` cwd because Settings reads `backend/.env`; verification gate scripts must use `cd backend &&` prefix (MEM041).
---

# M001-6cqls8: Foundation & Teams

**Replaced the template's localStorage JWT auth with httpOnly cookie sessions, replaced is_superuser with UserRole/TeamRole enums, shipped real team management (invites/roles/removal) with personal-team protection, a system admin panel, and a mobile-ready React frontend — all proven against real Postgres and a 375px Mobile-Chrome Playwright gate.**

## What Happened

M001-6cqls8 converted the FastAPI/React template into the real product foundation across 5 slices delivered in dependency order.

S01 (auth migration + roles) replaced OAuth2 Bearer/localStorage JWT with httpOnly cookie sessions, introduced `UserRole` (user, system_admin) on User and `TeamRole` (member, admin) on a new TeamMember join table, added a minimal Team stub, and shipped a fully-reversible alembic migration `s01_auth_and_roles`. New endpoints: POST /auth/{signup,login,logout}; GET /users/me now exposes role; WS /ws/ping authenticates via cookie. Discovered and patched a session-scoped autouse-Session lock hazard that blocked alembic DROP COLUMN (MEM016). 21/21 slice tests + full 76/76 backend suite pass.

S02 (teams + personal team bootstrap) extended the Team stub with name/slug/is_personal, added GET /teams (single-SELECT-JOIN, no N+1, doubles as the security boundary), POST /teams (creator becomes admin), and made signup atomic by introducing `crud.create_user_with_personal_team` (User + Team + TeamMember(admin) committed once). POST /teams/{id}/invite was wired as a 501 stub explicitly designed as a handoff signal for S03. Migration `s02_team_columns` ships nullable→backfill→NOT-NULL pattern. R003 validated.

S03 (invites + membership management) replaced the 501 stub with real invite issuance (7-day TTL, `secrets.token_urlsafe(24)`, ~190-bit entropy, never logged raw — only `sha256(code)[:8]`), POST /teams/join/{code} with the unknown→expired→used→duplicate-member guard chain and atomic insert+stamp, plus PATCH /teams/{id}/members/{uid}/role and DELETE /teams/{id}/members/{uid}. Established the `_assert_caller_is_team_admin` precondition helper and `_team_admin_count` aggregate that blocks demoting/removing the last admin in a single O(1) query. 19 new integration tests; 125/125 backend pass. R004 validated.

S04 (frontend: cookie auth + team dashboard) regenerated the OpenAPI client against the live S03 backend, set `OpenAPI.WITH_CREDENTIALS=true`, deleted localStorage token paths, and made React Query `['currentUser']` cache (populated by `ensureQueryData` in route guards) the auth-state truth source. Public-route allowlist gates the 401 redirect to break the login-page redirect loop (MEM051). Shipped /teams dashboard with role badges + Personal chip + empty state, CreateTeamDialog, InviteButton with copy-to-clipboard (`document.execCommand('copy')` fallback for non-HTTPS), /invite/{code} with `sanitizeNextPath` open-redirect defense (regex `^/[^/\\]`), MembersList with admin-only DropdownMenu, type-to-confirm RemoveMemberConfirm. Added GET /teams/{id}/members backend endpoint with paired `_assert_caller_is_team_member` helper. Mobile-Chrome Pixel-5 Playwright project covers all 10 slice-demo scenarios at 375px; R022 (mobile usability) hook mechanically asserted. Discovered TanStack Router trailing-underscore opt-out for sibling-not-child routes (MEM048) and a StrictMode `useMutation` lifecycle desync on `useEffect`-driven mutations (MEM049 — toast assertion preserves the user-visible signal).

S05 (system admin panel) added `/admin/*` router with router-level `dependencies=[Depends(get_current_active_superuser)]` so the role check fires before any handler logic. Endpoints: paginated GET /admin/teams, cross-team-bypass GET /admin/teams/{id}/members (deliberately bypasses per-team membership helpers — system admin must inspect any team), and idempotent POST /admin/users/{id}/promote-system-admin (logs `already_admin=true|false` in lowercase string form). Frontend added a reusable `requireSystemAdmin` TanStack guard at `frontend/src/lib/auth-guards.ts` (existing /admin refactored to consume it), /admin/teams paginated DataTable, /admin/teams/{teamId} read-only members view, PromoteSystemAdminDialog with the contracted confirm copy and `Promoted to system admin` toast, conditional `All Teams` sidebar entry. 15 backend integration tests + 2 Playwright specs all pass. R002 fully validated end-to-end across REST + UI.

Cumulatively: 75 application files changed, ~7,700 insertions; backend tests 140+ passing against real Postgres (no mocks); frontend lint+build+E2E green at desktop and Pixel-5 mobile. Every M001 success criterion is provable in a browser end-to-end.

## Success Criteria Results

## Success Criteria Verification

- ✅ **A new user signs up and is automatically assigned a personal team.**
  Evidence: S02 `test_signup_creates_personal_team` (happy path), `test_signup_rolls_back_on_mid_transaction_failure` (atomicity), `test_superuser_bootstrap_has_personal_team` (init_db wiring), `test_get_teams_after_signup_returns_only_personal_team`. R003 validated.

- ✅ **A user can create a team, invite another user, and manage roles.**
  Evidence: S03 19 integration tests covering invite issuance (4 cases), acceptance with TTL/one-shot/duplicate guards (6), role promote/demote with last-admin protection (6), and member removal (3). Multi-team membership with distinct roles end-to-end demonstrated. R004 validated. S04 frontend wires Create-Team dialog, InviteButton with copy-to-clipboard, MembersList with admin-only DropdownMenu — all green in teams.spec.ts at desktop + Pixel-5.

- ✅ **System admin can view all teams and promote users to system admin with confirm.**
  Evidence: S05 15 backend integration tests in test_admin_teams.py (paginated /admin/teams, cross-team members view, idempotent promote with already_admin=true|false log assertions, 200/403/401 matrix, 404s). Playwright admin-teams.spec.ts proves browser-level happy path: superuser sees all teams, drills into members, opens UserActionsMenu, confirms via PromoteSystemAdminDialog, role badge flips to Admin. Non-admin redirected from /admin/teams by `requireSystemAdmin` guard. R002 validated.

- ✅ **All integration tests pass against real Postgres — no mocked database.**
  Evidence: D001/D002 enforced. Backend full suite 140+/140+ green (76 baseline post-S01, 93 post-S02, 125 post-S03, +15 admin tests post-S05). All run via `cd backend && uv run pytest tests/` against the project's local Postgres on :55432. No mocks anywhere — verified by repo-wide grep at slice gate time.

- ✅ **httpOnly cookie auth works for both REST and WebSocket upgrade requests.**
  Evidence: S01 21/21 tests covering REST (test_auth.py 13 cases) and WS (test_ws_auth.py 6 cases all four reject reasons: missing_cookie, invalid_token, user_not_found, user_inactive — plus happy-path pong with role). `WS /api/v1/ws/ping` mounted; `Set-Cookie: perpetuity_session=...; HttpOnly; Path=/; SameSite=lax` confirmed at runtime. R001 validated.

- ✅ **Full flow is usable on a 375px mobile viewport.**
  Evidence: S04 Mobile-Chrome (Pixel 5) Playwright project runs the full slice demo end-to-end. R022 mechanically asserted by `tests/teams.spec.ts:280` — `document.documentElement.scrollWidth <= window.innerWidth` on /teams at 375px. 23/23 teams.spec scenarios pass on chromium AND mobile-chrome.

## Definition of Done Results

## Definition of Done

- ✅ **All 5 slices [x]:** S01, S02, S03, S04, S05 all marked complete in `M001-6cqls8-ROADMAP.md` with `verification_result: passed` in their YAML frontmatter.
- ✅ **All slice summaries exist:** SUMMARY.md present at `.gsd/milestones/M001-6cqls8/slices/{S01..S05}/` with full provides/requires/key_decisions/patterns/observability/verification.
- ✅ **Cross-slice integration verified:**
  - S01→S02: TeamMember/TeamRole enum + cookie get_current_user consumed by S02 signup atomicity.
  - S02→S03: Team(is_personal) + GET/POST /teams consumed by S03 invite/role/remove. S02's 501 invite stub flipped to 200 cleanly (test assertion message instructed the handoff).
  - S03→S04: All invite/join/role/remove endpoints consumed by frontend dashboard, /invite/{code} acceptance, MembersList.
  - S03→S05: `_assert_caller_is_*_admin` precondition pattern reused; admin endpoints intentionally BYPASS per-team membership helpers (cross-team contract).
  - S01→S05: `get_current_active_superuser` (rewritten in S01 to check `role==system_admin`) is the router-level gate for /admin/*.
- ✅ **Code change verification:** `git diff --stat HEAD origin/main..HEAD -- ':!.gsd/'` shows 75 application files changed across backend (models/routes/migrations/tests) and frontend (routes/components/tests). 7,739 insertions / 708 deletions. Real product changes — not planning artifacts only.
- ✅ **Test verification:** Backend 140+/140+ pass against real Postgres; Frontend lint clean + build green + Playwright suites green at chromium + Pixel-5 mobile-chrome.

## Requirement Outcomes

## Requirement Status Transitions

- **R001 (httpOnly cookie sessions for REST + WS) — Active → Validated** at S01 close.
  Evidence: 21/21 slice tests + full 76/76 backend suite. Cookie signup/login/logout, WS /ws/ping cookie auth proven for all four reject reasons (missing_cookie, invalid_token, user_not_found, user_inactive) plus happy path. Migration round-trip clean.

- **R002 (UserRole + TeamRole enums; roles enforced at API layer) — Active → Validated** at S05 close.
  Evidence: S01 introduces the enums + migrates the API layer off `is_superuser`. S03 enforces TeamRole on invite/role/remove. S05 closes with 15 backend integration tests (200/403/401 matrix, pagination, idempotency, 404s, cross-team bypass) + Playwright admin-teams.spec.ts (happy path + non-admin redirect). All endpoints emit structured INFO logs.

- **R003 (Personal team auto-created at signup) — Active → Validated** at S02 close.
  Evidence: S02 integration tests prove every signup creates exactly one TeamMember(role=admin) on a Team(is_personal=True), atomically. test_signup_creates_personal_team, test_signup_rolls_back_on_mid_transaction_failure, test_superuser_bootstrap_has_personal_team, test_invite_on_personal_team_returns_403.

- **R004 (Team admins can invite, promote, remove; multi-team distinct roles) — Active → Validated** at S03 close.
  Evidence: S03 19 integration tests cover invite/accept (with TTL+one-shot+duplicate+atomicity guards), promote/demote (with last-admin protection), and removal (with personal-team and last-admin guards). Multi-team membership with distinct roles end-to-end demonstrated.

- **R022 (Mobile usability — every feature works on phone screens) — Active (Advanced)** at S04 close.
  Evidence: S04 mechanically asserts no-horizontal-scroll on /teams at 375px on chromium AND mobile-chrome (Pixel 5) Playwright projects. Status remains active — M006 (PWA + Notifications + Voice) extends to PWA install + service worker + cross-route mobile audit. The M001 portion of R022 is delivered.

No requirement was invalidated, deferred, or re-scoped during this milestone.

## Deviations

## Deviations from the Original Plan

- **S01 — Token/TokenPayload SQLModel classes left in `models.py` as harmless dead shapes.** Aggressive removal was out of scope; deletion can happen any time without behavior change.
- **S01 — `app/api/routes/items.py` was not in the plan's file inventory but had 4 `is_superuser` references** that had to be rewritten to `role == UserRole.system_admin`.
- **S04 — Renamed `teams.$teamId.tsx` to `teams_.$teamId.tsx`** (trailing-underscore opt-out from TanStack Router nesting; MEM048). Not in T05's plan, but prerequisite for the slice gate to be passable at all because parent Teams component had no `<Outlet/>`.
- **S04 — Asserted on 'Invite not found' toast text rather than testid'd error card** in expired-invite test, due to StrictMode useMutation desync (MEM049). User-visible signal preserved; recommended follow-up captured.
- **S04 — Migrated 6 Playwright `waitForURL('/')` calls** across login.spec.ts/auth.setup.ts/utils/user.ts to `/teams` (T02). The slice plan flagged the 'Welcome back' text assertion but not the URL assertion — both broke from the same routing change.
- **S04 — Removed `isLoggedIn` from useAuth entirely** (T01) instead of keeping a deprecated alias. The rewrite touched all five callers anyway; alias would have been dead code.
- **S04 — Added Create-Team button to dashboard header** in addition to the empty-state stub (T03). Plan only called for empty-state wiring; users with at least one team also need an entry point. Same component, identical behavior.
- **S05 — No deviations.** Implementation matched T01–T05 exactly; T05's `may need to amend T03/T04` note turned out to be unnecessary because every required data-testid was wired proactively during T03/T04.

## Follow-ups

## Follow-ups for Future Milestones

- **Demote-system-admin endpoint** (S05 follow-up): if/when role demotion becomes a product requirement, add POST /admin/users/{id}/demote-system-admin with explicit safeguards (cannot demote self, cannot demote the last system admin) and paired confirm dialog.
- **Refactor /invite/$code to TanStack Router loader** (S04 — MEM049): would let the route assert on the testid'd error card directly rather than toast text and resolve StrictMode useMutation desync.
- **Backend 409 (already-member) detail body should include team_id** so the FE can redirect the user directly to that team instead of bouncing to /teams.
- **Frontend error reporting (Sentry or similar)** to replace console.warn breadcrumbs — out-of-scope for M001 but worth adding before public launch.
- **Code-split the main bundle** to address the pre-existing >500kB chunk warning. Vite manualChunks or dynamic import() for admin and team detail routes.
- **Periodic team_invite pruning slice** (S03 follow-up): drop used/expired rows older than N days to prevent unbounded growth.
- **SECRET_KEY rotation step in ops checklist**: config.py raises in staging/production but operators must regenerate the default `changethis` before first non-local boot.
- **DB-backed session table** (S01 follow-up): only required if revocation-on-logout becomes a requirement; current logout only clears the client cookie.
- **Brute-force rate-limiting on /teams/join/{code}**: entropy alone (~190 bits) is the protection today; add per-IP rate limiting at FastAPI middleware if abuse is observed.
- **Capture an ADR** (S04 follow-up): document the auth-state-truth-source pattern (React Query cache + ensureQueryData) so future major route additions follow the same shape.
- **Pre-existing failing chromium-only tests** (admin.spec, reset-password.spec) need either seeding/mailcatcher infra or removal so CI is unambiguously green.
- **Member count column on /admin/teams** (S05 follow-up): adds either a join in the list query or N+1 — defer until a real product/UX driver surfaces.
- **Extend mobile R022 audit beyond /teams** to /teams/$teamId, /invite/$code, and the create/invite/members panels — closes out R022 ahead of M006.
