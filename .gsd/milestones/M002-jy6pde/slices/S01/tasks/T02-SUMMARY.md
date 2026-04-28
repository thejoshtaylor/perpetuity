---
id: T02
parent: S01
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/auth.py
  - orchestrator/orchestrator/redis_client.py
  - orchestrator/orchestrator/errors.py
  - orchestrator/orchestrator/main.py
  - orchestrator/tests/unit/test_auth.py
  - orchestrator/tests/unit/test_health.py
  - orchestrator/tests/integration/__init__.py
  - orchestrator/tests/integration/conftest.py
  - orchestrator/tests/integration/test_redis_client.py
  - orchestrator/tests/integration/test_image_pull.py
key_decisions:
  - WS auth uses `?key=<value>` query string rather than Sec-WebSocket-Protocol subprotocol (server-to-server hop, trivial to attach via httpx_ws; documented in auth.py docstring as switchable).
  - Image-pull-on-boot uses inspect-first short-circuit (`docker pull --pull missing` semantics) because workspace images are built locally per MEM099 and never pushed to a registry — an unconditional registry pull always 404s.
  - Missing ORCHESTRATOR_API_KEY at boot is fatal: log `orchestrator_boot_failed reason=missing_api_key` and `os._exit(1)`. Loud failure beats silent 401-on-every-request.
  - Auth-key comparison iterates ALL candidates without short-circuit so timing identical regardless of which key matched (defense-in-depth on top of secrets.compare_digest).
  - Redis session blob is JSON, not a Hash — reads are always whole-record; one GET is atomic and avoids per-field deserialization. Index is `user_sessions:{user_id}:{team_id}` SET; treated as hint, not source of truth (list_sessions silently scrubs stale ids).
  - Redis client surfaces ALL connection-class errors as RedisUnavailable (no in-memory fallback per D013) and emits `redis_unreachable op=...` WARNING logs at the call site so outages correlate to the originating request.
duration: 
verification_result: passed
completed_at: 2026-04-25T09:08:20.374Z
blocker_discovered: false
---

# T02: Wire orchestrator core: shared-secret two-key auth (HTTP + WS), Redis session registry, and image-pull-on-boot with health surface

**Wire orchestrator core: shared-secret two-key auth (HTTP + WS), Redis session registry, and image-pull-on-boot with health surface**

## What Happened

Built the four foundational orchestrator subsystems every later S01 task depends on. (1) `orchestrator/orchestrator/errors.py` defines `OrchestratorError`, `RedisUnavailable`, `DockerUnavailable`, `ImagePullFailed`, `Unauthorized` — each maps to a specific HTTP status via the FastAPI exception handlers in `main.py` (Redis/Docker → 503). (2) `orchestrator/orchestrator/auth.py` implements two-key shared-secret auth per D016: a `SharedSecretMiddleware` rejects HTTP requests missing/wrong `X-Orchestrator-Key` with 401, an `authenticate_websocket` helper closes WS upgrades 1008 reason='unauthorized' before accept. Constant-time `secrets.compare_digest` comparison; iterates all candidates without short-circuit so timing is identical regardless of which key matched. WS uses `?key=` query string (not subprotocol) — auto-mode decision documented in module docstring; server-to-server hop where query strings are trivial to attach via httpx_ws. `_PUBLIC_PATHS` allowlists `/v1/health` so the compose healthcheck doesn't need the secret. Log lines emit only the first 4 chars of any presented key (e.g. `key_prefix=supe...`) — full keys never logged. (3) `orchestrator/orchestrator/redis_client.py` wraps `redis.asyncio` with `RedisSessionRegistry`: `set_session/get_session/update_last_activity/delete_session/list_sessions`. Per-session state is a JSON blob keyed `session:{sid}`, indexed by `user_sessions:{user_id}:{team_id}` SET for list ops. All Redis exceptions (ConnectionError, TimeoutError, OSError, plus generic RedisError) are caught and rethrown as `RedisUnavailable` with a `redis_unreachable op=...` WARNING log line. No in-memory fallback per D013. set_session validates the record contains user_id+team_id (saves a future debugging session). list_sessions silently scrubs stale ids whose JSON blob is missing — index is a hint, not source of truth. (4) `orchestrator/orchestrator/main.py` rewires the lifespan: validates `ORCHESTRATOR_API_KEY` is set or `os._exit(1)` with `orchestrator_boot_failed reason=missing_api_key`; calls `_pull_workspace_image` which uses `docker pull --pull missing` semantics (inspect first, fall through to registry pull only on 404) — workspace images are built locally per MEM099 and never pushed, so an unconditional registry pull would always 404. `image_pull_ok image=... source=local|registry` INFO on success; `image_pull_failed image=... reason=...` ERROR + exit(1) on failure. Binds the Redis registry singleton via `set_registry`. `/v1/health` now returns `{status, image_present}` and probes Docker live to flip image_present=False if the daemon goes away after boot. Registered exception handlers for RedisUnavailable + DockerUnavailable → 503. Added `SKIP_IMAGE_PULL_ON_BOOT=1` env hatch for the unit suite (Docker isn't required there; integration tests boot real ephemeral orchestrator containers).

Adapting to local reality: the planner specified `aiodocker.Docker.images.pull(image, stream=True)` should be awaited, but it actually returns an async generator directly — fixed (MEM102). Also adapted to MEM099 (workspace images never reach a registry) by adding the inspect-first short-circuit; without it the live orchestrator boot loop-crashed against `404 pull access denied`.

## Verification

Ran every verification check from the task plan. **Unit tests** (`SKIP_IMAGE_PULL_ON_BOOT=1 ORCHESTRATOR_API_KEY=... pytest tests/unit/`): 11 passed in 0.27s — all five plan-required auth cases (a) HTTP correct key→200, (b) HTTP wrong key→401, (c) PREVIOUS key accepted during rotation→200, (d) WS correct key accepts, (e) WS wrong key→close(1008,'unauthorized'); plus health-is-public, log-redaction-of-full-key, missing-key cases, and the existing T01 test_health (updated to assert `image_present` field). **Integration redis** (sibling container on perpetuity_default network): 8 passed in 0.15s — set/get round-trip, get-missing→None, update_last_activity advances, missing-session-update is silent, delete removes record+index, list_sessions filters by (user,team), set rejects partial records (no user_id), and `RedisUnavailable` raised against an unreachable port for every op. **Integration image-pull** (subprocess docker run on host): 3 passed in 2.99s — orchestrator with `WORKSPACE_IMAGE=perpetuity/workspace:test` logs `image_pull_ok` and stays alive (status=running); orchestrator with bogus image logs `image_pull_failed image=... reason=404:...` and exits non-zero; orchestrator with blank `ORCHESTRATOR_API_KEY` logs `orchestrator_boot_failed reason=missing_api_key` and exits non-zero. **Live stack**: rebuilt orchestrator image, recreated container — boot logs show `orchestrator_starting`, `image_pull_ok image=perpetuity/workspace:latest source=local`, `orchestrator_ready image_present=True`; compose healthcheck flipped to `(healthy)`. From inside the network, `curl http://orchestrator:8001/v1/health` returns `{"status":"ok","image_present":true}` (200); a request to a non-existent protected path returns 401 (middleware active). Total: **22 tests passed**, 0 failed. Slice observability keys present in live logs: `orchestrator_starting`, `image_pull_ok`, `orchestrator_ready` (INFO); auth ERROR lines `orchestrator_ws_unauthorized` / WARNING `orchestrator_http_unauthorized` exercised in unit tests with redaction confirmed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `SKIP_IMAGE_PULL_ON_BOOT=1 ORCHESTRATOR_API_KEY=unit-test-current-key SKIP_INTEGRATION=1 .venv/bin/pytest tests/unit/ -v` | 0 | ✅ pass — 11 passed (auth + health, all 5 plan-required cases plus log-redaction) | 270ms |
| 2 | `docker run --rm --network perpetuity_default -v $(pwd)/orchestrator:/work -w /work -e REDIS_HOST=redis -e REDIS_PASSWORD=... orchestrator:latest /app/.venv/bin/pytest tests/integration/test_redis_client.py -v` | 0 | ✅ pass — 8 passed (round-trip, list, delete, missing-op silent, RedisUnavailable on unreachable) | 150ms |
| 3 | `.venv/bin/pytest tests/integration/test_image_pull.py -v` | 0 | ✅ pass — 3 passed (image_pull_ok, image_pull_failed exit≠0, missing_api_key boot fail) | 2990ms |
| 4 | `docker compose logs orchestrator | grep -E 'image_pull_ok|orchestrator_ready|orchestrator_starting'` | 0 | ✅ pass — all three INFO lines emitted with image=perpetuity/workspace:latest source=local, image_present=True | 200ms |
| 5 | `docker run --rm --network perpetuity_default curlimages/curl:latest -fsS http://orchestrator:8001/v1/health` | 0 | ✅ pass — returned {"status":"ok","image_present":true} | 800ms |
| 6 | `docker run --rm --network perpetuity_default curlimages/curl:latest -s -o /dev/null -w '%{http_code}' http://orchestrator:8001/v1/some-protected` | 0 | ✅ pass — 401 (middleware rejects unkeyed request) | 500ms |

## Deviations

"Adapted to two local realities the planner snapshot didn't capture: (1) `aiodocker.Docker.images.pull(stream=True)` returns the async generator directly — must NOT be awaited (MEM102). The plan implied an awaitable; fixed during execution. (2) Per MEM099 workspace images are built locally and never pushed to a registry, so an unconditional registry pull always 404s. Implemented `docker pull --pull missing` semantics (inspect first, registry pull only on 404) so the boot pull succeeds when the image is already cached locally. The plan said 'pulls perpetuity/workspace:latest'; the inspect-first short-circuit honors the spirit (image must be present) without the impossible registry round-trip. Also added the `SKIP_IMAGE_PULL_ON_BOOT=1` env hatch for the unit suite — Docker isn't required there, and the integration tests cover the real pull path against ephemeral orchestrator containers."

## Known Issues

"None blocking. Tests dir is not copied into the orchestrator runtime image (Dockerfile only COPYs `orchestrator/orchestrator`) — integration tests are run from a sibling container that bind-mounts the source tree. This is fine for the dev/CI loop and aligns with keeping the runtime image small. If T03/T04 want to run pytest inside the deployed orchestrator container directly, the Dockerfile will need a multi-stage variant or a separate test image. The integration suite that needs to talk to internal compose services (redis) requires `--network perpetuity_default`; documented in conftest.py module docstring."

## Files Created/Modified

- `orchestrator/orchestrator/auth.py`
- `orchestrator/orchestrator/redis_client.py`
- `orchestrator/orchestrator/errors.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/tests/unit/test_auth.py`
- `orchestrator/tests/unit/test_health.py`
- `orchestrator/tests/integration/__init__.py`
- `orchestrator/tests/integration/conftest.py`
- `orchestrator/tests/integration/test_redis_client.py`
- `orchestrator/tests/integration/test_image_pull.py`
