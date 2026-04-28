# Secrets Manifest

**Milestone:** 
**Generated:** 

### Anthropic API key (Claude)

**Service:** 
**Status:** skipped
**Destination:** dotenv

1. Sign in at https://console.anthropic.com — create an account if one does not exist (organizational billing is required for sustained agentic-loop runs; a personal-tier key works for the four UAT scenarios but may rate-limit on scenario 4's burst).
2. Confirm a payment method is on file (Settings → Billing). Without billing, key creation is blocked.
3. Navigate to https://console.anthropic.com/settings/keys.
4. Click "Create Key", name it `perpetuity-m005-acceptance`, optionally restrict to a workspace.
5. Copy the key value immediately — Anthropic does not show it again.
6. Provide to the agent via `secure_env_collect` under variable name `ANTHROPIC_API_KEY_M005_ACCEPTANCE` (the test harness reads this env var, then PUTs it into `team_secrets` for the test team during fixture setup).

### OpenAI API key (Codex CLI)

**Service:** 
**Status:** skipped
**Destination:** dotenv

1. Sign in at https://platform.openai.com — create an account if needed.
2. Confirm credit balance > $0 (Settings → Billing → Add credit). The Codex CLI is gated on a paid balance, not a free tier.
3. Navigate to https://platform.openai.com/api-keys.
4. Click "Create new secret key", name it `perpetuity-m005-acceptance`, choose project scope `default` (or a project pinned to the Codex CLI if you have one).
5. Copy the key value immediately — OpenAI does not show it again.
6. Provide to the agent via `secure_env_collect` under variable name `OPENAI_API_KEY_M005_ACCEPTANCE`.

### GitHub test org webhook delivery (S06 scenario 3)

**Service:** 
**Status:** skipped
**Destination:** dotenv

1. Either reuse the existing M004 test org or create a new GitHub organization for M005 acceptance (free tier is fine).
2. Install the existing Perpetuity GitHub App (provisioned in M004) onto this org. Note: M004's app is already production-grade — no new App needed.
3. Create a public test repository in the org (e.g. `perpetuity-m005-acceptance`) with a `main` branch and one initial commit. Connect it to a Perpetuity team via the existing M004 connection UI.
4. For S06 scenario 3, the test harness needs a personal access token to programmatically open a PR on this repo. Create a fine-grained PAT at https://github.com/settings/personal-access-tokens/new with `Contents: read+write` and `Pull requests: read+write` on the test repo only.
5. Provide to the agent via `secure_env_collect` under variable name `GITHUB_TEST_ORG_PAT` and document the test repo full name (e.g. `my-org/perpetuity-m005-acceptance`) under `GITHUB_TEST_REPO_FULL_NAME`.
