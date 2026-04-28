# S01: Per-team AI credentials at rest — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-28T22:30:59.155Z

# UAT — M005/S01: Per-team AI credentials at rest

## Preconditions
- Compose stack up (`db`, `redis`, `orchestrator` healthy).
- Backend image built with the `s09_team_secrets` migration baked in (`docker compose build backend`).
- A team with at least two members: one with role=`admin`, one with role=`member`.
- Team admin signed in to the frontend.
- A clean Postgres database routable via `POSTGRES_DB` env var (use `perpetuity_app` if the shared `app` DB carries z2y/z3b CRM contamination — MEM420).

## UAT-1 — Paste-once happy path (Claude)
1. As team admin, navigate to `/teams/{team_id}` (team detail page).
2. Locate the **AI Credentials** panel; verify both `claude_api_key` and `openai_api_key` rows render with **Not set** badges.
3. Click **Replace** next to `claude_api_key`. The paste-once dialog opens with a password-type input + eye toggle.
4. Paste a valid key shape: `sk-ant-` followed by ≥34 random characters (total length ≥ 40). Click **Save**.
5. **Expected:** Dialog closes; the panel refreshes; `claude_api_key` row now shows **Set** with an `updated_at` timestamp; no value text is rendered anywhere in the DOM.
6. Reload the page. **Expected:** `claude_api_key` still shows **Set**; the panel never echoes the plaintext.
7. Open the browser devtools network tab and re-issue the GET. **Expected:** response body contains `{key, has_value: true, sensitive: true, updated_at}` only — no `value` field.

## UAT-2 — Paste-once happy path (OpenAI)
1. Repeat UAT-1 step 3–7 but for `openai_api_key` with a value of shape `sk-` followed by ≥37 random chars.
2. **Expected:** identical behavior — **Set** badge appears, no value flows back.

## UAT-3 — Replace bumps updated_at
1. After UAT-1 completes, note the `updated_at` shown next to `claude_api_key`.
2. Click **Replace** again, paste a different valid `sk-ant-...` value, Save.
3. **Expected:** Updated_at advances. has_value stays `true`. Value never appears in UI or response body.

## UAT-4 — DELETE clears the row
1. Click **Delete** next to `claude_api_key`. Confirm.
2. **Expected:** Panel refreshes; `claude_api_key` row reverts to **Not set**; subsequent GET-single (`/api/v1/teams/{team_id}/secrets/claude_api_key`) returns 404 with `{detail: "team_secret_not_set", key: "claude_api_key"}`. Audit log shows `team_secret_deleted team_id=... key=claude_api_key`.

## UAT-5 — Non-admin gets 403 on PUT
1. Sign in as a team member (role=`member`).
2. Navigate to `/teams/{team_id}`. **Expected:** AI Credentials panel renders both keys with their badges but **no Replace/Delete buttons** are visible (read-only mode).
3. Manually issue a PUT request to `/api/v1/teams/{team_id}/secrets/claude_api_key` with `{"value": "sk-ant-..."}`.
4. **Expected:** HTTP 403 with body `{detail: "team_admin_required"}`. No row created. No INFO log `team_secret_set` emitted (negative-assert).

## UAT-6 — Validator rejection (bad prefix)
1. As team admin, click **Replace** next to `claude_api_key`.
2. Paste a value with the wrong prefix shape — e.g. `xai-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` (40 chars but starts with `xai-`, not `sk-ant-`).
3. Save.
4. **Expected:** HTTP 400 with `{detail: {detail: "invalid_value_shape", key: "claude_api_key", hint: "bad_prefix"}}`. The frontend renders the discriminator (`invalid_value_shape: bad_prefix`) inline. Row is NOT updated. No `team_secret_set` log emitted. The pasted value never appears in the response body or logs.

## UAT-7 — Validator rejection (too short)
1. As team admin, click **Replace** next to `openai_api_key`.
2. Paste a value with the right prefix but too short — e.g. `sk-shortvalue`.
3. Save.
4. **Expected:** HTTP 400 with `hint: "too_short"`. Row not updated.

## UAT-8 — Unknown key (registry miss)
1. Manually issue PUT to `/api/v1/teams/{team_id}/secrets/grok_api_key` (not in registry).
2. **Expected:** HTTP 400 with `{detail: "unregistered_key", key: "grok_api_key"}`. No row created.

## UAT-9 — get_team_secret round-trip (downstream contract)
1. As **system admin** (NOT team admin) in a **local** environment, paste a Claude key via UAT-1, then issue GET to the local-only test surface `/api/v1/teams/{team_id}/secrets/claude_api_key/_test_decrypt`.
2. **Expected:** HTTP 200 with body `{value: "<original plaintext>"}`. The endpoint must NOT exist in any non-local deploy (verify via `/openapi.json` — endpoint should not appear). A team admin (non-system_admin) calling the same path returns 403.

## UAT-10 — Decrypt failure surfaces as 503
1. As system admin, manually corrupt the row via psql:
   ```sql
   UPDATE team_secrets SET value_encrypted = '\xdeadbeef'::bytea WHERE team_id = '<team_uuid>' AND key = 'claude_api_key';
   ```
2. Call the test surface from UAT-9.
3. **Expected:** HTTP 503 with `{detail: "team_secret_decrypt_failed", key: "claude_api_key"}`. Backend logs contain ERROR line `team_secret_decrypt_failed team_id=<team_uuid> key=claude_api_key` (NO value or value_prefix in the log).

## UAT-11 — Missing key surfaces as 404 (downstream contract)
1. With no row stored, call `/api/v1/teams/{team_id}/secrets/claude_api_key/_test_decrypt` (system admin, local).
2. **Expected:** HTTP 404 with `{detail: "team_secret_not_set", key: "claude_api_key"}`. Downstream callers in S02+ catch `MissingTeamSecretError` and surface as step failure with `error_class='missing_team_secret'`.

## UAT-12 — CASCADE delete on team removal
1. As system admin, store a key via UAT-1, then delete the team via `DELETE /api/v1/teams/{team_id}`.
2. **Expected:** All `team_secrets` rows for that team_id are removed (FK CASCADE). Verify with `SELECT count(*) FROM team_secrets WHERE team_id = '<deleted_team_uuid>'` returns 0.

## UAT-13 — Redaction sweep clean
1. After UATs 1–12, run `docker compose logs backend > /tmp/backend.log` and `bash scripts/redaction-sweep.sh /tmp/backend.log`.
2. **Expected:** Sweep emits 7 PASS lines including `PASS: no Anthropic API key prefix (sk-ant-) in log paths` and `PASS: no OpenAI API key prefix (sk-) in log paths`. No FAIL lines.

## Edge cases verified
- DELETE is idempotent — calling DELETE twice on a not-set key returns 404 the first time and 404 the second; never crashes.
- GET-list always returns one row per registered key (both `claude_api_key` and `openai_api_key`) even if neither is set yet — frontend can render both rows on first load.
- Skip-guard fixture (MEM162) on the e2e correctly skips with a rebuild instruction when the s09 revision is missing from `backend:latest`.
- `model_validate(team_secret_row)` cannot accidentally serialize ciphertext — `value_encrypted` is structurally absent from `TeamSecretPublic` and `TeamSecretStatus`.
