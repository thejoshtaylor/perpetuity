# S01 Assessment

**Milestone:** M005-oaptsz
**Slice:** S01
**Completed Slice:** S01
**Verdict:** roadmap-adjusted
**Created:** 2026-04-28T09:54:34.990Z

## Assessment

S01 closed clean — PWA install + SW route classifier + four-project mobile audit all enforced (MEM336–MEM342). Reassessing M005-oaptsz/S02 surfaced one structural gap: the milestone CONTEXT assumed M005-sqm8et shipped a no-op `notify(user_id, kind, payload)` integration point at workflow run start / step complete / run finish. It did not — there is no workflow run engine, no workflow detail page, no workflow_id surface in the codebase today (grep returns zero matches for `notify(` / `workflow_run` / `WorkflowRun` across `backend/app` and `orchestrator/orchestrator`). The S02 demo as written ("On the workflow detail page, the user toggles per-event-type routing") is not buildable end-to-end. Adjusting S02 to ship the in-app data-and-UI substrate that M005-oaptsz actually owns — notifications + notification_preferences tables, the `notify()` helper itself (the thing M005-sqm8et was supposed to stub), the bell + panel UI with cross-device read-state polling, the preferences UI scoped to team-default-per-event-type (workflow_id NULL = team-default; the per-workflow override path is the schema's natural extension when workflows land), and wiring the call sites that DO exist today (team_invite_accepted, project_created) plus a `system` test event so the bell has real content to display. The S02 demo is rewritten to match what's truly demonstrable. S03/S04/S05 demos and dependencies remain valid. No slices added or removed.
