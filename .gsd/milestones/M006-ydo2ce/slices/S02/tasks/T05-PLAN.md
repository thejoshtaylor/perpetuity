---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T05: Backwards-compat: legacy state JWT rejection test + M005-sqm8et regression check

T01 deliberately rejects legacy state JWTs without user_id; the rejection path must be tested explicitly so a future regression that quietly accepts the legacy shape is caught. Add test that mints a legacy-shape state JWT (manually construct jwt.encode without user_id claim, using settings.SECRET_KEY), calls the GET install-callback endpoint with that state + a mock OAuth code, asserts the redirect URL contains github_install_error=install_state_user_unknown. Add a second test asserting the existing org-install path (POST /github/install-callback with installation_id, state) STILL works without modification.

## Inputs

- `T01's _decode_install_state rejection logic`
- `Existing M005-sqm8et install-callback test for org-install path`

## Expected Output

- `Test for legacy-state JWT rejection with install_state_user_unknown error`
- `Test asserting POST /github/install-callback org-install path is unchanged from M005-sqm8et`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v -k "legacy_state or org_install"
