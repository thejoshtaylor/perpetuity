# M003-umluob: Terminal Infrastructure

**Vision:** Every (user, team) pair gets its own isolated Docker container with a persistent loopback-backed volume, a long-lived tmux-rooted shell session model that survives both browser disconnects and orchestrator restarts, and a cookie-authenticated WebSocket bridge that proxies bytes through a privileged orchestrator service to a tmux pane inside the user's container. The milestone ships the operator surface (resource limits, sysadmin-adjustable volume quotas via system_settings) needed to keep the host honest, and is proven end-to-end against the real compose stack with no mocked Docker. Backend-only — no frontend terminal UI in this milestone. Depends on M001 (auth/sessions) and M002 (teams) being shipped — cookie auth on the WS endpoint and (user_id, team_id) container scoping both rely on those contracts.

## Success Criteria

- Orchestrator service exists in docker-compose.yml; only it mounts /var/run/docker.sock; only it has CAP_SYS_ADMIN
- Redis 7-alpine service exists in docker-compose.yml, password-authed via shared-secret pattern, internal-network-only
- perpetuity/workspace:latest image builds from orchestrator/workspace.Dockerfile (Ubuntu base + git + node + python + bash + tmux); pulled/built on orchestrator startup with hard fail on failure
- Per-(user, team) container provisioning via Docker labels (user_id, team_id) with deterministic name perpetuity-ws-<first8-team>; quota of 1 container per (user, team), arbitrary tmux sessions inside
- Loopback-backed ext4 volumes under /var/lib/perpetuity/vols/; hard kernel-level size cap; grow-on-next-provision via resize2fs; shrink refused with warning naming affected pairs
- system_settings table + GET/PUT /api/v1/admin/settings API gated by role == system_admin; workspace_volume_size_gb seeded at 10
- Tmux-inside-container session model: pty survives WS disconnect AND orchestrator restart; ≥100KB scrollback restored on reattach via tmux capture-pane
- Backend WS /api/v1/ws/terminal/{session_id} cookie-authed (reuses M001 get_current_user_ws); proxies bytes to orchestrator WS /v1/sessions/{id}/stream (shared-secret-authed); JSON-framed protocol with attach/data/input/resize/exit/detach types
- Idle reaper kills tmux + container after WORKSPACE_IDLE_TIMEOUT_MINUTES of inactivity; volume persists; two-phase check against active WS attachments before kill
- Final integrated acceptance test passes against real compose stack: signup -> connect WS -> echo hello -> disconnect -> docker compose restart orchestrator -> reconnect same session -> scrollback intact -> echo world in same shell
- Full backend test suite stays green; M001 patterns preserved (cookie-auth get_current_user_ws, MEM016 autouse session-release fixture in any new alembic test, MEM017 cookie-clear discipline)

## Slices

- [x] **S01: S01** `risk:high` `depends:[]`
  > After this: Integration test against real Docker: orchestrator starts, image is present (hard-fails boot if not). POST /v1/sessions with (user_id, team_id) provisions a real container with labels user_id and team_id, mem_limit=2g, nano_cpus=2_000_000_000, pids_limit=512, name perpetuity-ws-<first8-team>; returns container_id and session_id. GET /v1/sessions?user_id=&team_id= lists by labels. Second POST for same (user, team) reuses the existing warm container. DELETE removes the container. All requests without correct X-Orchestrator-Key return 401.

- [x] **S02: S02** `risk:high` `depends:[]`
  > After this: Integration test against real Docker + real Postgres: orchestrator boots with loopback volume support; POST /v1/sessions creates a 10GB loopback ext4 volume bind-mounted at /workspaces/<user_id>/<team_id>/; writing past 10GB inside the container returns ENOSPC. Backend admin user PUTs workspace_volume_size_gb=20; the next provision (or restart of warm container) triggers resize2fs and the volume is now 20GB. Shrink preview endpoint surfaces overflow; shrink with overflow returns 4xx warning. Non-system-admin PUT returns 403. MEM016 autouse fixture released the session-scoped DB lock before s04 alembic migration ran.

- [x] **S03: S03** `risk:medium` `depends:[]`
  > After this: Integration test: provision a container, write a marker file inside the workspace volume, set last_activity in Redis to a time exceeding the idle timeout, run the reaper tick — the container is stopped, the volume persists (the .img file still exists on host). Re-provision the same (user, team) — a new container starts, the marker file is still readable at /workspaces/<user_id>/<team_id>/marker. Two-phase check test: even with stale last_activity, an active WS attachment in the in-memory map prevents reaping.

- [x] **S04: S04** `risk:high` `depends:[]`
  > After this: Integration test (the architectural bet): POST /v1/sessions creates a tmux session. Orchestrator's exec stream pipes 'echo hello\n' into stdin via the WS-style interface (this slice is HTTP-only for now; the WS bridge lands in S05). Wait for output. docker compose restart orchestrator (programmatically). After orchestrator boots and rebuilds state from Redis, GET /v1/sessions/{id}/scrollback returns content containing 'hello'. New exec attach to the same tmux session runs 'echo world' in the same shell.

- [x] **S05: S05** `risk:high` `depends:[]`
  > After this: Integration test: signup creates user + personal team; POST creates a session via the orchestrator; client opens WS to /api/v1/ws/terminal/<session_id> with the auth cookie; receives attach frame with scrollback (empty for fresh session); sends {type: 'input', bytes: 'echo hello\n'}; receives {type: 'data', bytes: 'echo hello\r\nhello\r\n...'}; sends {type: 'resize', cols: 120, rows: 30} — tmux SIGWINCH forwarded. Disconnect race test: send input, immediately close client WS — backend proxy task terminates cleanly, orchestrator-side WS closes, tmux session stays alive (verified by reattach in next test). Ownership test: user B attempts WS to user A's session_id — 1008 close 'session_not_owned'.

- [x] **S06: S06** `risk:medium` `depends:[]`
  > After this: The headline test runs end-to-end in CI against the real compose stack: docker compose up -d (db, backend, redis, orchestrator); test signs up via /api/v1/users; logs in to get cookie; POSTs to create a terminal session via backend (which calls orchestrator); opens WS to /api/v1/ws/terminal/<session_id> with cookie; sends input 'echo hello\n'; asserts 'hello' appears in data frames; closes WS; programmatically runs `docker compose restart orchestrator` (or equivalent client API); waits for orchestrator /healthz to return ready; opens new WS to same session_id; asserts attach frame's scrollback contains 'hello'; sends 'echo world\n'; asserts 'world' appears.

## Boundary Map

## Service boundaries

- **Browser ↔ FastAPI backend**: cookie-authed WebSocket at `/api/v1/ws/terminal/{session_id}`. JSON frames: `{type: "attach"|"data"|"input"|"resize"|"exit"|"detach"}`.
- **FastAPI backend ↔ Orchestrator (HTTP)**: shared-secret-authed (header `X-Orchestrator-Key`). Endpoints: `POST /v1/sessions`, `GET /v1/sessions?user_id=&team_id=`, `DELETE /v1/sessions/{id}`, `POST /v1/sessions/{id}/resize`, `POST /v1/sessions/{id}/scrollback`, plus admin-driven `POST /v1/volumes/preview-shrink` for shrink-warning preview.
- **FastAPI backend ↔ Orchestrator (WebSocket)**: shared-secret-authed (header on upgrade). `/v1/sessions/{id}/stream` — bidirectional byte bridge. Backend proxies between this WS and the browser WS.
- **Orchestrator ↔ Docker daemon**: aiodocker over `/var/run/docker.sock`. Sole Docker access in the system.
- **Orchestrator ↔ Redis**: session registry — `session:<id>` hash with `container_id`, `user_id`, `team_id`, `tmux_session_name`, `last_activity`, `created_at`. Password-authed.
- **Orchestrator ↔ Postgres (read-only path)**: orchestrator reads `workspace_volume_size_gb` from `system_settings` per provision. No writes.
- **Backend ↔ Postgres (admin settings path)**: `GET/PUT /api/v1/admin/settings` reads/writes `system_settings`.
- **Orchestrator ↔ host filesystem**: `/var/lib/perpetuity/vols/<volume_id>.img` (loopback files), `/var/lib/perpetuity/mnt/<volume_id>` (mountpoints). Orchestrator manages lifecycle.
- **Container internals**: tmux runs inside each workspace container. Orchestrator opens `aiodocker` exec to `tmux new-session`, `tmux attach`, `tmux capture-pane`. Workspace volume bind-mounted at `/workspaces/<user_id>/<team_id>/`.

## Privilege boundaries

- Backend: no Docker access, no host fs access, no privileged caps.
- Orchestrator: Docker socket mount + CAP_SYS_ADMIN (for losetup/mount) + bind mount of `/var/lib/perpetuity/`. Documented deployment constraint.
- Workspace containers: standard user, no Docker socket, no privileged caps, mem/cpu/pids limits enforced.
- Redis: internal compose network only, no host port published, password-authed.
