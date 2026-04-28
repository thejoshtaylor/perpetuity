---
phase: M005-oaptsz
phase_name: PWA, Notifications & Voice
project: perpetuity
generated: 2026-04-28T20:45:00Z
counts:
  decisions: 6
  lessons: 7
  patterns: 8
  surprises: 4
missing_artifacts: []
---

# M005-oaptsz Learnings — PWA, Notifications & Voice

### Decisions

- **Service-worker route classification: NetworkOnly /api/* + /ws/*, CacheFirst hashed assets, precache app shell.** Chosen over a single CacheFirst-everything strategy because the platform has live polling (run-status, notifications) that must never be silently cached. The route classifier in `frontend/src/sw.ts` is the contract enforced by `m005-oaptsz-sw-bypass.spec.ts`. Source: M005-oaptsz-ROADMAP.md/Success Criteria

- **Atomic VAPID keypair generation with public-non-sensitive split.** `vapid_public_key` is plain JSONB served unauthenticated (browsers need it during pushManager.subscribe); `vapid_private_key` is Fernet-encrypted and minted only through atomic `POST /api/v1/admin/settings/vapid_keys/generate` which writes both halves together and displays once. Public/private must rotate together — the atomic generate endpoint enforces this. Source: slices/S03/S03-SUMMARY.md/key_decisions

- **Endpoint-hash redaction across all push API and log surfaces.** Push subscription endpoint URLs are bearer-style secrets — anyone who has them can replay against the push service. Store/read only `sha256(endpoint)[:8]`; raw endpoint never crosses the API or log boundary. Forensics SQL is the only read path for raw endpoints. Source: slices/S03/S03-SUMMARY.md/key_decisions

- **D026 — Primitive-level auto-wrapping for universal voice coverage.** Wrap `Input` and new `Textarea` primitives with VoiceInput/VoiceTextarea by default; explicit opt-out via `voice={false}` prop or `data-voice-disabled` for password/OTP/sensitive/debounced fields. Per-call-site application would have missed inputs and risked applying voice to credential fields. Extends MEM337 design-system-primitive-floor. Source: DECISIONS.md/D026

- **notify() never re-raises (MEM356).** On any DB failure, log `notify.insert_failed` ERROR and return None — slice contract is that the calling route (invite-accept, project-create) always succeeds even when the notification side-effect fails. Pre-existing route logic must not break because of a notification side-effect. Source: slices/S02/S02-SUMMARY.md/key_decisions

- **Source-file grep redaction gate, scoped to source not minified bundles.** `scripts/redaction-sweep.sh` checks `.py`/`.ts`/`.tsx` source files for forbidden patterns co-occurring with `logger.*`/`console.*` call sites. The base64url VAPID check explicitly excludes `frontend/dist/sw.js` because the minified Workbox library produces false positives from internal identifiers. The TypeScript source is the security gate for application-authored code. Source: slices/S05/S05-SUMMARY.md/key_decisions

### Lessons

- **Snapshot the response model BEFORE calling notify() (MEM346).** `notify()` commits internally to insert the notification row, which expires every ORM-tracked object on the SQLModel session. A subsequent `team.model_dump()` or `_project_to_public(project)` returns empty/stale fields and crashes FastAPI's response validator with `ResponseValidationError`. Fix: take `.model_dump()` / build the response DTO BEFORE calling notify(). Two call sites (join_team, create_project) hit this; future call sites must follow the same pattern. Source: slices/S02/S02-SUMMARY.md/key_decisions

- **Public Pydantic DTO fields must be typed as the Python Enum class (MEM345).** `NotificationPublic.kind: NotificationKind` (not `str`) is required for openapi-ts to emit the seven literal values into `frontend/src/client/schemas.gen.ts`. Storage column can stay `VARCHAR(64) + CHECK`; ORM round-trip stays string. Without this the slice contract grep `grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts` fails. Source: slices/S02/S02-SUMMARY.md/key_decisions

- **Workbox class names are minified by terser to one-letter aliases.** Any service-worker grep verification depending on identifier shape (e.g. `NetworkOnly`) needs sentinel string constants embedded in `console.info` lifecycle logs. The constants double as documented per-fetch diagnostic content. Source: slices/S01/S01-SUMMARY.md/key_decisions (MEM340)

- **Playwright `page.route()` does not fire for SW-mediated fetches.** Use `context.route()` at the BrowserContext level + `serviceWorkers: 'allow'` + `storageState` for fresh context (MEM338). The production preview server at `:4173` is the canonical SW environment for tests because `vite-plugin-pwa devOptions.enabled=false` (MEM339). webServer is an array of two so dev (5173) and preview (4173) coexist. Source: slices/S01/S01-SUMMARY.md/key_decisions

- **Rate limit injection seam for tests.** Redis sorted-set sliding-window limiter scoped to `voice:transcribe:{user_id}` (30 req / 60s) ships with a FastAPI `Depends()` dependency-override seam so route tests exercise rate-limit boundaries without a live Redis. Reusable for any future per-user rate limit. Source: slices/S04/S04-SUMMARY.md/key_decisions

- **Transcript injection compatible with react-hook-form requires native value descriptor + bubbling input event.** Setting `input.value = transcript` directly triggers no React change events. The fix uses `Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, transcript)` followed by `input.dispatchEvent(new Event('input', {bubbles: true}))`. Source: slices/S04/S04-SUMMARY.md/key_decisions

- **MediaRecorder codec compatibility requires explicit fallback.** `audio/webm` first (Chrome/Edge), `audio/mp4` second (Safari). Cross-browser invariant for any future audio-capture feature. Source: slices/S04/S04-SUMMARY.md/key_decisions

### Patterns

- **Design-system-primitive-floor (MEM337).** Enforce ≥44×44 CSS-px touch targets via `min-h-11`/`min-w-11` on shared primitives (Button, LoadingButton, Input, PasswordInput, TabsList/Trigger, SidebarTrigger). Visible h-9/h-10 styling stays for desktop. Inline anchors get a transparent inline-flex 44×44 padding shell. Closes the four-project Playwright audit gate once for the whole app — no per-call-site work needed. Source: slices/S01/S01-SUMMARY.md/patterns_established

- **Browser-context route mocking for SW-aware Playwright tests.** `context.route()` (not `page.route()`) + `serviceWorkers: 'allow'` + production preview baseURL. Required for any test that needs to mock fetches the SW would intercept. Source: slices/S01/S01-SUMMARY.md/patterns_established (MEM338)

- **Sentinel string constants in SW lifecycle logs.** `STRATEGY_NETWORK_ONLY = 'NetworkOnly'` embedded in `console.info` survives terser minification of Workbox class names and doubles as documented diagnostic content. Use this whenever SW grep verification depends on identifier shape. Source: slices/S01/S01-SUMMARY.md/patterns_established (MEM340)

- **Snapshot-response-before-notify (MEM346).** Take `.model_dump()` / build response DTO BEFORE calling `notify()` since notify() commits internally and SQLModel session expiration would empty ORM-tracked fields on post-commit re-serialize. Applies to every future call site of notify() that returns its own response model. Source: slices/S02/S02-SUMMARY.md/patterns_established

- **Enum-typed public DTO for OpenAPI client (MEM345).** Type Pydantic public DTO fields as the Python Enum class (not str) so openapi-ts emits literal-union types into `frontend/src/client/schemas.gen.ts`. Storage column can stay VARCHAR + CHECK; ORM round-trip stays string. Source: slices/S02/S02-SUMMARY.md/patterns_established

- **page.evaluate + dynamic SDK import for Playwright seed (MEM347).** Seed test data from `page.evaluate` via dynamic import of the generated SDK rather than playwright's `request.fetch` — the SDK runs in page origin and inherits storageState cookies regardless of FE/API origin split. Reusable for any test that needs cookie-authenticated seeding across origins. Source: slices/S02/S02-SUMMARY.md/patterns_established

- **Data-attribute target for parallel-worker assertions (MEM349).** When Playwright workers share a seeded superuser, target the seeded item's `data-*` attribute (not the shared global badge) to avoid worker race conditions. Source: slices/S02/S02-SUMMARY.md/patterns_established

- **Endpoint-hash redaction pattern for push observability.** All push subscription API/log read surfaces project the endpoint as `endpoint_hash=sha256(endpoint)[:8]`; raw endpoint URLs never cross API or log boundaries. Forensics SQL is the only read path. Source: slices/S03/S03-SUMMARY.md/patterns_established

### Surprises

- **Auto-mode verifier splits `&&`-chained shell commands.** During slice closure, the auto-mode verifier produced spurious `cd: ../backend: No such file or directory` and `Script not found "build"` failures from `&&`-chained commands even when the underlying gates passed. Confirmed by running each command in its intended cwd. Document each verification step as a separate command going forward. Source: slices/S02/S02-SUMMARY.md narrative

- **TeamRole has no `owner` value in this codebase.** S02's plan called for the `project_created` notification fan-out to target `[TeamRole.admin, TeamRole.owner]`, but TeamRole is `{member, admin}` only. Plan intent (notify the team's escalation cohort) preserved by adapting to `[TeamRole.admin]`. Tighter coupling between plan-time and grep-the-enum at planning time would have caught this earlier. Source: slices/S02/S02-SUMMARY.md/key_decisions

- **POSTGRES_PORT and POSTGRES_DB env defaults differ between repo .env and docker-compose dev DB.** S04 verification was blocked until tests were re-run with `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app` — the `perpetuity_app` DB has all migrations applied while `app` at the same port is a shared CRM schema. Repo `.env` defaults to `:55432` per MEM021. Document the env override at the front of any verification-blocked summary. Source: slices/S04/S04-SUMMARY.md narrative

- **VALIDATION.md pre-existing verdict was `needs-attention`** because of a transient state where S05's SUMMARY hadn't yet been produced and the four real-device acceptance scenarios were marked unexecuted. By the time of milestone closure, S05 had completed (`verification_result: passed`) with the redaction sweep PASSing all 5 checks and S05-CHECKLIST.md as the explicit operator UAT handoff. The roadmap markdown still showed S05 as `[ ]` because it predates the renderer that toggles checkboxes from DB state — `gsd_complete_milestone` rebuilds the roadmap at close time. Going forward: trust the slice SUMMARY files + DB state, not the static roadmap markdown. Source: M005-oaptsz-VALIDATION.md / STATE.md
