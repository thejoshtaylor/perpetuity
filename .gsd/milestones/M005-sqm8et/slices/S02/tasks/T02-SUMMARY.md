---
id: T02
parent: S02
milestone: M005-sqm8et
key_files:
  - orchestrator/workspace-image/Dockerfile
  - orchestrator/orchestrator/routes_exec.py
  - orchestrator/orchestrator/main.py
  - orchestrator/tests/integration/test_routes_exec.py
  - backend/app/alembic/versions/s12_seed_direct_workflows.py
  - backend/app/api/workflows_seed.py
  - backend/app/crud.py
  - backend/tests/api/test_workflows_seed.py
  - backend/tests/migrations/test_s12_seed_direct_workflows_migration.py
key_decisions:
  - util-linux script(1) syntax `script -q -e -c '<cmd>' /dev/null` (NOT BSD `script -q /dev/null sh -c '<cmd>'`) is the correct TTY wrapper on Ubuntu 24.04; `-e` is required for child exit-code propagation (MEM427)
  - System-workflow seed payload duplicated between `backend/app/api/workflows_seed.SYSTEM_WORKFLOWS` (runtime) and `s12_seed_direct_workflows._SYSTEM_WORKFLOWS` (migration) — alembic shouldn't depend on the app package import surface (MEM428)
  - Wired seed call into `crud.py` not `routes/teams.py` because crud is the actual code path that writes Team rows; both `create_user_with_personal_team` and `create_team_with_admin` seed BEFORE the final `session.commit()` so seed failures roll the team + admin back
  - `_build_script_cmd` keeps bare `$NAME`/`${NAME}` cmd entries unquoted-but-quoted-as-`"$NAME"` so `/bin/sh` expands them against the env dict — locks in MEM274 secret-passing pattern (API keys never appear in cmd list)
  - Hard heap cap of 5 MiB on stdout drain with 600s ceiling on timeout_seconds — protects orchestrator heap if a CLI streams forever; backend's step_runs.stdout column accepts any size but realistic shape is far below this
duration: 
verification_result: passed
completed_at: 2026-04-29T02:23:03.401Z
blocker_discovered: false
---

# T02: Pinned claude/codex CLIs in workspace image, added orchestrator one-shot exec endpoint with util-linux script(1) TTY wrapper, and auto-seeded `_direct_claude`/`_direct_codex` system workflows for every team

**Pinned claude/codex CLIs in workspace image, added orchestrator one-shot exec endpoint with util-linux script(1) TTY wrapper, and auto-seeded `_direct_claude`/`_direct_codex` system workflows for every team**

## What Happened

Three deliverables landed in a single transaction.

(1) `orchestrator/workspace-image/Dockerfile` — pinned `@anthropic-ai/claude-code@1.0.30` and `@openai/codex@0.20.0` via `npm install -g`, then added two TTY smoke checks (`script -q -e -c '<cli> --version' /dev/null`) so an upstream CLI regressing on TTY discipline fails the image build rather than silently breaking inside a Celery task. Image rebuild verified end-to-end: claude prints `1.0.30 (Claude Code)` and codex prints `codex-cli 0.20.0` through the wrapper.

(2) `orchestrator/orchestrator/routes_exec.py` — new `POST /v1/sessions/{session_id}/exec` route. The request shape (UUID user/team, cmd argv, env dict, timeout_seconds capped at 600, optional cwd, free-form action discriminator) is enforced by pydantic with hard caps on `_MAX_CMD_ENTRIES=64`, `_MAX_CMD_ENTRY_LEN=8KB`, `_MAX_ENV_ENTRIES=64`, `_MAX_ENV_KEY_LEN=128`, `_MAX_ENV_VALUE_LEN=64KB`. Cmd argv is shell-quoted via `shlex.quote`, EXCEPT bare `$NAME`/`${NAME}` references which are passed as `"$NAME"` so `/bin/sh` (invoked by `script -c`) expands them against the env dict — that's the MEM274 secret-passing pattern: API keys flow only through the env frame, never the cmd list. `_exec_collect_with_env` opens the docker exec with `tty=True` (required because the script wrapper produces a merged stdout/stderr stream and claude/codex refuse on tty=False), drains stdout up to a 5 MiB heap cap, and times out via `asyncio.wait_for` to surface a clean 504 `oneshot_exec_timeout` rather than a wedged exec stream. Two structured INFO logs emit per call: `oneshot_exec_started session_id=<uuid> user_id=<uuid> team_id=<uuid> action=<...>` and `oneshot_exec_completed session_id=<uuid> exit=<n> duration_ms=<n>`. Cmd argv, env values, and stdout never appear in the log stream — the integration test for secret discipline scrapes `docker logs` for the marker and asserts absence. Wired into `orchestrator/main.py` via `app.include_router(exec_router)`.

(3) `backend/app/alembic/versions/s12_seed_direct_workflows.py` + `backend/app/api/workflows_seed.py` — the seed payload (`_direct_claude` / `_direct_codex`, both `system_owned=TRUE` `scope='user'` with one step at index 0 and config `{"prompt_template": "{prompt}"}`) is the single source of truth in `workflows_seed.SYSTEM_WORKFLOWS`. The runtime helper `seed_system_workflows(session, team_id)` runs `INSERT ... ON CONFLICT (team_id, name) DO NOTHING RETURNING id` so re-runs are no-ops; partial-seed recovery (only `_direct_claude` exists) is exercised by the test suite. Both runtime team-create paths in `crud.py` (`create_user_with_personal_team` and `create_team_with_admin`) call the helper BEFORE `session.commit()` so a seed failure rolls the team + admin membership back rather than leaving an orphan team without dashboard buttons. The s12 migration duplicates the payload inline (deliberate — alembic shouldn't depend on the app package import surface) and walks every `team` row to backfill pre-existing teams.

**Two debugging adventures during execution:**

(a) The integration tests boot orchestrator with `DATABASE_URL=postgresql://...:5432/${POSTGRES_DB}` defaulting to `app`. The dev compose stack uses `perpetuity_app` instead, so the workspace_volume table didn't exist and every exec returned 503 `workspace_volume_store_unavailable`. Fixed by exporting `POSTGRES_DB=perpetuity_app` for the test invocation — the `_create_pg_user_team` helper inside the test suite already inherits this env var, so the user/team rows wrote to the right database.

(b) The original task plan said wrap as `script -q /dev/null sh -c '<body>'` (BSD `script` syntax). Ubuntu 24.04's `script` is util-linux which uses `script [opts] [file]` — the BSD form errors with `script: unexpected number of arguments`. Switched to `script -q -e -c '<body>' /dev/null`. The `-e` (`--return`) flag is critical: without it, script always exits 0 regardless of the child's exit code, masking CLI failures (e.g. `cli_nonzero` would show as success). The non-zero-exit integration test caught this. Captured as MEM427.

**Wiring decision (MEM428):** kept the seed payload duplicated between `workflows_seed.py` and `s12_seed_direct_workflows.py` rather than importing across the alembic boundary. Alembic runs with a different working directory and partial PYTHONPATH; ~20 lines of duplicated dict literal is cheaper than fixing import surface every time the app package shifts.

## Verification

All three suites green against the live compose stack:

- `cd orchestrator && POSTGRES_DB=perpetuity_app uv run pytest tests/integration/test_routes_exec.py -v` → 8 passed in 19.52s. Covers happy path (`echo "$WHAT"` env passthrough), non-zero exit propagation (`exit 7` → exit_code=7), secret discipline (`sk-ant-DO-NOT-LOG-…` never appears in `docker logs`), timeout (`sleep 5` w/ timeout=2 → 504 `oneshot_exec_timeout` in <5s), unauthorized (no `X-Orchestrator-Key` → 401), validation oversized env / malformed UUID (→ 422), container reuse across two exec calls (→ 1 workspace container per (user, team)).
- `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflows_seed.py -v` → 6 passed in 0.15s. Covers fresh-team double insert, idempotent re-seed (returns 0), partial-seed recovery (only missing row added), team isolation, runtime team-create wiring (both crud paths), int-count contract.
- `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s12_seed_direct_workflows_migration.py -v` → 6 passed in 0.70s. Covers s11→s12 backfill, step payload shape, idempotent re-upgrade, partial-seed recovery preserves existing rows, downgrade removes only system-owned rows, post-migration teams are NOT auto-seeded by the migration (runtime helper covers them).
- `docker build -t perpetuity/workspace:m005s02-test orchestrator/workspace-image` → DONE. Both smoke checks emitted `1.0.30 (Claude Code)` and `codex-cli 0.20.0` through the `script -q -e -c '<cmd>' /dev/null` wrapper. The TTY discipline is now image-build-asserted.
- `docker compose build orchestrator` → image rebuilt twice during execution (after each script-syntax fix); final image runs all 8 routes_exec integration tests green.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd orchestrator && POSTGRES_DB=perpetuity_app uv run pytest tests/integration/test_routes_exec.py -v` | 0 | ✅ pass | 19520ms |
| 2 | `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflows_seed.py -v` | 0 | ✅ pass | 150ms |
| 3 | `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s12_seed_direct_workflows_migration.py -v` | 0 | ✅ pass | 700ms |
| 4 | `docker build -t perpetuity/workspace:m005s02-test orchestrator/workspace-image` | 0 | ✅ pass | 95000ms |
| 5 | `docker compose build orchestrator` | 0 | ✅ pass | 30000ms |

## Deviations

Two adaptations during execution:

1. **Test fixture email TLD.** `test_create_team_with_admin_seeds_workflows` originally used `@test.local` which pydantic's email validator rejects as a reserved TLD. Switched to `@example.com` matching the convention in `tests/integration/test_m005_s01_team_secrets_e2e.py`.

2. **`script(1)` invocation syntax.** Task plan said wrap as `script -q /dev/null sh -c '<body>'` (BSD form). Ubuntu 24.04 ships util-linux script which uses `script [opts] [file]` — the BSD form raises `script: unexpected number of arguments`. Corrected to `script -q -e -c '<body>' /dev/null` and updated both the orchestrator wrapper (`_build_script_cmd`) and the Dockerfile smoke check. The `-e` flag was an additional hidden requirement — without it script always exits 0, masking CLI failures. Captured as MEM427.

Neither is a slice-plan-invalidating blocker — the contract (TTY discipline through the workspace container, secrets in env not cmd, exit code propagation) holds, just expressed in the platform-correct syntax.

## Known Issues

- The `perpetuity/workspace:test` image used by integration tests is the older 3-day-old build (no claude/codex CLIs); current routes_exec tests use only `echo`/`sleep`/`sh` so this works. T03 will need to either bump the test fixture to `perpetuity/workspace:m005s02-test` (just built locally) or wire `WORKSPACE_IMAGE` through the test boot env when the test exercises the actual claude/codex binaries.
- `script -e` propagation has only been verified for the immediate exit code of the command body. If `claude`/`codex` daemonize a child or trap SIGTERM during a future timeout, behavior is undefined; T03 should add an integration test that covers a CLI killed mid-stream.
- The orchestrator integration tests require `POSTGRES_DB=perpetuity_app` exported in the runner environment — without it the boot uses `app` which lacks the workspace_volume table and every test returns 503. This is a pre-existing pattern from the reaper / sessions_lifecycle suites; consider documenting in `orchestrator/tests/integration/conftest.py` or adding a default to the boot helper.

## Files Created/Modified

- `orchestrator/workspace-image/Dockerfile`
- `orchestrator/orchestrator/routes_exec.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/tests/integration/test_routes_exec.py`
- `backend/app/alembic/versions/s12_seed_direct_workflows.py`
- `backend/app/api/workflows_seed.py`
- `backend/app/crud.py`
- `backend/tests/api/test_workflows_seed.py`
- `backend/tests/migrations/test_s12_seed_direct_workflows_migration.py`
