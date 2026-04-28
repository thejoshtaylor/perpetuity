# M004-guylpp / S07 — Manual UAT Recording

This file is the durable artifact for the four "Final Integrated
Acceptance" scenarios from
`.gsd/milestones/M004-guylpp/M004-guylpp-CONTEXT.md`. The operator runs
the scenarios against a real GitHub test org and fills in each section
inline as they go. Until every scenario is recorded with `Result: PASS`,
the M004 milestone completion claim is unsupported.

The executable contract for each scenario lives in
`backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py`.
That file is `pytest.mark.skip`-decorated by default; the operator opts
in via `RUN_REAL_GITHUB=1` after copying
`backend/tests/integration/.env.test-org.example` to
`backend/tests/integration/.env.test-org` and filling in the test-org
credentials.

## Run Header

- **Operator:** _<name>_
- **Date (UTC):** _<YYYY-MM-DD>_
- **Test org:** _<github-org-slug>_
- **Test repo:** _<owner/repo>_
- **Backend image SHA:** _<docker inspect -f '{{.Id}}' backend:latest>_
- **Orchestrator image SHA:** _<docker inspect -f '{{.Id}}' orchestrator:latest>_
- **Compose project:** _<docker compose ls | grep perpetuity>_

## Scenario 1 — End-to-end happy path

> install GitHub App → see connection in team settings → create project
> linked to real repo → click open → repo materializes at
> `/workspaces/<u>/<t>/<project_name>` with no credentials in
> `.git/config` → user commits + pushes → mirror receives → auto-push
> pushes to GitHub → github.com shows the commit

- **Started at:** _<UTC timestamp>_
- **Finished at:** _<UTC timestamp>_
- **Observed log lines:**
  - `team_mirror_started trigger=ensure team_id=<id>` — _<file:line or "observed">_
  - `team_mirror_clone_started repo=<owner/repo>` — _<file:line or "observed">_
  - `team_mirror_clone_completed repo=<owner/repo>` — _<file:line or "observed">_
  - `installation_token_minted installation_id=<id> token_prefix=ghs_<4>...` — _<file:line or "observed">_
  - `auto_push_started project_id=<id>` — _<file:line or "observed">_
  - `auto_push_completed project_id=<id> last_push_status=ok commit_sha=<sha>` — _<file:line or "observed">_
- **Verified `git ls-remote HEAD` against github.com:** _<remote-sha> matches local <local-sha>_
- **`.git/config` clean of token:** _<paste of `cat .git/config` from inside the user container — must show no `gho_/ghs_/ghu_/ghr_/github_pat_/x-access-token`>_
- **Result:** PASS | FAIL
- **Screenshots:** _<list of file paths or attachment links>_
- **Notes:** _<any deviations, retries, or surprises>_

## Scenario 2 — Webhook round-trip

> external push to GitHub repo → GitHub delivers webhook → HMAC verifies
> → row in github_webhook_events → no-op dispatch_github_event invoked
> (assert via `webhook_dispatched` log line)

- **Started at:** _<UTC timestamp>_
- **Finished at:** _<UTC timestamp>_
- **Trigger used:** _<browser commit, gh api PUT contents, or live `git push`>_
- **GitHub `X-GitHub-Delivery` for the triggering push:** _<delivery_id>_
- **Observed log lines:**
  - `webhook_received delivery_id=<delivery_id>` — _<file:line or "observed">_
  - `webhook_verified delivery_id=<delivery_id> signature_valid=true` — _<file:line or "observed">_
  - `webhook_dispatched delivery_id=<delivery_id> event_type=push dispatch_status=noop` — _<file:line or "observed">_
- **Row in `github_webhook_events`:** _<paste of `SELECT delivery_id, event_type, received_at FROM github_webhook_events WHERE delivery_id='<id>'`>_
- **Result:** PASS | FAIL
- **Screenshots:** _<list>_
- **Notes:** _<deviations or surprises>_

## Scenario 3 — Generate-then-rotate webhook secret

> admin generates secret → pastes into GitHub → webhook from GitHub
> verifies clean → admin re-generates → next external GitHub delivery
> returns 401 + audit row in webhook_rejections until GitHub side updated

- **Started at:** _<UTC timestamp>_
- **Finished at:** _<UTC timestamp>_
- **First-generate delivery (must verify clean):** _<delivery_id>_
- **Re-generate delivery (must reject):** _<delivery_id>_
- **Observed log lines:**
  - `webhook_received delivery_id=<rejected_delivery_id>` — _<file:line or "observed">_
  - `webhook_signature_invalid delivery_id=<rejected_delivery_id> signature_present=true` — _<file:line or "observed">_
- **Row in `webhook_rejections`:** _<paste of `SELECT delivery_id, signature_present, signature_valid, received_at FROM webhook_rejections WHERE delivery_id='<rejected_id>'` — must show signature_valid=false>_
- **HTTP status returned to GitHub:** _<401 expected>_
- **GitHub-side recovery applied at:** _<UTC timestamp the operator pasted the new secret into the App settings>_
- **Post-recovery delivery (must verify clean):** _<delivery_id>_
- **Result:** PASS | FAIL
- **Screenshots:** _<list, including GitHub Settings → Webhooks → Recent deliveries panel>_
- **Notes:** _<deviations or surprises>_

## Scenario 4 — Mirror lifecycle cold-start

> mirror reaped (idle or admin force-reap) → user clicks open → mirror
> cold-starts → clone proceeds → mirror reachable via compose-network DNS

- **Started at:** _<UTC timestamp>_
- **Finished at:** _<UTC timestamp>_
- **Reap trigger:** admin force-reap | idle reaper
- **Team id:** _<team_id under test>_
- **Project id:** _<project_id used to trigger cold-start>_
- **Observed log lines:**
  - `team_mirror_reaped team_id=<team_id> reason=admin` — _<file:line or "observed">_
  - `team_mirror_started team_id=<team_id> trigger=ensure` — _<file:line or "observed">_
  - `team_mirror_clone_started repo=<owner/repo>` — _<file:line or "observed">_
  - `team_mirror_clone_completed repo=<owner/repo>` — _<file:line or "observed">_
- **`docker network inspect perpetuity_default` confirms mirror container present:** _<paste of the relevant container row>_
- **`POST /api/v1/projects/<id>/open` wall-clock:** _<seconds>_ (must be < 30s)
- **Result:** PASS | FAIL
- **Screenshots:** _<list>_
- **Notes:** _<deviations or surprises>_

## Final Redaction Sweep

After scenarios 1-4 record PASS, run the milestone-wide redaction sweep
to confirm no scenario introduced a token-prefix or PEM-armor regression
into the live container logs:

```bash
bash scripts/m004_redaction_sweep.sh
```

Expected: `M004 redaction sweep: clean` on stdout, exit 0.

- **Sweep run at:** _<UTC timestamp>_
- **Sweep result:** PASS | FAIL
- **Sweep stdout:** _<paste>_

## Sign-off

- [ ] Scenarios 1, 2, 3, 4 all recorded with `Result: PASS`.
- [ ] Final redaction sweep passes against the post-UAT logs.
- [ ] All screenshots and SQL pastes attached above are committed (or
      linked to a stable artifact store) so the recording is durable.
- [ ] `gsd_complete_slice` may proceed for S07.
