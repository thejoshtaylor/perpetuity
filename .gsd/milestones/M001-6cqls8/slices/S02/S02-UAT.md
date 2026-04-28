# S02: Team model + personal team bootstrap — UAT

**Milestone:** M001-6cqls8
**Written:** 2026-04-24T23:21:20.312Z

# S02 UAT — Team model + personal team bootstrap

**Preconditions**
- Postgres is running and migrated to the `s02_team_columns` head.
- Environment uses FIRST_SUPERUSER credentials from config (dev default `admin@example.com` / `changethis`).
- Backend is reachable at the test base URL (`cd backend && uv run pytest tests/` runs these assertions programmatically — no human action required).

**Proof level:** integration (HTTP contract + DB transactional invariants). No human UAT required — all assertions are programmatic.

---

## UAT-01: Fresh signup creates exactly one personal team

**Steps**
1. `POST /api/v1/auth/signup` with `{email: "alice@example.com", password: "SecretPW123!", full_name: "Alice Example"}`.
2. Assert response 200 and body contains `id`, `email`, `role: "user"`, `full_name: "Alice Example"`.
3. Using the Set-Cookie session, `GET /api/v1/teams/`.
4. Assert response 200, `count == 1`, `data` has exactly one entry.
5. Assert that entry has `is_personal: true`, `role: "admin"`, `name` non-empty, `slug` matches `^[a-z0-9-]+$` and ends with 8 hex chars.

**Proved by:** `test_signup_creates_personal_team`, `test_get_teams_after_signup_returns_only_personal_team`.

---

## UAT-02: GET /teams requires cookie authentication

**Steps**
1. With no session cookie, `GET /api/v1/teams/`.
2. Assert 401 with detail `"Not authenticated"`.

**Proved by:** `test_get_teams_without_cookie_returns_401`.

---

## UAT-03: Create a non-personal team and see both teams

**Steps**
1. Sign up as alice@example.com (retain cookie).
2. `POST /api/v1/teams/` with `{name: "Widgets Inc"}`.
3. Assert 200, response `is_personal: false`, `role: "admin"`, `name: "Widgets Inc"`, `slug` starts with `widgets-inc-` followed by 8 hex chars.
4. `GET /api/v1/teams/`.
5. Assert `count == 2` — one personal, one Widgets Inc, both with role=admin for alice.

**Proved by:** `test_post_teams_creates_non_personal_team_with_creator_as_admin`.

---

## UAT-04: Invalid team-creation input returns 422

**Steps**
1. Authenticated, `POST /api/v1/teams/` with body `{}`.
2. Assert 422.
3. `POST /api/v1/teams/` with `{name: "a" * 256}` (256 characters).
4. Assert 422.

**Proved by:** `test_post_teams_missing_name_returns_422`, `test_post_teams_name_too_long_returns_422`.

---

## UAT-05: Invite endpoint rejects personal teams

**Steps**
1. Signup as alice, get personal team id from GET /api/v1/teams/.
2. `POST /api/v1/teams/{personal_team_id}/invite` (any body).
3. Assert 403 with detail exactly `"Cannot invite to personal teams"`.

**Proved by:** `test_invite_on_personal_team_returns_403`.

---

## UAT-06: Invite endpoint returns 501 on non-personal teams (S03 handoff)

**Steps**
1. Signup as alice; `POST /api/v1/teams/` to create a non-personal team; capture the returned team id.
2. `POST /api/v1/teams/{non_personal_team_id}/invite`.
3. Assert 501 with detail containing `"Invite endpoint not yet implemented"` and referencing S03.
4. **S03 executor note:** when you wire real invite issuance, this test's 501 assertion must be updated to the new 200 contract — that is the designed handoff signal.

**Proved by:** `test_invite_on_non_personal_team_returns_501_stub` (assertion message documents the handoff).

---

## UAT-07: Cross-user isolation — team membership never leaks

**Steps**
1. User A signs up as a@example.com and creates a non-personal team `Team X`.
2. User B signs up as b@example.com (fresh cookies).
3. As user B, `GET /api/v1/teams/`.
4. Assert `count == 1`, the one team is B's personal team, `Team X` is absent.
5. Optional: as B, `POST /api/v1/teams/{team_x_id}/invite` — should also fail the admin membership check (403 'Only team admins can invite'), never surface Team X's data.

**Proved by:** `test_get_teams_does_not_leak_other_users_teams` (case 8).

---

## UAT-08: Slug collision on identical team names — both succeed

**Steps**
1. User A signs up; `POST /api/v1/teams/ {name: "Research"}`.
2. Assert 200, slug starts with `research-` and ends in 8 hex chars; record slug.
3. User B signs up (fresh cookies); `POST /api/v1/teams/ {name: "Research"}`.
4. Assert 200, slug starts with `research-`, differs from A's slug in the hex suffix.

**Proved by:** `test_slug_collision_on_identical_names_still_succeeds` (case 9).

---

## UAT-09: Signup atomicity — nothing persists on mid-transaction failure

**Steps**
1. Monkeypatch `crud.create_user_with_personal_team` to raise RuntimeError after the user flush but before commit.
2. Using `TestClient(app, raise_server_exceptions=False)`, `POST /api/v1/auth/signup` with a fresh email.
3. Assert response is 500.
4. Query the DB directly — assert zero user rows with that email.
5. Query the DB — assert zero team rows with the slug stem derived from that email's local part.

**Proved by:** `test_signup_rolls_back_on_mid_transaction_failure`.

---

## UAT-10: Identical full_name across users produces distinct slugs

**Steps**
1. Sign up two different users, both with `full_name: "Jane Q Public"`.
2. Inspect each user's personal team slug from GET /teams.
3. Assert both slugs start with `jane-q-public-` and end with different 8-hex suffixes (derived from each user's UUID).

**Proved by:** `test_signup_identical_full_name_produces_distinct_slugs`.

---

## UAT-11: FIRST_SUPERUSER bootstrap is wired correctly

**Steps**
1. Bring up a fresh DB and run `init_db` (happens in the session-scoped test fixture).
2. Query TeamMember for the FIRST_SUPERUSER user_id.
3. Assert exactly one row, role=admin, team.is_personal=true.

**Proved by:** `test_superuser_bootstrap_has_personal_team`.

---

## UAT-12: Migration round-trip preserves DB shape

**Steps**
1. From s01 head, run `alembic upgrade head` to reach `s02_team_columns`.
2. Assert `team` table has columns `name` (NOT NULL), `slug` (NOT NULL, unique index `ix_team_slug`), `is_personal` (NOT NULL, default FALSE).
3. Seed a team row manually in s01 shape (id + created_at only) BEFORE upgrade; re-run upgrade; assert row is backfilled with `Legacy Team <short>`, `legacy-<short>`, `is_personal=FALSE`.
4. Run `alembic downgrade -1`; assert the three columns and unique index are gone.

**Proved by:** `test_s02_upgrade_adds_columns_not_null_and_unique_slug`, `test_s02_downgrade_drops_columns`, `test_s02_backfills_preexisting_row_with_legacy_name_and_slug`.

---

## Operational Readiness

- **Health signal:** successful signups emit `personal_team_bootstrapped user_id=<uuid> team_id=<uuid>` and `team_created team_id=<uuid> is_personal=True creator_id=<uuid>` INFO logs. Team-create endpoint emits `team_created is_personal=False`.
- **Failure signal:** transactional failures emit `signup_tx_rollback <redacted_email> stage=crud` WARNING. Slug collisions on POST /teams emit `team_create_slug_conflict slug=<attempted> user_id=<uuid>` WARNING and return 409. Invite rejections on personal teams emit `invite_rejected_personal team_id=<uuid> user_id=<uuid>` INFO.
- **Recovery procedure:** if a signup 500s, the transaction guarantees no orphan user or team. Caller retries with the same body. If slug collision repeats beyond the 8-hex window (astronomically unlikely), a follow-up slice would widen the suffix.
- **Monitoring gaps:** signup rate-limiting is not in this slice — noted for a later slice. Admin-membership cache is not introduced (the admin check is a single indexed lookup; fine at current scale). No metrics emission yet — logs only.
- **Inspection surfaces:** `team` and `team_member` tables via psql, `GET /api/v1/teams` as a caller self-inspection surface, `alembic_version` table for migration state.
- **Redaction invariants:** no log line contains team name, team slug, or raw user email. Emails go through existing `_redact_email`; team names never enter logs; only UUIDs do.
