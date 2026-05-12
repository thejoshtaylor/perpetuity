---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T01: Pre-flight — confirm GitHub App config + compose stack health

Any unfinished prerequisite turns a 'find a real-runtime bug' exercise into 'debug environment misconfiguration' — eliminate the latter first. Visit https://github.com/settings/apps/<app-slug>/permissions; take a screenshot showing OAuth enabled + Contents: R/W (or equivalent); save to evidence dir. Run docker compose ps and confirm all five services healthy. Hit GET /api/v1/health on backend; hit GET /v1/health on orchestrator. Verify head migration is s17_github_user_oauth_tokens via cd backend && uv run alembic current. Write 00-preflight.md listing each check + result.

## Inputs

- `S06's runbook docs/runbooks/m006-github-oauth-setup.md`
- `Compose stack credentials in .env`

## Expected Output

- `GitHub App permissions screenshot (OAuth + Contents: R/W)`
- `docker compose ps output showing 5 healthy services`
- `Backend + orchestrator health endpoint responses`
- `alembic current = s17_github_user_oauth_tokens`
- `00-preflight.md documenting all four checks`

## Verification

test -f .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md && grep -q s17_github_user_oauth_tokens .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md
