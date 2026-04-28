---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T03: Orchestrator session lifecycle: provision container, named tmux session, scrollback capture, HTTP API

Implement the per-(user, team) container provisioning + tmux session model that is the heart of M002. In `orchestrator/orchestrator/sessions.py`: (1) `provision_container(user_id, team_id) -> container_id` — looks up existing container by Docker label `user_id=<uuid>,team_id=<uuid>` (name `perpetuity-ws-<first8-team>`); if absent, creates one from `WORKSPACE_IMAGE` with `mem_limit=2g`, `pids_limit=512`, `nano_cpus=1_000_000_000`, labels set, bind-mount `/workspaces/<user_id>/<team_id>/` (T03 uses a plain bind-mount on a host directory `/var/lib/perpetuity/workspaces/<user_id>/<team_id>/` created on the fly — loopback-ext4 volume management is S02; T03 explicitly defers it but reserves the path shape). Container starts with `command: ['sleep','infinity']` so tmux sessions live inside via `docker exec`. (2) `start_tmux_session(container_id, session_id)` — `docker exec` runs `tmux new-session -d -s <session_id> -x 200 -y 50 bash`; the `-d` (detached) flag is critical — exec returns immediately, tmux owns the pty (D012). (3) `capture_scrollback(container_id, session_id) -> str` — `docker exec` runs `tmux capture-pane -t <session_id> -p -S - -E -`; result is hard-truncated to 100 KB on the orchestrator side (NEVER trust tmux to limit, per D017). (4) `kill_tmux_session(container_id, session_id)` — `docker exec` runs `tmux kill-session -t <session_id>`. (5) HTTP routes in `orchestrator/orchestrator/routes_sessions.py`: `POST /v1/sessions` body `{session_id, user_id, team_id}` → provisions container + starts tmux + writes Redis session record, returns `{session_id, container_id, tmux_session, created: true|false}`; `GET /v1/sessions?user_id=&team_id=` → reads Redis; `DELETE /v1/sessions/{id}` → kills tmux session, deletes Redis record (container reaped by S04 idle reaper); `POST /v1/sessions/{id}/scrollback` → returns scrollback (used by attach in T04); `POST /v1/sessions/{id}/resize` body `{cols, rows}` → `tmux refresh-client -t <session_id> -C cols,rows` (default tmux semantics — smaller of attached clients wins, per D017). All routes are gated by the shared-secret auth from T02. Ownership check: orchestrator does NOT enforce ownership — backend does (orchestrator trusts the backend's shared secret). ASSUMPTION (auto-mode): host directory `/var/lib/perpetuity/workspaces/` is mounted into the orchestrator container at the same path so the orchestrator can `mkdir -p` it before bind-mounting into workspace containers.

## Inputs

- ``orchestrator/orchestrator/auth.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/config.py``

## Expected Output

- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/routes_sessions.py``
- ``orchestrator/orchestrator/main.py` (modified — registers session routes)`
- ``orchestrator/tests/integration/test_sessions_lifecycle.py``

## Verification

Integration test `test_sessions_lifecycle.py` (real Docker + real Redis, image=`perpetuity/workspace:test`): (a) `POST /v1/sessions` for new (user_a, team_a, sid_1) → 200, `created:true`; assert `docker ps --filter label=user_id=<uuid>` shows the container; assert `docker exec <c> tmux ls` lists `sid_1`. (b) `POST /v1/sessions` for same (user_a, team_a, sid_2) → 200, `created:false`; SAME container_id; tmux ls now shows both sessions (R008 multi-tmux per container). (c) `POST /v1/sessions/{sid_1}/scrollback` → 200, body `{scrollback: '...'}` (initial empty or shell prompt). (d) `POST /v1/sessions/{sid_1}/resize` cols=80,rows=24 → 200; assert no error log. (e) `DELETE /v1/sessions/{sid_1}` → 200; assert tmux ls no longer lists sid_1, but sid_2 still alive (kill is per-tmux-session not per-container). (f) `GET /v1/sessions?user_id=<a>&team_id=<a>` returns `[sid_2]` only. (g) Scrollback hard-cap: `docker exec` writes `yes | head -c 200000 > /tmp/x; cat /tmp/x` into sid_2; `POST .../scrollback` returns body length ≤ 100 KB.

## Observability Impact

INFO `container_provisioned container_id=<uuid> user_id=<uuid> team_id=<uuid>`. INFO `session_created session_id=<uuid> container_id=<uuid>`. WARNING `tmux_session_orphaned session_id=<uuid> container_id=<uuid>` if tmux ls fails to find a session that Redis says exists. UUIDs only. Failure modes: Docker unreachable → 503 `docker_unreachable`; container provision fails with `image_pull_failed` (only if image was somehow removed post-boot) → 500 `volume_mount_failed` placeholder for now (S02 owns the real loopback errors). Negative tests: malformed UUIDs in body → 422; missing `X-Orchestrator-Key` → 401; resize on non-existent session → 404.
