---
estimated_steps: 40
estimated_files: 4
skills_used: []
---

# T02: Transactional personal-team bootstrap in POST /auth/signup

Mutate the existing S01 signup endpoint so it creates User + Team(is_personal=True, name=email-derived, slug=generated) + TeamMember(role=admin) in a single SQL transaction. If any step fails, the whole thing rolls back — no orphan users, no orphan teams.

**Why this is a separate task:** keeping schema (T01) and signup flow mutation (T02) independent means T01 is committable and verifiable on its own; this task consumes T01's output. Also: the atomicity test here is the proof that delivers R003.

**Approach — new CRUD helper, NOT inline code:**
Add `crud.create_user_with_personal_team(*, session: Session, user_create: UserCreate) -> tuple[User, Team]` to `app/crud.py`. Inside:
1. Check existing user by email — if exists, raise the same HTTPException the signup endpoint currently raises (preserves S01 error-path behavior).
2. Build user via `User.model_validate(user_create, update={'hashed_password': get_password_hash(...)})` — do NOT commit yet.
3. `session.add(user)`, `session.flush()` — gets user.id without committing.
4. Build Team: `name = user.full_name or user.email.split('@')[0]` (trim to 255 if needed); `slug = _slugify(name) + '-' + short_suffix_from(user.id)` — ensures uniqueness even if two users have the same name. is_personal=True.
5. `session.add(team); session.flush()` — gets team.id.
6. `session.add(TeamMember(user_id=user.id, team_id=team.id, role=TeamRole.admin))`.
7. `session.commit()`; refresh both objects; return (user, team).

On ANY exception inside: `session.rollback()` then re-raise. The commit-only-at-end pattern guarantees atomicity. DO NOT call `crud.create_user` from within — that helper commits early and would break atomicity.

**Slug generation:**
New module-level helper `_slugify(name: str) -> str` in `app/crud.py`:
- Lowercase.
- Replace any run of non-[a-z0-9] with single `-`.
- Strip leading/trailing `-`.
- Truncate to 48 chars.
- If empty after normalization, fall back to `user`.
Then append `-` + first 8 chars of `user.id.hex` for uniqueness. Total slug ≤ 64 chars (fits the T01 column constraint).

**Signup endpoint changes (`app/api/routes/auth.py::signup`):**
- Replace the `existing = crud.get_user_by_email(...)` + `crud.create_user(...)` pair with a single call to `crud.create_user_with_personal_team(...)`.
- Handle the duplicate-email path inside the new helper (raise HTTPException(400, 'The user with this email already exists in the system')).
- Log `personal_team_bootstrapped user_id=<uuid> team_id=<uuid>` at INFO after success, in addition to the existing `signup ok <redacted_email>` log.
- On IntegrityError or any unexpected exception, log `signup_tx_rollback <redacted_email> stage=<crud|session>` at WARNING and re-raise (FastAPI → 500).

**System-admin seed consistency (`app/core/db.py::init_db`):**
The FIRST_SUPERUSER seed path calls `crud.create_user` directly and would NOT get a personal team. Update it to call `crud.create_user_with_personal_team` too (same contract — the superuser also deserves a personal team; R003 says every new user). If the helper rejects duplicate emails via HTTPException, wrap or add a `_internal` variant that raises a plain ValueError instead. Simplest: have the helper take `raise_http_on_duplicate: bool = True` and set it False for init_db.

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (insert) | rollback, re-raise as 500 | same (network timeout → SA OperationalError → rollback) | N/A (DB returns typed errors) |
| UserCreate validation | pydantic raises 422 before reaching crud | N/A | N/A |

**Load Profile:**
- Shared resources: one DB connection per request (pool already sized); signup adds 3 inserts in one tx vs 1 pre-S02 — 3x write amplification on signup only.
- Per-operation cost: 3 INSERTs + 1 SELECT (email check) per signup.
- 10x breakpoint: pool exhaustion at ~100 concurrent signups given default pool size of 5; acceptable — signup is not the hot path. Not addressed in this slice.

**Negative Tests (in test_auth.py):**
- Malformed: signup with 256-char name → 422 (pydantic validator).
- Error path: monkeypatch `crud.create_user_with_personal_team` to raise mid-transaction → assert response is 500 AND no user row persisted AND no team row with that slug persisted.
- Boundary: two users with identical full_name → both succeed, both get distinct slugs (suffix differs).

## Inputs

- ``backend/app/models.py` — Team model extended by T01 (needs name/slug/is_personal).`
- ``backend/app/alembic/versions/s02_team_columns.py` — migration must be applied before tests run (handled by existing test harness auto-migrate).`
- ``backend/app/crud.py` — existing `create_user` and `get_user_by_email` helpers; add new `create_user_with_personal_team` and `_slugify` alongside, do not modify existing.`
- ``backend/app/api/routes/auth.py` — existing signup endpoint to mutate.`
- ``backend/app/core/db.py` — init_db for FIRST_SUPERUSER seed; update to use new helper.`
- ``backend/tests/api/routes/test_auth.py` — extend with personal-team + rollback assertions.`

## Expected Output

- ``backend/app/crud.py` — new `create_user_with_personal_team(session, user_create, raise_http_on_duplicate=True)` helper and `_slugify(name)` helper added; existing helpers untouched.`
- ``backend/app/api/routes/auth.py` — signup endpoint rewritten to call the new helper; duplicate-email + atomic rollback logging added.`
- ``backend/app/core/db.py` — init_db calls the new helper with raise_http_on_duplicate=False so bootstrap works at app startup.`
- ``backend/tests/api/routes/test_auth.py` — new tests: signup creates personal team, signup rolls back fully on mid-tx failure, duplicate email still rejects.`

## Verification

cd backend && uv run pytest tests/api/routes/test_auth.py -v (expect all S01 tests + new signup-personal-team + rollback tests passing)

## Observability Impact

Signals added: `personal_team_bootstrapped user_id=<uuid> team_id=<uuid>` INFO on happy path; `signup_tx_rollback <redacted_email> stage=<stage>` WARNING on failure. How a future agent inspects: `grep personal_team_bootstrapped` on app logs reveals every signup's team id for debugging; a failed signup leaves a WARNING with the stage where the tx bailed. Failure state exposed: WARNING log with redacted email + stage name lets operators localize whether crud.create_user_with_personal_team fails in user, team, or membership insert.
