---
verdict: needs-attention
remediation_round: 0
---

# Milestone Validation: M004-guylpp

## Success Criteria Checklist
## Reviewer A — Requirements Coverage

| Requirement | Status | Evidence |
|---|---|---|
| R009 — Projects at team level, linked to GitHub | COVERED | S04: projects CRUD + team-scoped routes (`backend/app/api/routes/projects.py`); S06: ProjectsList UI |
| R010 — Repo cloned into workspace independently per user | COVERED | S04: `clone_to_user_workspace` (credential-free `git://`) + POST /open chain; S06: OpenProjectButton |
| R011 — Webhooks for push/PR/tag events | COVERED | S05: POST /api/v1/github/webhooks with HMAC verify, persistence, no-op dispatch hook |
| R012 — Per-team GitHub App connections | COVERED | S02: install-url + callback + list/delete + persistence (`s06b_github_app_installations`); S06: ConnectionsList UI |
| R034 — Sensitive system_settings encrypted at rest, never round-trip plaintext | COVERED | S01: Fernet encryption substrate, redacted GET/list, e2e proves PEM PUT redacted |
| R035 — Generate-only keys, one-time display | COVERED | S01: POST /admin/settings/{key}/generate, one-shot plaintext, destructive re-generate (D025); S06: OneTimeValueModal |
| R036 — Single GitHub App, four system_settings keys | COVERED | S01: github_app_id/client_id/private_key/webhook_secret registered in `_VALIDATORS`; S02 consumes via `_load_github_app_credentials` |
| R037 — Per-team installation_id with Redis-cached 50-min tokens | COVERED | S02: `mint_installation_token` + `get_installation_token` cache `gh:installtok:{id}` TTL 3000s |
| R038 — One team-mirror container per team, lifecycle + always-on | COVERED | S03: `team_mirror.py` ensure/reap, `team_mirror_reaper.py`, PATCH always_on |
| R039 — Team-mirror exposes git daemon on 9418, credential-free | COVERED | S03: `git daemon --base-path=/repos --enable=receive-pack --port=9418`; e2e Scenario C |
| R050 — Two-hop clone, env-on-exec credentials | COVERED | S04: `clone_to_mirror` env-on-exec + `.git/config` sanitize + leak detection; e2e zero token leaks |
| R051 — Push-back rules (auto/rule/manual+workflows) schema + auto executor | COVERED | S04: project_push_rules table + CHECK on mode, `auto_push.py` executor |
| R052 — Webhook receiver: HMAC verify, persist, dispatch hook, audit rejections | COVERED | S05: github_webhook_events + webhook_rejections, raw-body HMAC, ON CONFLICT idempotency |
| R053 — Fernet decrypt fail-loud (503 + ERROR log, no silent fallback) | COVERED | S01 global SystemSettingDecryptError handler; S02 + S05 e2e prove HTTP 503 + log shape |
| R054 — Structured git-op logs, no token leakage | COVERED | S07 `scripts/m004_redaction_sweep.sh` (5825 lines, 7 token/PEM families, exit 0) |

**Reviewer A verdict: PASS** — every M004-relevant requirement (R009–R012, R034–R039, R050–R054) has clear slice-level evidence.

## Milestone Success Criteria

- [x] Team admin installs GitHub App via real install URL — S02 install-url + callback + persistence; mocked e2e green; real-org closure pending S07 UAT scenario 1
- [x] PEM PUT once → GET returns has_value:true, value:null — S01 e2e
- [x] Generate webhook secret one-time-display + destructive re-generate — S01 + S06 + D025
- [x] Project create + push-back rule (auto/rule/manual_workflow); only auto wired — S04 schema + executor; S06 form
- [x] Disable auto-reap (always-on) per team — S03 PATCH + reaper skip log
- [x] Open project materializes repo with no creds in user `.git/config` — S04 e2e + defense-in-depth `remote.origin.url` guard
- [x] Push lands in mirror; auto-mode pushes to GitHub — S04 e2e against fixture upstream
- [x] External webhook 200 + persisted + dispatched — S05 e2e 8-step contract proof
- [x] Bad-HMAC → 401 + audit row, no body persistence — S05 e2e
- [x] Fernet decrypt fail → 503 + ERROR log naming key — S01/S02/S05 all assert
- [x] Structured git-op logs + redaction sweep zero matches — S04 observability + S07 sweep clean
- [ ] **Final integrated acceptance against real GitHub test org** — S07-UAT.md scaffold + recording template ON DISK with all 4 scenario headings, but operator has NOT yet executed the real-GitHub run. Redaction-grep half is verified clean; the four scenarios are operator-pending.

## Slice Delivery Audit
## Slice Delivery Audit

| Slice | SUMMARY on disk | Verification result | Notes |
|---|---|---|---|
| S01 | yes (.gsd/milestones/M004-guylpp/slices/S01/S01-SUMMARY.md) | passed | 19 unit + 1 e2e (7.91s) — Fernet substrate + sensitive registry |
| S02 | yes | passed | 27 install-route + 21 token + 6 migration unit; 1 e2e (23.29s) with mock-github + Redis TTL probe |
| S03 | yes | passed | 16 backend + 29 orchestrator unit; 5-scenario e2e (16.92s) with sibling alpine/git clone over compose DNS |
| S04 | yes | passed | 145 unit; 8-scenario e2e (25.88s) — two-hop clone + auto-push + zero token leaks |
| S05 | yes | passed | 9 unit + 3 schema; 8-step contract e2e (9.94s) |
| S06 | yes | passed | typecheck/lint/build + Playwright `--list` green |
| S07 | yes | passed (with environmental flag) | Scaffold + UAT recording template + runbook + redaction sweep all on disk; verify gate hit MEM322 port-5432 sibling-worktree conflict (environmental, not deliverable defect) — recorded in S07-SUMMARY |

All seven slice SUMMARY files are on disk. Every slice has a `verification_result: passed` field in its summary frontmatter. No missing artifacts.

**Reviewer C verdict: NEEDS-ATTENTION** — Contract / Integration / Operational verification classes all PASS. UAT class is PARTIAL: deliverables (test scaffold, UAT.md template, redaction sweep script) are on disk and the redaction-grep half is verified clean, but the four real-GitHub scenarios in S07-UAT.md are operator-pending — recording template entries have not been filled in with `Result: PASS`/timestamps/screenshots.

## Cross-Slice Integration
## Reviewer B — Cross-Slice Integration

| Boundary | Producer Confirms | Consumer Confirms | Status |
|---|---|---|---|
| S01 → S02 | yes — `decrypt_setting`, `SystemSettingDecryptError`, four registered keys, orchestrator 1:1 mirror | yes — S02 `requires:` declares decrypt_setting + Fernet substrate + four keys | ✓ |
| S02 → S03 | partial — S02 `affects:` names "S03 will consume `get_installation_token()`" | partial — S03 narrative "backend-side: nothing direct yet (S04 calls into both)" matches roadmap design | ⚠ (semantically clean by design — token consumption deferred to S04) |
| S03 → S04 | yes — `ensure_team_mirror`, `is_row_reapable`, `team_mirror_volumes`, compose-DNS `team-mirror-<first8>:9418`, named-volume durability | yes — S04 `requires:` lists `ensure_team_mirror(team_id)`, network addr helper, and `git daemon --enable=receive-pack` | ✓ |
| S04 → S05 | yes — projects + project_push_rules + clone modules + auto_push; "next slice should know" addresses S05 FK to installation_id | yes — S05 `requires:` lists S01 decrypt + S02 `github_app_installations(installation_id)` ON DELETE SET NULL FK | ✓ |
| S05 → S06 | yes — webhook receiver routes + tables + dispatch stub; OpenAPI regenerated | yes — regenerated `sdk.gen.ts` covers all M004 endpoints; webhook-secret FE flow consumes S01 surface | ⚠ (S05 not in S06 explicit `requires:` — webhook receiver has no UI surface; design-clean) |
| S06 → S07 | yes — Playwright e2e + admin/teams/projects UI components | yes — S07 `requires:` lists S06: "Frontend admin/teams/projects UI — UAT scenario 1 install + project-create flow" | ✓ |

## End-to-End Trace

The four-hop credential discipline pipeline traces cleanly: (1) admin pastes PEM via S01 sensitive `_VALIDATORS`, encrypted with Fernet in `value_encrypted`; (2) S02's `mint_installation_token` calls `decrypt_setting('github_app_private_key')` at the JWT-sign site, mints RS256 App JWT, exchanges for installation token, caches under `gh:installtok:{id}` with 50-min TTL; (3) when user clicks "Open project" backend calls orchestrator `/v1/teams/{id}/mirror/ensure` which spins up team-mirror on a named docker volume with compose-DNS `team-mirror-<first8>:9418` and `git daemon --enable=receive-pack` (S03); (4) orchestrator `clone_to_mirror` calls `get_installation_token`, passes it via env-on-exec into `git clone https://x-access-token:$TOKEN@github.com/...`, sanitizes `.git/config`, then user containers (NetworkMode=`perpetuity_default` per S04 closing MEM264) clone credential-free over `git://` from S03's mirror; (5) on push, post-receive hook calls `auto_push` which uses S02's cached token to `git push --all --prune` to GitHub. S07's redaction sweep verifies the entire pipeline (backend + orchestrator logs, 5825 lines, 5h activity, exit 0) has zero token-prefix or PEM matches — closing the loop on S01's encryption invariant.

**Reviewer B verdict: PASS** — every boundary edge honored; two ⚠ entries are design-clean (token consumption deferred to S04 by the roadmap; webhook receiver is FE-independent by design).

## Requirement Coverage
All 15 M004-relevant requirements (R009–R012, R034–R039, R050–R054) are COVERED with cited slice-level evidence. See the "Reviewer A — Requirements Coverage" table in `successCriteriaChecklist` for the per-requirement matrix.

No requirements are PARTIAL or MISSING. The single milestone-level gap (final integrated acceptance against a real GitHub test org) is a UAT-execution gap, not a requirements-coverage gap — every requirement has component-level evidence (per-slice unit + integration tests, structured logs, redaction sweep). What remains is operator-driven validation against the real github.com surface, captured in the S07-UAT.md recording template that ships with the milestone.

## Verification Class Compliance
## Verification Classes

| Class | Planned Check | Evidence | Verdict |
|---|---|---|---|
| **Contract** | Unit tests: encrypt/decrypt, validators, JWT sign, HMAC verify, generators, push-rule CRUD, schema migration round-trip | S01: 19 unit + 45 passed total. S02: 27 install-route + 21 github_tokens + 6 migration (RS256 JWT shape, cache TTL, lookup). S03: 16 backend + 29 orchestrator including is_row_reapable boundary. S04: 145 unit across clone/auto_push/projects + migration. S05: 9 webhook unit + 3 schema (UNIQUE, ON DELETE SET NULL, alembic round-trip). S06: typecheck/lint/build + Playwright `--list`. | **PASS** |
| **Integration** | Postgres + Redis + Docker; respx for GitHub; bare git fixture; install token cache TTL; team-mirror; two-hop clone with no creds; auto-push; webhook receiver; Fernet decrypt fail | S01 e2e (1 passed 7.91s). S02 e2e 6 scenarios with mock-github sidecar + Redis TTL probe (1 passed 23.29s). S03 e2e A–E with sibling alpine/git clone over compose DNS (1 passed 16.92s). S04 e2e 8 scenarios two-hop clone + auto-push (PASSED 25.88s) — `.git/config` clean + leak-guard. S05 e2e 8-step contract (PASSED 9.94s). All e2es include redaction sweeps. | **PASS** |
| **Operational** | Mirror survives orchestrator restart; idle timeout admin-tunable; always-on suppresses reap; webhook secret rotation invalidates old signatures; missing SYSTEM_SETTINGS_ENCRYPTION_KEY → fail-fast; runbook documents rotation | S03: named-volume durability invariant (reap NEVER removes volume); `mirror_idle_timeout_seconds` validator [60, 86400] tunable; always-on toggle proven (Scenario D). S05: webhook_signature_invalid + webhook_rejections audit on rotation. S01: compose declares env var with `?Variable not set` (compose-up fail-fast); lazy `_load_key()` RuntimeError. S07-T02: `docs/runbooks/m004-secrets-rotation.md` (255 lines) — Procedure 1 (encryption key rotation w/ Fernet snippet + Recovery), Procedure 2 (webhook-secret rotation, audit-evidence callout), Procedure 3 (state inspection). | **PASS** |
| **UAT** | S07 four real-GitHub scenarios recorded in S07-UAT.md; redaction grep returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers across backend + orchestrator | Scaffold `test_m004_guylpp_s07_full_acceptance_e2e.py` on disk (module-skip + RUN_REAL_GITHUB env gate). `S07-UAT.md` has all 4 `## Scenario` headings + sign-off checklist, but **all fields are placeholder template entries — no operator has filled in `Result: PASS`/timestamps/screenshots**. Redaction sweep `scripts/m004_redaction_sweep.sh` verified clean against live `perpetuity-orchestrator-1` (5825 lines, exit 0). Note: S07 verify gate hit MEM322 port-5432 sibling-worktree conflict (environmental, not deliverable). | **PARTIAL** |

**Summary:** Contract / Integration / Operational classes all PASS with full evidence. UAT class is PARTIAL: deliverables (test scaffold, UAT.md template, redaction sweep) are on disk and the redaction-grep half is verified clean. The four real-GitHub scenarios are operator-pending — recording template not yet filled in with execution results.


## Verdict Rationale
Reviewer A (Requirements): PASS — all 15 M004-relevant requirements covered. Reviewer B (Cross-Slice Integration): PASS — every boundary honored, end-to-end credential pipeline traces cleanly through S01→S02→S03→S04→S05, redaction sweep verifies zero leaks. Reviewer C (Acceptance + Verification Classes): NEEDS-ATTENTION — Contract/Integration/Operational classes PASS; UAT class is PARTIAL because the four real-GitHub scenarios in S07-UAT.md are operator-pending (template on disk with all 4 scenario headings, but no recorded `Result: PASS` / timestamps / screenshots from a real-org run). Per protocol any NEEDS-ATTENTION → overall verdict `needs-attention`. This is NOT `needs-remediation` because every deliverable is built and operational — what remains is operator-driven execution against the real github.com surface, which is by-design out of automated CI scope (RUN_REAL_GITHUB env gate). The milestone is functionally complete; closure waits on operator UAT recording.
