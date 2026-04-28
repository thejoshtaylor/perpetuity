---
estimated_steps: 19
estimated_files: 7
skills_used: []
---

# T03: Wire /auth router (signup/login/logout) + cookie-based get_current_user + /users/me role field

Create `app/api/routes/auth.py` with three endpoints: `POST /auth/signup` (body: email, password, full_name?), `POST /auth/login` (body: email, password — JSON, not OAuth2 form), `POST /auth/logout` (no body). Signup creates a user with `role=UserRole.user`, sets the session cookie, returns `UserPublic`. Login validates, sets cookie, returns `UserPublic`. Logout clears cookie, returns `{message: 'Logged out'}`. Mount the new router in `app/api/main.py`. Rewrite `app/api/deps.py::get_current_user` to read `settings.SESSION_COOKIE_NAME` from `Request.cookies` via `Cookie(None)` dependency — remove the `OAuth2PasswordBearer` usage and the `reusable_oauth2` global. Update `UserPublic` to include `role: UserRole`. Delete the old `POST /login/access-token` endpoint and the `/users/signup` endpoint (replaced by `/auth/*`); update `app/api/routes/login.py` to keep only password-recovery endpoints, and remove signup from `users.py`. Update `get_current_active_superuser` to check `current_user.role == UserRole.system_admin` instead of `is_superuser`. Update `initial_data.py` and any tooling that referenced `is_superuser` on the User model.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Password verify | 400 Incorrect email or password (generic) | N/A | N/A |
| Cookie decode | 401 Not authenticated | N/A | 401 Not authenticated |
| User.is_active=False | 400 Inactive user | N/A | N/A |
| User lookup by id | 401 Not authenticated (not 404 — don't leak existence) | N/A | N/A |

Load Profile:
- Shared resources: DB session pool; password hasher (Argon2 CPU)
- Per-operation cost: 1 DB query for login, 1 insert for signup, 0 for logout; Argon2 verify ~50–100ms
- 10x breakpoint: Argon2 CPU (mitigation is out of scope for S01 — documented only)

Negative Tests (in T05):
- Missing cookie → 401
- Tampered cookie → 401 (not 500)
- Expired cookie → 401
- Login with wrong password → 400 generic message (no user-existence leak)
- Signup with duplicate email → 400
- Logout without cookie → 200 idempotent (clears cookie anyway)

## Inputs

- ``backend/app/api/deps.py``
- ``backend/app/api/main.py``
- ``backend/app/api/routes/login.py``
- ``backend/app/api/routes/users.py``
- ``backend/app/core/cookies.py``
- ``backend/app/core/security.py``
- ``backend/app/models.py``
- ``backend/app/initial_data.py``

## Expected Output

- ``backend/app/api/routes/auth.py``
- ``backend/app/api/main.py``
- ``backend/app/api/deps.py``
- ``backend/app/models.py``
- ``backend/app/api/routes/login.py``
- ``backend/app/api/routes/users.py``
- ``backend/app/initial_data.py``

## Verification

cd backend && uv run python -c "from app.main import app; routes={r.path for r in app.routes}; assert '/api/v1/auth/signup' in routes and '/api/v1/auth/login' in routes and '/api/v1/auth/logout' in routes; assert '/api/v1/login/access-token' not in routes" && uv run python -c "from app.models import UserPublic; assert 'role' in UserPublic.model_fields"

## Observability Impact

Structured INFO logs on login success/failure and signup (email redacted: `a***@domain.com`). Cookie decode failures log the exception class at DEBUG. 401/400 response shape unchanged — agents inspecting failures still get `detail` field.
