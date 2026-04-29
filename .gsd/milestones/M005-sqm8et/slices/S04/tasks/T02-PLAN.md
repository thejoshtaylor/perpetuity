---
estimated_steps: 15
estimated_files: 2
skills_used: []
---

# T02: Orchestrator auto_push.py — mode='rule' branch fnmatch executor

Extend `run_auto_push` in `orchestrator/orchestrator/auto_push.py` to handle `mode='rule'` in addition to the existing `mode='auto'`.

Currently, line 320 checks `if mode != 'auto': return {'result': 'skipped_rule_changed'}`. This must change.

**New logic:**
1. Accept `ref: str | None = None` as an optional keyword argument to `run_auto_push`. When called for a webhook-triggered mode=rule dispatch, the backend passes the push ref from the webhook payload.
2. If `mode == 'auto'`: existing path, unchanged.
3. If `mode == 'rule'`:
   a. Require `branch_pattern` (loaded from DB via `_read_push_rule_mode_auto_push` or inline query). If no `branch_pattern` in DB, log `auto_push_skipped project_id=X reason=rule_no_branch_pattern` and return `{'result': 'skipped_rule_no_branch_pattern'}`.
   b. Extract branch name from `ref` by stripping `refs/heads/` prefix. If `ref` is None or doesn't start with `refs/heads/`, log `auto_push_skipped project_id=X reason=ref_not_branch` and return `{'result': 'skipped_ref_not_branch'}`.
   c. Apply `fnmatch.fnmatch(branch, branch_pattern)`. If no match: log `auto_push_skipped project_id=X reason=branch_pattern_no_match ref=Y pattern=Z` and return `{'result': 'skipped_branch_pattern_no_match'}`.
   d. If match: proceed with the existing auto-push flow (token mint → find mirror → git push). The execution path from 'auto_push_started' onward is identical — reuse it.
4. If `mode == 'manual_workflow'`: return `{'result': 'skipped_rule_manual_workflow'}` — these are handled at the backend layer, not here.
5. If mode is anything else: keep existing `{'result': 'skipped_rule_changed'}` return.

**Update the auto-push-callback route** in `orchestrator/orchestrator/routes_projects.py` to accept an optional JSON body with `{"ref": "refs/heads/feature/foo"}` and pass it through to `run_auto_push`. The post-receive hook doesn't send a body (existing callers), so the body must be optional. Add a small Pydantic model `AutoPushCallbackBody(ref: str | None = None)`.

**Backward compat:** `run_auto_push(docker, pool, project_id=...)` callers (post-receive hook via orchestrator route) pass no `ref` → defaults to None → existing mode=auto path is unaffected.

**New result values to add to the result dict union in the route response:** `skipped_rule_no_branch_pattern`, `skipped_ref_not_branch`, `skipped_branch_pattern_no_match`, `skipped_rule_manual_workflow`.

## Inputs

- `orchestrator/orchestrator/auto_push.py`
- `orchestrator/orchestrator/routes_projects.py`

## Expected Output

- `orchestrator/orchestrator/auto_push.py`
- `orchestrator/orchestrator/routes_projects.py`
- `orchestrator/tests/unit/test_auto_push_mode_rule.py`

## Verification

Run: cd orchestrator && python -m pytest tests/unit/test_auto_push_mode_rule.py -v (unit tests: mode=rule match executes push, mode=rule no-match returns skipped, mode=rule no branch_pattern returns skipped, mode=manual_workflow returns skipped, mode=auto unchanged). All pass.
