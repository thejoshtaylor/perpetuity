# S01: Orchestrator service + tmux-durable WS terminal (the proof-first slice) — UAT

**Milestone:** M002-jy6pde
**Written:** 2026-04-25T10:16:32.246Z

# M002-jy6pde / S01 — UAT

**Goal:** Prove that a signed-up user can open a cookie-authed WebSocket terminal to their isolated per-(user, team) workspace, run a shell command, observe its output, restart the orchestrator service mid-session, reconnect to the SAME session ID, see the prior scrollback AND continue typing into the SAME shell process. Plus negative paths (auth, ownership, enumeration, orchestrator-unavailable). Plus regression: zero email/full_name leakage in logs.

## Preconditions

- Working tree at the head of S01.
- Repo root: `/Users/josh/code/perpetuity`.
- Docker Desktop running.
- `docker compose up -d db redis orchestrator` shows all three services healthy.
- Workspace images present: `docker images | grep perpetuity/workspace` shows `:latest` AND `:test`.
- `.env` contains `REDIS_PASSWORD`, `ORCHESTRATOR_API_KEY`, `ORCHESTRATOR_BASE_URL=http://orchestrator:8001`, `WORKSPACE_IMAGE=perpetuity/workspace:latest`.
- The running orchestrator container is built from the current source (rebuild with `docker compose build orchestrator && docker compose up -d --force-recreate orchestrator` if any orchestrator code changed since last boot — see MEM126).

## Test 1 — Slice acceptance (the demo bar)

This is the canonical end-to-end exercise. One pytest run covers it.

**Steps:**
1. From repo root: `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v`.

**Expected:**
- `1 passed in <≤60>s`. (Reference: 19.16s on the dev box.)
- The test internally exercises:
  - `POST /api/v1/auth/signup` for a fresh `<random>@example.com` user → 200.
  - `POST /api/v1/auth/login` with the cookie jar → 200, `session` cookie returned.
  - `POST /api/v1/sessions` with `team_id` of the user's personal team → 200 with `{session_id, team_id, created_at}`.
  - `WS /api/v1/ws/terminal/{session_id}` (cookie attached as explicit `Cookie:` header) → first frame is `{type:"attach", scrollback:"…"}`.
  - Send `{type:"input", bytes:base64("echo hello\n")}` → within 10s, a `{type:"data", bytes:…}` frame whose b64-decoded + ANSI-stripped payload contains `hello`.
  - Capture `pid_before` via `echo $$\n`.
  - Close the WS.
  - Subprocess `docker compose restart orchestrator`; poll `docker compose ps` until orchestrator is healthy (≤30s).
  - Reattach to the SAME `session_id` with same cookies → `attach` frame's scrollback (b64-decoded + ANSI-stripped) contains `hello`.
  - Send `echo $$\n` → response data contains `pid_before` (proves SAME shell process — tmux durability held).
  - Send `echo world\n` → response data contains `world`.
  - `DELETE /api/v1/sessions/{sid}` → 200.
  - `docker compose logs orchestrator backend` captured to `/tmp/m002_s01.log`; assertion: ZERO occurrences of seeded email AND zero occurrences of seeded full_name.

## Test 2 — Skip-when-Docker-unreachable

**Steps:**
1. `cd backend && SKIP_INTEGRATION=1 uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v`.

**Expected:** `1 skipped`. The test never breaks unit-only runs.

## Test 3 — Component sanity (run on a slow machine to localize failures)

**Steps:**
1. `cd orchestrator && uv run pytest tests/unit -v` → 11 passed (auth + health + log-redaction).
2. `cd orchestrator && uv run pytest tests/integration/test_redis_client.py -v` → 8 passed.
3. `cd orchestrator && uv run pytest tests/integration/test_image_pull.py -v` → 3 passed.
4. `cd orchestrator && uv run pytest tests/integration/test_sessions_lifecycle.py -v` → 12 passed.
5. `cd orchestrator && uv run pytest tests/integration/test_ws_bridge.py -v` → 9 passed.
6. `cd backend && POSTGRES_PORT=5432 POSTGRES_SERVER=localhost uv run pytest tests/api/routes/test_sessions.py -v` → 11 passed.

**Expected:** All 54 component tests pass without changes to compose state.

## Test 4 — Negative paths (covered inside the e2e suite, listed here for the human reviewer)

**Auth:**
- WS attach without cookie → close `1008 missing_cookie`.
- Backend HTTP without auth on `POST /api/v1/sessions` → 401.

**Ownership / enumeration:**
- User B logs in, attaches to user A's `session_id` → close `1008 session_not_owned`.
- Any user attaches to a never-existed `session_id` → close `1008 session_not_owned` (IDENTICAL close to the user-B-on-A's-sid case — no enumeration).
- After owner DELETEs their session, re-attaching that same sid → close `1008 session_not_owned` (same shape).

**Team membership:**
- User A logs in and posts `POST /api/v1/sessions` with user B's personal team_id → 403.

**Orchestrator unavailable:**
- `docker stop perpetuity-orchestrator-1`; `POST /api/v1/sessions` → 503; WS attach → close `1011 orchestrator_unavailable`. `docker start` → both succeed again.

**Two-key shared-secret rotation (code path exists for S05's full acceptance):**
- Unit test with `ORCHESTRATOR_API_KEY=current` + `ORCHESTRATOR_API_KEY_PREVIOUS=old` accepts requests presenting either key with HTTP 200 / WS accept.

## Test 5 — Observability log discipline (manual spot check)

**Steps:**
1. After Test 1 completes, run: `docker compose logs orchestrator | grep -E 'image_pull_ok|session_created|session_attached|session_detached|orchestrator_starting|orchestrator_ready'`.

**Expected:** All six INFO keys appear at least once. Every line containing identifiers shows ONLY UUIDs (no email, no full_name, no team slug). The `image_pull_ok` line shows `source=local` for cached images.

## Edge cases exercised

- **Multi-tmux per container:** Two separate `POST /v1/sessions` for the same (user, team) yield SAME container_id (`created:false` on the second) and TWO tmux sessions inside that container — covered by `test_sessions_lifecycle::test_b_*`.
- **Scrollback hard-cap:** Orchestrator caps capture-pane output at 100 KB even when tmux's `history-limit` is bumped to 200 000 lines — covered by `test_sessions_lifecycle::test_g_*` (NEVER trusts tmux to limit per D017).
- **Disconnect+reconnect (no restart):** WS disconnect then reconnect to SAME sid yields a second `attach` frame whose scrollback contains the prior session's output — covered by `test_ws_bridge::test_disconnect_reconnect_preserves_scrollback`. This is the precursor to the orchestrator-restart proof.
- **Shell exit:** `input: exit\n` produces `{type:"exit", code:<int>}` then close `1000` — covered by `test_ws_bridge::test_shell_exit_emits_exit_frame_and_closes_1000`.
- **Malformed JSON:** Non-JSON bytes from the WS client → close `1003 malformed_frame`.
- **Unknown frame type:** `{type:"telepathy"}` is logged and ignored (forward-compat); subsequent `input` still works.

## Pass criteria

The slice passes UAT iff:
1. Test 1 (the e2e) returns `1 passed` with wall-clock ≤ 60s.
2. Tests 3.1–3.6 all green (54 component tests).
3. Test 5 confirms required observability keys with UUID-only identifiers.
4. The log-redaction step inside Test 1 reports zero email/full_name matches.
