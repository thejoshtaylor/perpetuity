# M002-jy6pde: Terminal Infrastructure

**Gathered:** 2026-04-25
**Status:** Ready for planning

## Project Description

Per-(user, team) Docker workspace containers with persistent loopback-ext4 volumes, accessed via cookie-authed WebSocket terminal sessions. A new top-level `orchestrator/` service (Python + FastAPI + `aiodocker`) is the only component with Docker socket access; the existing FastAPI backend proxies bytes to it over a shared-secret-authed orchestrator WS. Long-lived shells run inside `tmux` sessions so they survive both browser disconnect *and* orchestrator restart. Backend-only delivery — frontend xterm.js terminal UI is deferred to a later milestone.

This milestone also lands the **system-wide settings infrastructure** (admin-only key/value store) needed for runtime-adjustable workspace volume caps.

## Why This Milestone

**The problem it solves:** M001 shipped auth + teams but every downstream milestone — M003 (clone GitHub repo into workspace), M004 (run `claude`/`codex` CLI inside workspace), M005 (Celery workflow steps that acquire a workspace) — depends on the per-(user, team) container model existing. Without it, none of the product loop is possible.

**Why now:** It is the immediate next building block on the roadmap, and it is also the largest piece of net-new infrastructure (orchestrator service, Docker socket integration, loopback volumes, Redis, tmux session model). Locking it down before M003+ means the harder-to-revisit decisions (Docker socket location, secret model, volume storage, pty durability strategy) are settled while no other code depends on them.

## User-Visible Outcome

### When this milestone is complete, the user can:

- Sign up, log in (M001 contract), then connect a WS client to `wss://<host>/api/v1/ws/terminal/<session_id>` with their session cookie and get a live shell inside their personal team's container.
- Open multiple WS sessions for the same (user, team) — each is a separate tmux session inside the single shared container, all seeing the same `/workspaces/<user_id>/<team_id>/` filesystem.
- Disconnect their WS client, reconnect to the same `session_id` later, and see the previous scrollback (up to 100 KB) plus the running shell still alive.
- Restart the orchestrator service (`docker compose restart orchestrator`); reconnect to the same `session_id`; the shell and its scrollback are still there.
- List their currently-live (user, team) sessions via `GET /api/v1/sessions`.
- As `system_admin`, change `workspace_volume_size_gb` (or any other key) via `PUT /api/v1/admin/settings/{key}`; the next provisioned container's volume reflects the new cap.

### Entry point / environment

- **Entry point:** WebSocket `wss://<host>/api/v1/ws/terminal/<session_id>` (cookie-authed); REST `POST/GET/DELETE /api/v1/sessions`; admin REST `GET/PUT /api/v1/admin/settings[/{key}]`.
- **Environment:** local docker-compose dev environment; production-like compose stack (no Kubernetes in scope).
- **Live dependencies involved:** Docker daemon (host socket, orchestrator-only); Postgres (existing); Redis 7-alpine (new compose service, internal network, password-authed); host kernel (`losetup`, `mount`, `resize2fs`).

## Completion Class

- **Contract complete means:** orchestrator HTTP/WS endpoints return correct shapes; settings API CRUD round-trips; `workspace_volume` table writes are correct; structured error codes (`image_pull_failed`, `disk_full`, `name_conflict`, `volume_mount_failed`) are emitted.
- **Integration complete means:** end-to-end flow against real Docker daemon + real Redis + real Postgres works — provision container, start tmux session, attach via FastAPI WS, send input, receive output, detach, reattach, see scrollback, kill via DELETE. No mocks. Full M001 test discipline.
- **Operational complete means:** orchestrator restart preserves live shells; idle reaper kills containers after the configured timeout *only* when both Redis `last_activity` and the active-WS map agree the session is idle; backend WS auth failures close 1008; orchestrator WS unauth closes 1008; Docker daemon unreachable propagates as 503; admin can change volume cap and the next container honors it.

## Final Integrated Acceptance

To call this milestone complete, we must prove:

- A signed-up user with a personal team can connect a WS client to `/api/v1/ws/terminal/<new_session_id>`, run `echo hello`, see `hello` echoed back in the WS data frame.
- Same user disconnects, runs `docker compose restart orchestrator`, reconnects to the **same** `session_id`, sees the previous output in the attach-frame scrollback, and runs `echo world` in the **same** shell session (env vars, cwd preserved).
- A `system_admin` can `PUT /api/v1/admin/settings/workspace_volume_size_gb` with a new value; the next container provisioned for any (user, team) writes its volume row with the new cap and the loopback `.img` is sized accordingly.
- All of the above proven by an end-to-end test running against the real compose stack (no aiodocker mocks, no Redis mocks, no Postgres mocks).

The proof that **cannot be simulated** is the orchestrator-restart scenario — it requires a real `tmux` session in a real container, observed surviving a real process restart.

## Architectural Decisions

### Orchestrator location and stack

**Decision:** New top-level `orchestrator/` directory, separate `pyproject.toml`, separate Dockerfile, separate compose service. Stack: Python + FastAPI + `aiodocker`. Reuses backend tooling (uv, ruff, pytest).

**Rationale:** Only component with Docker socket access — preserves D005 (orchestrator owns container lifecycle; no other service touches Docker directly). Shared Python tooling keeps the dev experience uniform without coupling deployment.

**Alternatives Considered:**
- Add an `orchestrator/` package inside `backend/` — rejected; would force the backend container to mount the Docker socket, breaking D005's isolation contract.
- Separate language (Go) — rejected; no team familiarity benefit, breaks tooling reuse.

### Pty durability via tmux-in-container

**Decision:** Each `session_id` is a named `tmux` session inside the (user, team) container. Orchestrator restart kills the `docker exec` stream but tmux keeps running and keeps buffering output; reattach via a fresh exec into the existing tmux session restores the live shell.

**Rationale:** Direct `docker exec` ptys die when the orchestrator restarts (the exec stream is owned by the orchestrator process). Tmux as the pty owner decouples shell lifetime from orchestrator lifetime. Solves "survive orchestrator restart" — the requirement that `docker exec` alone cannot meet.

**Alternatives Considered:**
- Plain `docker exec` — rejected; cannot survive orchestrator restart.
- `screen` — rejected; tmux has better scrollback API (`capture-pane`) and is more commonly preinstalled.
- Custom pty supervisor process inside the container — rejected; reinventing tmux for no gain.

### Session registry: Redis (new compose service)

**Decision:** Add `redis:7-alpine` as a new compose service on the internal network, password-authed via shared secret. Orchestrator stores `session_id → {container_id, tmux_session, user_id, team_id, last_activity}` in Redis.

**Rationale:** Hot-path read/write per WS frame is too chatty for Postgres; D005's "container state in Postgres (not Redis)" applies to *container lifecycle* records, not the per-session activity heartbeat. Redis unreachable → 503 (no in-memory fallback — would lie about persistence).

**Alternatives Considered:**
- In-process dict — rejected; lost on orchestrator restart, defeats tmux durability.
- Postgres with a heartbeat row — rejected; write amplification on `last_activity` updates.
- SQLite on a shared volume — rejected; locking pain across orchestrator workers.

### Volume storage: loopback-backed ext4 with hard kernel cap

**Decision:** Per-workspace sparse `.img` files at `/var/lib/perpetuity/vols/<volume_id>.img`, mounted via `losetup` then bind-mounted into the container at `/workspaces/<user_id>/<team_id>/`. Cap is enforced by the ext4 filesystem size — kernel-level, not advisory.

**Rationale:** Hard cap is a real constraint (admin can shrink-prevent or grow). Bind-mounted directory volumes can't enforce a size limit; ZFS/btrfs quotas require host filesystem buy-in we don't want to mandate. Loopback ext4 works on any Linux host.

**Constraint surfaced:** Orchestrator must run with `CAP_SYS_ADMIN` (or root) on the host for `losetup`/`mount`. **Documented as M002 deployment constraint.**

**Alternatives Considered:**
- Plain bind mount — rejected; no size cap.
- Docker named volume with size limit — rejected; not portable across drivers.
- ZFS dataset per workspace — rejected; host-filesystem dependency.

### Volume cap configurability and partial-apply shrink

**Decision:**
1. `system_settings` table in Postgres, generic key/value (`key TEXT PK`, `value JSONB`). Admin API: `GET /api/v1/admin/settings`, `GET /api/v1/admin/settings/{key}`, `PUT /api/v1/admin/settings/{key}` body `{value: any}`. Gated by `role == system_admin`.
2. **New `workspace_volume` table:** `(id UUID PK, user_id UUID FK, team_id UUID FK, size_gb INT, img_path TEXT, created_at)`. Effective cap is `volume.size_gb` per row — not derived from the global setting.
3. **Partial-apply shrink rule:** when admin sets `workspace_volume_size_gb` to N:
   - All **new** workspace_volume rows use N.
   - **Existing** rows with `size_gb > N` keep their old `size_gb` (cap divergence allowed).
   - The PUT response warns the admin with the list of (user, team, current `size_gb`, current usage) for affected volumes.
   - Existing rows with `size_gb <= N` are eligible for grow on next container provision (`resize2fs` + `losetup -c`).
4. **Grace-period force-shrink flow is deferred** but the schema is ready: the `workspace_volume` table is the natural home for a future `grace_period_expires_at` column and a reaper. Documented as an M002 follow-up.

**Rationale (settings shape):** Generic key/value PUT-per-key chosen over a typed Pydantic doc. Tradeoff: loses OpenAPI type clarity and shifts validation runtime-side, but lets us add settings without touching the schema and avoids whole-doc-overwrite footguns.

**Rationale (per-volume storage):** Single source of truth for the effective cap. Partial-apply means the global setting is *the default for new volumes only*, not the live cap on every existing volume.

**Rationale (grace-period deferred):** Adding a force-shrink reaper is a destructive code path that needs its own test surface and admin UI. M002 ships the schema-ready data model; a future ops milestone adds `grace_period_expires_at` + reaper without requiring a migration.

**Alternatives Considered:**
- Typed Pydantic settings doc — rejected; user chose flexibility/extensibility over OpenAPI clarity.
- Always-refuse shrink — rejected; admins need a path to lower the global default for new workspaces even when some old ones are oversize.
- Force-shrink with `force: true` flag — rejected; user prefers the grace-period model (deferred to a later milestone).

### Backend ↔ orchestrator auth: shared secret with two-key rotation

**Decision:** Shared secret env vars `ORCHESTRATOR_API_KEY` (current) and `ORCHESTRATOR_API_KEY_PREVIOUS` (optional). Orchestrator accepts either on every HTTP and WS request. Backend always sends the current key.

**Rationale:** Lets ops roll the secret without simultaneous restart of both services — set previous=old, current=new on orchestrator first, then update backend. Modest implementation cost (one extra config value, one extra acceptance branch); meaningful operational value.

**Alternatives Considered:**
- Single static key — rejected; user wanted graceful rotation in scope.
- mTLS — rejected; certificate plumbing overkill for an internal-network service-to-service call in M002.

### WS bridge architecture and frame protocol

**Decision:** Browser ↔ FastAPI WS (cookie-authed) ↔ Orchestrator WS (shared-secret-authed) ↔ tmux exec stream. FastAPI proxies bytes; orchestrator owns Docker. JSON-framed protocol:
- Server frames: `{type: "attach", scrollback}`, `{type: "data", bytes}`, `{type: "exit", code}`, `{type: "detach", reason}`
- Client frames: `{type: "input", bytes}`, `{type: "resize", cols, rows}`

**Rationale:** Two-hop bridge keeps the Docker socket out of the backend (D005). JSON framing accommodates both control messages (resize, attach) and data without an out-of-band channel. Resize uses default tmux semantics: smaller of attached clients wins.

**Alternatives Considered:**
- Direct browser → orchestrator WS — rejected; would require exposing orchestrator publicly and reimplementing cookie auth there.
- Raw byte stream — rejected; no good way to multiplex resize/attach/exit signals.

### HTTP API surface (orchestrator)

**Decision:** Orchestrator exposes:
- `POST /v1/sessions` — create session (provisions container if absent, starts tmux session)
- `GET /v1/sessions?user_id=&team_id=` — list caller's live sessions
- `DELETE /v1/sessions/{id}` — kill tmux session (container reaped on idle)
- `POST /v1/sessions/{id}/resize` — resize attached tmux pane
- `POST /v1/sessions/{id}/scrollback` — fetch up-to-100-KB scrollback
- `WS /v1/sessions/{id}/stream` — orchestrator-side bridge endpoint

Backend exposes the public contracts: `POST/GET/DELETE /api/v1/sessions`, `WS /api/v1/ws/terminal/{session_id}`, `GET/PUT /api/v1/admin/settings[/{key}]`.

**Rationale:** Backend is the public API surface; orchestrator is internal. Backend translates cookie auth → shared-secret auth on the orchestrator hop.

### Container discovery and quotas

**Decision:** Container name `perpetuity-ws-<first8-team>`. Docker labels `user_id`, `team_id`. Quota: 1 container per (user, team), unlimited tmux sessions inside.

**Rationale:** Labels make orchestrator discovery deterministic; name is human-recognizable for debugging. Single container per pair preserves D004 isolation while allowing the multi-pane UX of tmux without container-per-tab cost.

### Idle reaper: two-phase check

**Decision:** Orchestrator background task runs every N seconds. For each session: if Redis `last_activity` exceeds the configured idle timeout AND the in-memory active-WS map has no live attach, kill the tmux session and reap the container (volume persists). Default idle timeout: **15 minutes**.

**Rationale:** Two-phase check prevents the race where a freshly-attached client hasn't yet updated `last_activity`. 15 min is the user-chosen default — aggressive enough to control idle resource use, softened by tmux+scrollback replay so reattach feels seamless.

### Resource limits per container

**Decision:** `mem_limit=2g`, `nano_cpus` (TBD in planning), `pids_limit=512`. Default `workspace_volume_size_gb` = **2 GB** (admin-adjustable).

**Rationale:** Hard caps prevent one runaway workspace from starving the host. 2 GB volume is tight on purpose — pushes admins to use the grow flow early, validating that path is real.

## Error Handling Strategy

**Principle:** Fail fast and visibly. No retries on infrastructure errors (Docker daemon, Redis) — the orchestrator surfaces 503 and the backend propagates it. Structured error codes for all provision failures.

- **Image lifecycle:** orchestrator pulls `perpetuity/workspace:latest` once on startup. Pull failure → orchestrator boot fails loudly. **No lazy pulls** at session-create time.
- **Docker daemon unreachable:** 503 from orchestrator → 503 from backend.
- **Redis unreachable:** 503 from orchestrator on all endpoints. No in-memory fallback (would lie about persistence).
- **Container provision failures:** structured error codes — `image_pull_failed`, `disk_full`, `name_conflict`, `volume_mount_failed`.
- **tmux session creation fails:** clean up half-created Redis record, return 500.
- **Orchestrator WS dies mid-stream:** FastAPI sends `{type: "detach", reason: "orchestrator_disconnected"}` then closes 1011. tmux keeps running. Client reconnects → scrollback intact.
- **Backend WS dies mid-stream:** orchestrator detects close, leaves tmux running. Redis `last_activity` updated only on actual I/O.
- **Cookie auth fails on backend WS:** 1008 close with reason — matches M001 pattern.
- **Shared secret mismatch on orchestrator WS:** 1008 close `unauthorized`. Logged with redacted secret prefix only.
- **Session ownership violation:** 1008 close `session_not_owned`. **No existence enumeration** — same close code/reason whether session exists for someone else or doesn't exist at all.
- **Idle reaper race with active attach:** two-phase check (Redis `last_activity` + active-WS map) before killing.
- **Container exists but tmux session gone:** 410 Gone; client creates new session with a fresh `session_id`.
- **Resize for unattached session:** 404, no-op.
- **Volume shrink for affected oversize volumes:** partial-apply (see Architectural Decision above) — affected volumes keep old cap; PUT response includes the warning list.

## Risks and Unknowns

- **Loopback volume privilege requirement** — orchestrator needs `CAP_SYS_ADMIN`/root on the host for `losetup`/`mount`. Documented; affects deployment story (not just dev).
- **CI cost of "no mocks"** — full integration suite against real Docker daemon adds ~10–30s and requires Docker socket mount in the CI runner. Accepted; matches M001's "real Postgres" discipline.
- **Two-key rotation not exercised in tests by default** — need an explicit test that hits the orchestrator with `_PREVIOUS` and confirms it accepts. Easy to forget.
- **Generic key/value settings means runtime-side validation** — typo in admin PUT body for `workspace_volume_size_gb` (e.g. string instead of int) must be caught with a clear error, not silently stored. Per-key validators required.
- **Cap divergence audit surface** — once partial-apply ships, ops will eventually want a `GET /admin/volumes?oversize=true` view. Not in M002 scope but worth flagging.
- **`tmux capture-pane` payload size discipline** — must hard-cap at 100 KB on the orchestrator side, not trust tmux to limit. Bug here = WS frame too big to send.

## Existing Codebase / Prior Art

- `backend/app/api/routes/ws.py` — M001's WS auth pattern (cookie → user → close-before-accept on failure with 1008). M002 backend WS reuses this.
- `backend/app/api/routes/admin.py` — M001's router-level `dependencies=[Depends(get_current_active_superuser)]` gate. M002 admin settings router reuses this pattern exactly.
- `backend/app/api/deps.py` — `get_current_user` cookie-auth dependency for REST + WS. Reused for backend WS.
- `backend/app/core/cookies.py` — M001 cookie helpers.
- `backend/app/models.py` — User/Team/TeamMember models. M002 adds `workspace_volume` and `system_settings` here.
- `backend/app/alembic/versions/s01_…/s02_…/s03_…` — M001's nullable→backfill→NOT-NULL migration discipline. M002 migrations follow the same shape.
- `backend/tests/conftest.py` — real-Postgres test fixtures (no mocks). M002 extends with real-Docker + real-Redis fixtures.
- `docker-compose.yml` — current services: db, adminer, prestart, backend, frontend. M002 adds `orchestrator` and `redis`.
- `.gsd/DECISIONS.md` D004, D005, D006 — already lock per-(user, team) container model, dedicated orchestrator with sole Docker socket access, multi-instance over shared volume. M002 is the implementation.

## Relevant Requirements

- **R005** — Per-user-per-team Docker container with mounted `/workspaces/<user_id>/<team_id>/` — **directly delivered.**
- **R006** — On-demand spin-up + idle reap + volume persistence + remount — **directly delivered.** (Orchestrator tracks `last_activity` in Redis; Postgres holds container/volume lifecycle.)
- **R007** — `/ws/terminal/{session_id}` cookie-authed WS relay — **directly delivered** (with the tmux durability addition that R007 didn't anticipate).
- **R008** — Multiple terminal panes for same workspace, single shared volume — **directly delivered** as multiple tmux sessions inside one container (refines D006).

R009–R022 are **not** delivered by M002 (deferred to their own milestones), but M002 unblocks them all.

## Scope

### In Scope

- New top-level `orchestrator/` service (FastAPI + `aiodocker` + Python).
- `redis:7-alpine` compose service (internal network, password auth).
- Backend ↔ orchestrator HTTP + WS contract with two-key shared-secret auth.
- Backend public API: `POST/GET/DELETE /api/v1/sessions`, `WS /api/v1/ws/terminal/{session_id}`.
- Backend admin API: `GET /api/v1/admin/settings`, `GET/PUT /api/v1/admin/settings/{key}` (key/value, `system_admin`-gated).
- Postgres tables: `system_settings` (key/value/JSONB), `workspace_volume` (id, user_id, team_id, size_gb, img_path, created_at).
- Loopback-ext4 volumes with hard kernel cap; partial-apply shrink rule.
- Per-(user, team) container with `mem_limit=2g`, `pids_limit=512`, configured nano_cpus.
- tmux session model with named-session-per-`session_id`, scrollback replay (100 KB cap on orchestrator side).
- Idle reaper (15-min default, two-phase check) — system-wide configurable via the new settings API.
- Structured error codes and 1008/1011/410/503 close-code discipline.
- Observability: INFO/WARNING/ERROR taxonomy below; UUIDs only in logs (no emails or names).
- Full M001 test discipline: integration tests against real Docker daemon, real Redis, real Postgres. CI mounts host Docker socket.

### Out of Scope / Non-Goals

- **Frontend terminal UI** (xterm.js, multi-tab UX, mobile terminal layout) — deferred to a later frontend-touching milestone.
- **`/admin/settings` web page** — settings API ships in M002; UI page lands later.
- **Team-customizable container images** — one shared `perpetuity/workspace:latest` only.
- **Per-team idle-timeout configuration** — system-wide default only in M002.
- **Claude/Codex CLI plumbing** (R013/R014) — M004.
- **GitHub repo cloning into workspace** (R010) — M003.
- **Celery worker integration with orchestrator** (R017) — M005.
- **Audit-grade full-transcript logging** — only the 100 KB ring-buffer scrollback in M002.
- **Grace-period force-shrink reaper for oversize volumes** — schema-ready (per-volume `size_gb` column lives on `workspace_volume`); flow deferred. Future milestone adds `grace_period_expires_at` + reaper without migration.
- **Hot rotation of shared secret** beyond the two-key acceptance pattern (no SIGHUP, no admin endpoint to rotate at runtime — env-var based only).

## Technical Constraints

- **Orchestrator privilege:** must run with `CAP_SYS_ADMIN` (or root) on the host for `losetup`/`mount`. Documented as deployment constraint.
- **Docker socket access:** **only** the orchestrator service mounts `/var/run/docker.sock`. Backend never touches it. Enforced by compose.
- **Redis password:** internal-network only; never exposed externally; password = shared secret pattern.
- **No mocks of aiodocker, no mocks of Redis, no mocks of Postgres** in tests — full M001 discipline.
- **Image lifecycle:** orchestrator pulls `perpetuity/workspace:latest` once on startup; pull failure is a boot blocker.
- **Single shared base image** in M002: Ubuntu + git + node + python + bash + tmux.
- **Backend cwd discipline:** continues from M001 (MEM041) — backend tests run from `backend/` cwd. Orchestrator service tests run from `orchestrator/` cwd.

## Integration Points

- **Docker daemon (host)** — orchestrator-only via `/var/run/docker.sock`. Sole container lifecycle authority.
- **Postgres (existing service)** — gains `system_settings` and `workspace_volume` tables. Backend reads/writes; orchestrator reads `workspace_volume` on provision and reads `system_settings` (`workspace_volume_size_gb`, `idle_timeout_seconds`) on every relevant operation.
- **Redis (new service)** — orchestrator-only. Stores live session registry and `last_activity` heartbeat.
- **Host kernel** — `losetup`, `mount`, `resize2fs` for loopback volume management. Orchestrator-only.
- **Backend FastAPI** — owns public API surface (cookie auth) and proxies WS bytes to orchestrator.
- **Frontend** — does **not** integrate with terminal endpoints in M002 (deferred). Frontend will, however, eventually consume `GET /api/v1/sessions` and `WS /api/v1/ws/terminal/{session_id}` — APIs designed with that future caller in mind.

## Testing Requirements

**Discipline:** full M001-style integration testing. **No mocks** of aiodocker, Redis, or Postgres.

**Test fixtures (new):**
- Real Docker daemon — assumed available via mounted host socket in CI and dev.
- Real `redis:7-alpine` from compose.
- Test-specific image tag: tests use `perpetuity/workspace:test` (built from same Dockerfile, smaller layer set acceptable) to keep image-pull cost manageable.

**Required test categories:**
- **Unit:** loopback `.img` allocation, `losetup`/`mount` invocation shape (mocking only the subprocess boundary at this level), `system_settings` per-key validators.
- **Integration (orchestrator):** create session → attach exec → send input → receive output → detach → reattach → scrollback present → kill. Against real Docker, real Redis, real `workspace/test` image.
- **Integration (backend ↔ orchestrator):** WS bridge round-trip with cookie auth on the public side and shared-secret auth on the orchestrator side. Two-key rotation: orchestrator with both keys set must accept either. `orchestrator_ws_unauthorized` close with bad key.
- **Integration (cross-service):** admin `PUT /api/v1/admin/settings/workspace_volume_size_gb` then `POST /api/v1/sessions` for a never-before-provisioned (user, team); inspect the resulting `workspace_volume.size_gb` row and the actual `.img` file size.
- **Migration:** alembic up/down round-trip for `system_settings` and `workspace_volume` tables (M001 MEM016 lock-hazard pattern still applies).
- **End-to-end (final integrated acceptance):** `echo hello` → restart orchestrator → reconnect to same `session_id` → see scrollback + `echo world` in same shell. Against the real compose stack.
- **Negative paths:** Docker daemon unreachable → 503; Redis unreachable → 503; cookie auth fail → 1008; shared-secret fail → 1008; session ownership violation → 1008 with no existence enumeration; container-exists-but-tmux-gone → 410; resize for unattached session → 404.
- **Observability assertion tests:** confirm UUIDs-only in log output (no email/name leakage). Pattern lift from M001 admin tests.

## Acceptance Criteria

(Per-slice criteria will be detailed during slice planning. Milestone-level criteria below; slice-level criteria must be satisfiable from these.)

- All endpoints in the In-Scope list return correct shapes and status codes; all close-codes documented above are produced under their stated conditions.
- The final integrated acceptance test (above) passes against the real compose stack.
- `docker compose restart orchestrator` does not kill any live tmux session; reattach to the same `session_id` shows scrollback in the attach frame.
- Idle reaper kills containers after exactly the configured idle timeout, AND only when the active-WS map confirms no live attach.
- `PUT /api/v1/admin/settings/workspace_volume_size_gb` to a smaller value than at least one existing volume's `size_gb` returns 200 with a warning payload listing affected (user, team) pairs and current usage; affected volumes keep their old cap; new provisions use the new cap.
- All sensitive log lines emit user/team/session/container IDs as UUIDs only; never email or name.
- Full backend + orchestrator test suite passes against real Postgres + real Redis + real Docker. Suite runtime budget: ≤ 60s wall clock for the full integration set.
- Migration round-trip clean for both new tables.

## Open Questions

- **Concrete `nano_cpus` value per container** — left to planning; default likely 1.0 vCPU equivalent (1_000_000_000 nanos), confirm during S01 plan.
- **Image pull strategy in CI** — pre-warm via `docker pull` before test job vs let orchestrator pull on boot. Matters for CI suite runtime; decide in S01.
- **Whether to ship a minimal admin-CLI for setting key/value settings before the UI exists** (operator UX gap during M002). Not blocking; can be post-merge.

---

## Appendix: Observability Taxonomy

- **INFO:** `session_created`, `session_attached`, `session_detached`, `session_killed_idle`, `container_provisioned`, `container_reaped`, `image_pull_ok`, `volume_resized`, `volume_provisioned`, `setting_changed` (key + actor user_id, value redacted if sensitive).
- **WARNING:** `redis_unreachable`, `docker_unreachable`, `tmux_session_orphaned`, `quota_exceeded`, `volume_shrink_partial_apply` (with affected count), `setting_validation_failed`.
- **ERROR:** `image_pull_failed` (boot blocker), `volume_mount_failed`, `orchestrator_ws_unauthorized`, `losetup_failed`, `resize2fs_failed`.
- **All session/container/user/team IDs logged as UUIDs only** — never emails or names.

> See `.gsd/DECISIONS.md` for the full append-only register of all project decisions. M002-derived decisions (settings API shape, partial-apply shrink rule, two-key rotation pattern, `workspace_volume` table) will be appended during planning and implementation phases.
