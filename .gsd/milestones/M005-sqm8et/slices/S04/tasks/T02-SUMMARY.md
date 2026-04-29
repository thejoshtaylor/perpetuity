---
id: T02
parent: S04
milestone: M005-sqm8et
key_files:
  - orchestrator/orchestrator/auto_push.py
  - orchestrator/orchestrator/routes_projects.py
  - orchestrator/tests/unit/test_auto_push_mode_rule.py
  - orchestrator/tests/unit/test_auto_push.py
key_decisions:
  - Added _read_push_rule() returning both mode+branch_pattern; _read_push_rule_mode() kept as alias to avoid breaking existing callers
  - KeyError caught when reading branch_pattern from fake DB rows — avoids breaking the existing test harness without requiring every _seed_rule call to include branch_pattern
  - mode='manual_workflow' is now a first-class dispatch result (skipped_rule_manual_workflow) rather than falling through to skipped_rule_changed
  - AutoPushCallbackBody defaults to AutoPushCallbackBody() so legacy no-body callers (post-receive hook) are unaffected
duration: 
verification_result: passed
completed_at: 2026-04-29T09:04:10.036Z
blocker_discovered: false
---

# T02: Orchestrator run_auto_push extended with mode='rule' fnmatch branch executor; routes_projects updated with optional AutoPushCallbackBody; 5 new unit tests all pass

**Orchestrator run_auto_push extended with mode='rule' fnmatch branch executor; routes_projects updated with optional AutoPushCallbackBody; 5 new unit tests all pass**

## What Happened

Extended `run_auto_push` in `orchestrator/orchestrator/auto_push.py` with mode='rule' branch fnmatch dispatch and updated the auto-push-callback route to accept an optional body.

Key changes:
1. Added `import fnmatch` to auto_push.py.
2. Introduced `_read_push_rule(pool, project_id)` → `dict[mode, branch_pattern] | None` replacing the inline mode-only query. The old `_read_push_rule_mode` is kept as a thin alias (delegates to `_read_push_rule`) so no existing callers break. KeyError is caught gracefully when the fake DB row doesn't include `branch_pattern` — preserving backward compat with the existing test harness.
3. `run_auto_push` gains `ref: str | None = None` keyword arg. Mode dispatch was restructured:
   - `mode='manual_workflow'` → returns `skipped_rule_manual_workflow` (previously fell through to `skipped_rule_changed`).
   - `mode='rule'` → reads branch_pattern; if absent → `skipped_rule_no_branch_pattern`; if ref is None or not `refs/heads/` prefix → `skipped_ref_not_branch`; if fnmatch miss → `skipped_branch_pattern_no_match`; if match → falls through to the shared mint→mirror→push execution path with `rule_mode_label='rule'`.
   - `mode='auto'` → unchanged path with `rule_mode_label='auto'`.
   - anything else → `skipped_rule_changed`.
4. The `auto_push_started` log now uses `rule_mode=%s` (was hardcoded `rule_mode=auto`).
5. `routes_projects.py`: added `AutoPushCallbackBody(ref: str | None = None)` Pydantic model; `post_auto_push_callback` accepts it with a default of `AutoPushCallbackBody()` so legacy (no-body) callers are unaffected; `ref=body.ref` forwarded to `run_auto_push`.
6. Updated existing `test_rule_changed_skipped_no_exec` in `test_auto_push.py` — it used `mode='manual_workflow'` to trigger the old catch-all path, but `manual_workflow` is now a first-class result. Changed test to use `mode='unknown_legacy_mode'` to correctly exercise the `skipped_rule_changed` fallback.
7. Created `orchestrator/tests/unit/test_auto_push_mode_rule.py` with 5 hermetic tests: rule+match→ok, rule+no-match→skipped, rule+no-branch-pattern→skipped, manual_workflow→skipped, auto+no-ref→ok (backward compat).

## Verification

Ran `uv run pytest tests/unit/test_auto_push_mode_rule.py tests/unit/test_auto_push.py -v` — 19 passed (5 new + 14 existing), 0 failed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/orchestrator && uv run pytest tests/unit/test_auto_push_mode_rule.py tests/unit/test_auto_push.py -v` | 0 | ✅ 19 passed | 270ms |

## Deviations

Updated existing test_rule_changed_skipped_no_exec in test_auto_push.py — that test used mode='manual_workflow' as a stand-in for 'stale rule' which no longer falls through to skipped_rule_changed; changed to mode='unknown_legacy_mode' to correctly exercise the fallback path.

## Known Issues

None.

## Files Created/Modified

- `orchestrator/orchestrator/auto_push.py`
- `orchestrator/orchestrator/routes_projects.py`
- `orchestrator/tests/unit/test_auto_push_mode_rule.py`
- `orchestrator/tests/unit/test_auto_push.py`
