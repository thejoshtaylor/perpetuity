# M002-jy6pde: Terminal Infrastructure

**Vision:** Per-(user, team) Docker workspace containers with persistent volumes, accessed via cookie-authed WebSocket terminal sessions, durable across orchestrator restarts via tmux. A new top-level orchestrator service owns the Docker socket; the backend proxies bytes to it. Backend-only delivery — frontend xterm.js UI is deferred. This milestone also lands the system-wide settings infrastructure (admin-only key/value store) needed for runtime-adjustable workspace volume caps.</vision>
<parameter name="dependsOn">["M001-6cqls8"]

## Success Criteria

- A signed-up user can connect WS to /api/v1/ws/terminal/<new_session_id>, run `echo hello`, and see `hello` in the data frame.
- After `docker compose restart orchestrator`, the user can reconnect to the SAME session_id and see prior scrollback in the attach frame plus continue typing into the same shell with env/cwd preserved.
- system_admin can PUT /api/v1/admin/settings/workspace_volume_size_gb to a smaller value than at least one existing volume's size_gb; response is 200 with a warning payload listing affected (user, team, current size_gb, current usage); affected volumes keep their old cap; new provisions use the new cap.
- Multiple WS sessions for the same (user, team) all attach to the single shared container at /workspaces/<user_id>/<team_id>/, each as a distinct tmux session, and see each other's filesystem changes.
- GET /api/v1/sessions returns the caller's currently-live (user, team) sessions; DELETE /api/v1/sessions/{id} kills the tmux session.
- Idle reaper kills containers ONLY when both Redis last_activity exceeds the configured idle timeout AND the in-memory active-WS map confirms no live attach.
- Orchestrator pulls perpetuity/workspace:latest once on startup; pull failure is a boot blocker (no lazy pulls at session-create time).
- All sensitive log lines emit user/team/session/container IDs as UUIDs only — never email or full name.
- Full backend + orchestrator integration suite passes against real Postgres + real Redis + real Docker daemon. Suite runtime ≤ 60s wall clock for the integration set.
- Migrations for system_settings and workspace_volume round-trip cleanly up/down (M001 MEM016 lock-hazard pattern preserved).

## Slices

- [x] **S01: S01** `risk:high` `depends:[]`
  > After this: Signup as a fresh user via M001 endpoints, then open a WS to /api/v1/ws/terminal/<new-uuid> with the session cookie. Observe `{type:"attach",scrollback:""}` then send `{type:"input",bytes:"echo hello\n"}` and receive `{type:"data",bytes:"hello\r\n"}` (or equivalent line). Run `docker compose restart orchestrator`. Reconnect to the SAME session_id; observe `{type:"attach",scrollback:"...hello..."}` and confirm `echo $$` returns a stable shell PID across reconnects. This is exercised end-to-end by an automated integration test against the real compose stack — no mocks.

- [x] **S02: S02** `risk:high` `depends:[]`
  > After this: Provision a workspace with size_gb=1; attach a shell; run `dd if=/dev/zero of=/workspaces/<u>/<t>/big bs=1M count=1100` and observe ENOSPC at ~1 GB. Other workspaces' .img files are untouched and their writes still succeed. The workspace_volume row matches what's on disk.

- [x] **S03: S03** `risk:medium` `depends:[]`
  > After this: PUT /api/v1/admin/settings/workspace_volume_size_gb {value:1} as a system_admin when at least one existing workspace_volume has size_gb=4 — response is 200 with `{warnings:[{user_id,team_id,size_gb:4,usage_bytes:N}, ...]}`; the affected row is unchanged in DB; a fresh signup's first POST /api/v1/sessions creates a workspace_volume with size_gb=1 and a ~1 GB .img file.

- [x] **S04: S04** `risk:medium` `depends:[]`
  > After this: User opens two WS sessions, both attach to distinct tmux sessions in the same container; one session writes a file, the other sees it via `ls`. GET /api/v1/sessions returns both. DELETE one — the tmux session is killed, GET returns one. After idle_timeout_seconds with no I/O and no live attach, the reaper kills the remaining tmux session and reaps the container; the volume persists; next POST /api/v1/sessions for the same (user, team) re-provisions the container and remounts the existing volume.

- [x] **S05: S05** `risk:medium` `depends:[]`
  > After this: Acceptance test: signup → POST /api/v1/sessions → WS attach → `echo hello` → restart orchestrator → reconnect same session_id → observe `hello` in scrollback → `echo world` in the same shell (`echo $$` PID stable) → DELETE the session → wait idle_timeout_seconds → assert container reaped via `docker ps`. Two-key rotation test: orchestrator with both keys set, two requests with different keys, both succeed. Ownership test: user B WS to user A's session_id and to a never-existed session_id both close 1008/session_not_owned identically. Regression test: log scan finds zero email/name leaks across all M002 log lines.

## Boundary Map

## Boundary Map (cross-slice surfaces)

- **Orchestrator HTTP API (orchestrator:8001/v1)** — created in S01, extended in S02 (volumes), S03 (settings read), S04 (sessions list/delete, scrollback). Shared-secret-authed.
  - `POST /v1/sessions` — provision container if absent + start tmux session for given session_id, user_id, team_id.
  - `GET /v1/sessions?user_id=&team_id=` — list caller's live sessions (S04).
  - `DELETE /v1/sessions/{id}` — kill tmux session (S04).
  - `POST /v1/sessions/{id}/resize` — resize attached pane (S01 minimum, hardened S04).
  - `POST /v1/sessions/{id}/scrollback` — capture-pane up to 100 KB (S01 inline as part of attach; S04 explicit endpoint).
  - `WS /v1/sessions/{id}/stream` — bidirectional bridge to tmux exec.
- **Orchestrator → Docker socket** — only S01+ (CAP_SYS_ADMIN added in S02 for losetup/mount).
- **Orchestrator → Redis** — S01 introduces (session registry, last_activity heartbeat).
- **Orchestrator → Postgres** — read-only on `workspace_volume` (S02) and `system_settings` (S03); read-only on `team_member` for ownership checks (S01).
- **Backend public API (backend:8000/api/v1)** — created/extended:
  - `POST /api/v1/sessions` — S01 (creates session_id, calls orchestrator).
  - `GET /api/v1/sessions` — S04.
  - `DELETE /api/v1/sessions/{id}` — S04.
  - `WS /api/v1/ws/terminal/{session_id}` — S01.
  - `GET/PUT /api/v1/admin/settings[/{key}]` — S03.
- **Backend → orchestrator (shared secret)** — S01 introduces; S05 hardens with two-key rotation acceptance test.
- **Postgres tables** — `system_settings` (S03), `workspace_volume` (S02). Both follow M001 alembic discipline (s04_…, s05_… migrations under backend/app/alembic/versions/).
- **Compose** — `redis:7-alpine` + `orchestrator` services added in S01 (compose plumbing must land before S01's WS bridge can be tested end-to-end).
- **Workspace base image** — `perpetuity/workspace:latest` (Ubuntu + git + node + python + bash + tmux). Built in S01; size adjustments deferred. Test variant `perpetuity/workspace:test` lives in `orchestrator/tests/fixtures/`.
- **Frame protocol (WS)** — JSON frames defined in S01: server `{type:"attach",scrollback}`, `{type:"data",bytes}`, `{type:"exit",code}`, `{type:"detach",reason}`; client `{type:"input",bytes}`, `{type:"resize",cols,rows}`. Locked at end of S01 — downstream slices must not change shape.
- **Observability log keys** — taxonomy in milestone CONTEXT appendix; UUIDs only in `actor_id`, `target_user_id`, `team_id`, `session_id`, `container_id`. Enforced from S01.
