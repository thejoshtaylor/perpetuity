---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T01: Reorder install-token mint to after `lookup_installation` + add header read

Minting the install token before knowing the install type wastes a GitHub mint call on personal installs that won't use it; doing so also makes the 422 path noisy in logs. Reorder once before adding branching logic so the diff is easier to review. Read user_token = (request.headers.get(X-GitHub-User-Token) or '').strip() or None immediately after the JSON body parse (:243-253). Move the get_installation_token block at :256-275 and the resulting token variable definition at :277-286 to AFTER the lookup_installation block at :311-330. Keep all existing exception mapping intact.

## Inputs

- `orchestrator/orchestrator/routes_github.py:230-395 (existing create_repository_route)`

## Expected Output

- `X-GitHub-User-Token header is read once, treated as None when empty/missing`
- `get_installation_token mint reordered to AFTER lookup_installation`
- `Existing M005-sqm8et tests still pass (reordering is behavior-neutral for org installs)`

## Verification

cd orchestrator && uv run pytest tests/integration/test_create_repository.py -v
