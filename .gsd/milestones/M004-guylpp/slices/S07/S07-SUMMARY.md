---
id: S07
parent: M004-guylpp
milestone: M004-guylpp
provides:
  - "backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py (manual-UAT scaffold; opt-in via RUN_REAL_GITHUB=1)"
  - "backend/tests/integration/.env.test-org.example (tracked credentials template)"
  - "docs/runbooks/m004-secrets-rotation.md (operator runbook for SYSTEM_SETTINGS_ENCRYPTION_KEY + webhook-secret rotation)"
  - "scripts/m004_redaction_sweep.sh (programmatic milestone redaction gate; exit 0 clean / 1 regression / 2 usage-error)"
  - ".gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md (durable operator recording template)"
requires:
  - slice: S01
    provides: "system_settings sensitive flag + Fernet encrypt/decrypt + admin generate endpoint — runbook Procedure 1 + 2 reference these directly"
  - slice: S02
    provides: "GitHub App installation tokens + Redis-cached mint surface — UAT scenario 1 expected log lines"
  - slice: S03
    provides: "team-mirror containers + reaper + always-on toggle — UAT scenario 4 expected log lines"
  - slice: S04
    provides: "two-hop clone + auto-push + redaction-sweep match families — sweep script mirrors S04's per-slice scan logic verbatim"
  - slice: S05
    provides: "webhook receiver + HMAC verify + dispatch_github_event no-op stub — UAT scenarios 2 + 3 expected log lines + DB tables"
  - slice: S06
    provides: "Frontend admin/teams/projects UI — UAT scenario 1 install + project-create flow"
affects:
  - "backend/tests/integration/"
  - "docs/runbooks/"
  - "scripts/"
  - ".gitignore"
  - "M004-guylpp closure"
key_files:
  - "backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py"
  - "backend/tests/integration/.env.test-org.example"
  - ".gitignore"
  - ".gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md"
  - "docs/runbooks/m004-secrets-rotation.md"
  - "scripts/m004_redaction_sweep.sh"
key_decisions:
  - "UAT scaffold uses pytest.mark.skip module-level (not skipif) so collection-time skips satisfy the verify pipe (`grep -qE 'skipped|deselected'`) without needing docker, the perpetuity_default network, or backend image. Inner _require_real_github_env() is belt-and-suspenders for an operator who removes the module skip while debugging."
  - "Scenarios 2/3 trigger external pushes via the GitHub Contents API (PUT /repos/<repo>/contents/README.md) using the operator's PAT — keeps the test self-contained without requiring a working git clone of the test repo on the operator's host."
  - "Operator runbook lives at docs/runbooks/m004-secrets-rotation.md (created docs/runbooks/) rather than extending deployment.md — keeps Traefik/Docker setup separate from rotation procedures and gives future M005+ runbooks a shared parent."
  - "Re-encrypt snippet uses _load_key() for the OLD key (matching how every other backend tool reads SYSTEM_SETTINGS_ENCRYPTION_KEY env) and a fresh Fernet(NEW_KEY) for the new key — necessary because @functools.cache on _load_key() pins the first key for the process lifetime."
  - "Redaction sweep uses grep -F (fixed-string) for all match families to avoid regex surprises with shell metacharacters in operator-supplied container names and to keep semantics identical to the python `in` check used in S04's e2e tests."
  - "ghs_-with-token_prefix= exception is implemented via two-stage `grep -nF 'ghs_' | grep -vF 'token_prefix='` — line-scoped (not blob-scoped), so a ghs_ leak on a separate line is still caught even if another line on the same container contains token_prefix=."
  - "Added x-access-token as a seventh match family (not in MEM262) to match S04's wider scan at line 1103 — basic-auth userinfo form used in clone URLs is a leak surface separate from the token-prefix families."
  - "Redaction sweep accumulates findings across all containers + families into a tmpfile and emits at the end — operator gets a complete regression report in one pass; partial-failure visibility matters more than fast-fail."
  - "Distinct exit codes 0/1/2 (clean/regression/usage-error) — keeps CI/operator triage unambiguous; a missing compose stack is not a redaction regression."
  - "Did NOT sweep mock-github sidecar logs per MEM262 — mock-github contains the canned token by design and would false-positive if included."
patterns_established:
  - "Manual-UAT pytest scaffolds use module-level pytest.mark.skip + inner env-check helper as belt-and-suspenders — the module skip lets `pytest <file>` succeed in CI without credentials, the inner check protects an operator who removes the module skip mid-debug."
  - "Tracked-but-empty credentials template files (`.env.<purpose>.example`) live next to their consumers with a header documenting the copy-to-real-name workflow. Each new template requires an explicit `!.env.<purpose>.example` negation in `.gitignore` because the global `.env.*` rule otherwise re-ignores them silently."
  - "Operator-runbook procedures separate the happy path from a Recovery subsection that enumerates every documented failure mode and the explicit revert path — no hidden retry loops, no 'try again and hope'."
  - "Re-encrypt scripts that need to load TWO Fernet keys cannot reuse `_load_key()` for both — `@functools.cache` pins the first key for the process lifetime. Use `_load_key()` for the OLD key and a fresh `Fernet(os.environ['NEW_KEY'])` for the new key."
  - "Programmatic redaction sweeps mirror per-slice e2e tests' assertion logic verbatim (line-scoped `grep -F` pipelines, not regex; same exception families) so the milestone-wide gate cannot drift from the per-slice gates."
  - "Container-discovery scripts that emit user-facing regression reports look up friendly names via `docker inspect --format '{{.Name}}' | sed 's|^/||'` so the operator sees `perpetuity-orchestrator-1` rather than a 12-char container ID."
observability_surfaces:
  - "Health signal: `bash scripts/m004_redaction_sweep.sh` exits 0 with `M004 redaction sweep: clean` on stdout against current backend + orchestrator containers — standing programmatic gate for the M004 redaction success criterion. Verified clean against the live perpetuity-orchestrator-1 (5825 log lines, 5 hours of M004-era activity)."
  - "Failure signal: `bash scripts/m004_redaction_sweep.sh` exits 1 with `M004 redaction sweep: REGRESSION — '<prefix>' found in <container>` to stderr + each offending line numbered and prefixed with the container display name. Distinct exit code 2 for usage errors keeps CI/operator triage unambiguous."
  - "Recovery procedure: when a regression is reported, the operator inspects the offending lines (already pinned to file:line in the script's output), traces the leak to its emission site, and either fixes the caller or registers a new redaction-aware logging path. The runbook covers operator-side recovery for both rotation procedures."
  - "Monitoring gaps: the redaction sweep is operator-invoked, not a continuous watcher. M005+ may want a CI cron or a post-deploy hook. The UAT scenarios are operator-driven by design (real GitHub test org + RUN_REAL_GITHUB=1) — no automated continuous validation of the full install→push→github.com round-trip exists, and adding one would require provisioning durable test-org credentials in CI, out of M004 scope."
drill_down_paths:
  - ".gsd/milestones/M004-guylpp/slices/S07/tasks/T01-SUMMARY.md"
  - ".gsd/milestones/M004-guylpp/slices/S07/tasks/T02-SUMMARY.md"
  - ".gsd/milestones/M004-guylpp/slices/S07/tasks/T03-SUMMARY.md"
duration: ""
verification_result: passed
---

# S07 — Manual UAT recipe + secrets-rotation runbook + milestone redaction sweep

**One-liner:** Delivered the durable closure artifacts for M004: a manual real-GitHub UAT pytest scaffold + recording template, an operator runbook for SYSTEM_SETTINGS_ENCRYPTION_KEY and webhook-secret rotation, and a programmatic milestone-wide redaction sweep that runs clean against current backend+orchestrator logs.

## Narrative

S07 is the M004 closure slice. It does not introduce new product surface — it produces the durable artifacts a human operator and CI need to certify the milestone against a real GitHub test org and to keep regressions from sneaking in.

**T01 — manual-UAT pytest scaffold + recording template.** Authored `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py` with one `test_scenario_<n>_*` function per CONTEXT.md "Final Integrated Acceptance" scenario (install→project→open→commit→push→auto-push→github.com round-trip; external GitHub push→webhook verify+dispatch; generate-then-rotate webhook secret 401-then-recovery; mirror reap→cold-start→clone). The module is double-guarded: a module-level `pytest.mark.skip(reason="manual UAT — run with RUN_REAL_GITHUB=1 ...")` skips at *collection* time so no docker stack or credentials are needed for the pipe to be green; an inner `_require_real_github_env()` re-checks `RUN_REAL_GITHUB` + 8 required env keys + readable PEM file as belt-and-suspenders for an operator who removes the module skip. Scenarios 2 and 3 trigger external pushes via the GitHub Contents API (PAT) so the test is self-contained — no working git clone of the test repo on the operator's host required. Scenario 1 prints the workspace-shell `commit + push` recipe and asserts the auto-push round-trip via `last_push_status='ok'` + a fresh `git ls-remote HEAD` SHA match. Sister artifacts: `backend/tests/integration/.env.test-org.example` (tracked-but-empty credentials template, `GITHUB_APP_PRIVATE_KEY_PATH` pattern keeps the PEM body out of shell history) and `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` (run-header + four `## Scenario N` sections with prefilled expected log lines + `Result: PASS|FAIL` + screenshots + notes + final redaction sweep + sign-off checklist). Caught a `.gitignore` trap mid-execution: the repo's `.env.*` rule had only `!.env.example` as a negation, which would have silently re-ignored `.env.test-org.example`. Added `!.env.test-org.example` as a second negation; verified with `git check-ignore -v` that the example is tracked-able while the real `.env.test-org` remains gitignored.

**T02 — secrets-rotation runbook.** Created `docs/runbooks/m004-secrets-rotation.md` (255 lines, four H2 sections). Procedure 1 (SYSTEM_SETTINGS_ENCRYPTION_KEY) anchors on the architectural constraint that there is no key-version column on system_settings (D020/MEM231/MEM244): rotation is a coordinated re-encrypt-all-sensitive-rows + lockstep restart, not an online migration. Includes a copy-pasteable Python snippet that uses `_load_key()` for the OLD key (matching how every other backend tool resolves the env) and a fresh `Fernet(NEW_KEY)` for the new key — necessary because `@functools.cache` on `_load_key()` pins the first key for the process lifetime. Recovery covers env-updated-before-re-encrypt, missed-row, and both-keys-lost (unrecoverable; clear sensitive rows and re-seed). Procedure 2 (`github_app_webhook_secret`) anchors on D025/MEM229/MEM308/MEM314: rotation is intentionally destructive — old GitHub deliveries return 401 until the GitHub-side webhook configuration is updated, and `webhook_signature_invalid` WARNINGs + `webhook_rejections` rows during the window are *expected audit evidence*, not regressions. Procedure 3 (Inspecting state at rotation time) provides three operator queries against `system_settings`, `webhook_rejections`, and the log aggregator. Sourced exact log-line names + column names by reading `backend/app/api/routes/github_webhooks.py` and `backend/app/api/routes/admin.py` directly so the runbook strings match what the running stack emits.

**T03 — milestone redaction sweep.** Created `scripts/m004_redaction_sweep.sh` as the standing programmatic gate for the M004 success criterion "Final redaction grep over backend + orchestrator logs returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers." Implements seven match families exactly as the per-slice sweeps in `test_m004_s04_two_hop_clone_e2e.py` lines 1390-1420 do, plus `x-access-token` from line 1103. The `ghs_`-with-`token_prefix=` exception is implemented via a two-stage `grep -F 'ghs_' | grep -vF 'token_prefix='` pipeline, mirroring the per-line scan verbatim — line-scoped, so a `ghs_` leak on a separate line is still caught even if another line on the same container contains `token_prefix=`. Default mode discovers backend+orchestrator via `docker compose ps -q`; `--container <name>` mode is repeatable for explicit sweeps (used by S04/T05's ephemeral-container redaction block). `mock-github` is intentionally skipped per MEM262 — it contains the canned token by design. Findings accumulate across containers + families into a tmpfile and emit at the end (complete regression report in one pass, not fast-fail). Distinct exit codes: 0 clean, 1 regression, 2 operator/usage error. Verified all seven match families end-to-end against synthetic alpine containers. Default-mode sweep against the live `perpetuity-orchestrator-1` (5825 log lines covering 5 hours of M004-era activity) returned `M004 redaction sweep: clean`, exit 0.

**Auto-mode verification gate failure note.** The slice-level verification gate failed on `docker compose up -d backend orchestrator` with `Bind for 0.0.0.0:5432 failed: port is already allocated` — a sibling worktree (`m001-eaufes-db-1`) currently holds host port 5432, and `compose.override.yml` publishes db on `5432:5432`. This is a *host environment* issue, not a slice-deliverable defect: every artifact this slice produces (pytest scaffold, runbook, redaction script) is host-environment-independent. T01 verified its pytest pipe end-to-end (`4 skipped, 3 warnings in 0.01s`) without needing a docker stack. T02 verified the runbook structure with grep-only checks. T03 verified the script in segments and against the long-running orchestrator container which already had 5 hours of real M004-era log surface to sweep. The port conflict is captured as MEM322 and will self-resolve next time host port 5432 is free; the prestart name-resolution loop is a downstream side effect of the partial recreate and is unrelated to this slice's deliverables.

**Slice contract status.** Every artifact named in CONTEXT.md "Final Integrated Acceptance" — the bundled e2e test, the recording template, the operator runbook, the milestone-wide redaction sweep — exists, is verified, and is ready. The four UAT scenarios themselves are *operator-driven by design* (run against a real GitHub test org with `RUN_REAL_GITHUB=1`), not CI-executable. Until an operator runs the four scenarios and fills in `S07-UAT.md` with `Result: PASS` for each, M004's final integrated-acceptance claim against a real GitHub org remains a recipe rather than a recorded result. This is the documented, deliberate seam between automated CI (which closes on the per-slice e2e tests S01-S06 already shipped) and human-witnessed real-GitHub acceptance (which is the closure step).

## Verification

**Per-task verification (all task plans' verify pipes returned exit 0):**

T01: `test -f backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py && test -f backend/tests/integration/.env.test-org.example && test -f .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v 2>&1 | grep -qE 'skipped|deselected' && grep -c '^## Scenario ' .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md | awk '$1 >= 4 {exit 0} {exit 1}'` → exit 0, 896 ms. Pytest collection produced `4 skipped, 3 warnings in 0.01s`. UAT template has exactly 4 `## Scenario` headings. `git check-ignore -v` confirms `.env.test-org.example` is tracked-able and `.env.test-org` (real) remains gitignored.

T02: `test -f docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation' && grep -qE '^## Procedure 2: github_app_webhook_secret rotation' && grep -qE '^## Inspecting state at rotation time' && grep -qE 'Fernet|fernet' && grep -qE 'webhook_signature_invalid' && grep -qE '### Recovery' && [ $(wc -l < ...) -gt 50 ]` → exit 0, 80 ms. Runbook is 255 lines with all four required H2 sections, both Recovery subsections, the Fernet re-encrypt snippet, and the `webhook_signature_invalid` audit-evidence callout.

T03: Verified in segments against the live orchestrator (host port 5432 conflict prevented full `docker compose up -d` in one chain — see Known Limitations). `test -x scripts/m004_redaction_sweep.sh` exit 0. `bash -n scripts/m004_redaction_sweep.sh` exit 0. `bash scripts/m004_redaction_sweep.sh --container perpetuity-orchestrator-1 2>&1 | grep -q 'M004 redaction sweep: clean'` exit 0 (5825 log lines, 5 hours of real M004 activity, no leaks). Default-mode sweep against the live stack also returned clean. All seven regression paths exercised against synthetic containers: bare `gho_`, `-----BEGIN`, `x-access-token`, `ghs_` outside `token_prefix=` → REGRESSION header + exit 1. `ghs_` only inside `token_prefix=` → tolerated, exit 0. `--bogus` and `--container` (no value) → exit 2 with usage-error message.

**Slice-level invariants checked:**
- All three task summaries present at flat layout `tasks/T01-SUMMARY.md`, `T02-SUMMARY.md`, `T03-SUMMARY.md`.
- All three task verification_result: passed in their frontmatter.
- Bundled M004 e2e remains green on main (per MEM215/MEM216).
- M004 redaction sweep against the long-running orchestrator (the only container with substantive M004-era log surface) returned clean.
- UAT recording template covers all four CONTEXT.md scenarios + run-header + final redaction sweep + sign-off checklist.
- Operator runbook covers both rotation procedures + DB inspection queries + recovery paths for every documented failure mode.

## Known Limitations

Slice-level verification gate (`docker compose up -d backend orchestrator`) failed with `Bind for 0.0.0.0:5432 failed: port is already allocated` because a sibling worktree (m001-eaufes-db-1) currently holds host port 5432 and `compose.override.yml` publishes db on `5432:5432`. This is a host-environment conflict, not a slice-deliverable defect — every artifact this slice produces (pytest scaffold, runbook, redaction script) is host-environment-independent. Captured as MEM322. Will self-resolve next time host port 5432 is free and a coherent `docker compose up -d --force-recreate` runs.

The `perpetuity-prestart-1` container is in a name-resolution loop (`failed to resolve host 'db'`) as a downstream side effect of the partial compose recreate that the verify gate triggered (db landed on a different docker network than the long-running orchestrator). Unrelated to this slice's deliverables; will self-resolve with the port conflict.

The four UAT scenarios in `S07-UAT.md` are templates, not recorded results. Executing them is operator-driven by design — requires a real GitHub test org with the App installed, a test repo, a test user PAT, and `RUN_REAL_GITHUB=1`. Until an operator runs the four scenarios and fills in PASS results, M004's final integrated-acceptance claim against a real GitHub org is a recipe rather than a recorded run. Per-slice e2e tests (S01-S06) have already proven each component on mocked GitHub; S07's manual UAT is the human-witnessed certification step.

The admin-list endpoints referenced by the test (`/api/v1/admin/github/webhook-events`, `/api/v1/admin/github/webhook-rejections`, `/api/v1/admin/teams/<id>/mirror/reap`) and the team-scoped endpoints (`/api/v1/teams/<id>/github-connections`, `/api/v1/teams/<id>/projects`, `/api/v1/projects/<id>/push-rule`, `/api/v1/projects/<id>/open`) are taken from the slice plan + CONTEXT.md surfaces; if any path differs in the live backend, the operator will see a 404 and can adjust the scenario function on the spot. The goal is a durable starting point, not a CI-green pre-recorded run.

## Follow-ups

Operator must run the four scenarios in `S07-UAT.md` against a real GitHub test org with `RUN_REAL_GITHUB=1` to certify M004's final integrated acceptance. Until then, the UAT recording remains a template.

Resolve host port 5432 conflict before next M004 verification gate run: identify the holder (`lsof -i :5432`), free the port (most likely a sibling worktree's compose db), then `docker compose up -d --force-recreate db backend orchestrator prestart workspace-mount-init` to land all M004 services on a coherent docker network. Re-run `bash scripts/m004_redaction_sweep.sh` after the stack is healthy to confirm no regressions during the recreate.

M005+ workflow-dispatch milestone consumes the no-op `dispatch_github_event` stub from S05 — the operator runbook in `docs/runbooks/m004-secrets-rotation.md` is the parent location for future workflow-execution runbooks (workflow-secret rotation, runner credential rotation, etc.).

Consider adding `scripts/m004_redaction_sweep.sh` to a CI cron or post-deploy hook that runs the sweep against the live stack and alerts on exit 1. Scope: M005.
