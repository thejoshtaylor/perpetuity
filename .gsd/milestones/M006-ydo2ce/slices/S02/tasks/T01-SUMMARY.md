---
id: T01
parent: S02
milestone: M006-ydo2ce
key_files:
  - /Users/josh/code/perpetuity/backend/app/api/routes/github.py
  - /Users/josh/code/perpetuity/backend/tests/api/routes/test_github_state_jwt.py
  - /Users/josh/code/perpetuity/backend/tests/api/routes/test_github_install.py
key_decisions:
  - _mint_state in test_github_install.py updated to include user_id by default (uuid4()) so existing negative-path tests that craft JWTs without user_id still pass the new validation gate and reach their intended failure mode (team_unknown, expired, bad_sig, etc.)
  - user_id validation fires after successful jwt.decode, so signature/expiry failures are still surfaced with their existing detail strings before user_id is checked
duration: 
verification_result: passed
completed_at: 2026-05-12T21:25:57.152Z
blocker_discovered: false
---

# T01: Extended install-state JWT to carry user_id claim; updated mint/decode helpers and install-url route; added 11 unit tests

**Extended install-state JWT to carry user_id claim; updated mint/decode helpers and install-url route; added 11 unit tests**

## What Happened

Read github.py lines 113–134 (_mint_install_state, _decode_install_state) and 473–518 (get_github_install_url) to understand the existing JWT shape.

Changes made:
1. _mint_install_state(team_id, user_id): added user_id parameter; added "user_id": str(user_id) to the JWT payload.
2. _decode_install_state: after the successful jwt.decode block, added validation that user_id is present (non-empty/non-null) and parseable as uuid.UUID. Missing or unparseable → HTTPException(400, detail="install_state_user_unknown").
3. get_github_install_url at line ~502: changed _mint_install_state(team_id) to _mint_install_state(team_id, current_user.id).
4. Created tests/api/routes/test_github_state_jwt.py with 11 pure unit tests (no DB): round-trip preserves user_id, exp window, missing user_id, empty user_id, null user_id, non-UUID string, integer user_id, random string, empty state token, expired token, bad signature.
5. Updated _mint_state() helper in test_github_install.py to include user_id by default (a fresh uuid4()), so the existing test_install_callback_team_unknown_returns_400 test (which uses _mint_state with a nonexistent team_id) continues to reach the team-lookup stage rather than failing earlier on missing user_id.

## Verification

Ran: cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_state_jwt.py -v — 11 passed, 0 failed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_state_jwt.py -v` | 0 | 11 passed | 1200ms |

## Deviations

None — implementation matches the task plan exactly.

## Known Issues

None.

## Files Created/Modified

- `/Users/josh/code/perpetuity/backend/app/api/routes/github.py`
- `/Users/josh/code/perpetuity/backend/tests/api/routes/test_github_state_jwt.py`
- `/Users/josh/code/perpetuity/backend/tests/api/routes/test_github_install.py`
