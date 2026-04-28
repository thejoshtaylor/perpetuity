---
phase: M003-umluob
phase_name: Terminal Infrastructure
project: Perpetuity
generated: 2026-04-25T21:32:00Z
counts:
  decisions: 3
  lessons: 6
  patterns: 5
  surprises: 3
missing_artifacts: []
---

### Decisions

- Treated M003-umluob as a verification-only milestone over already-shipped M002-jy6pde code rather than re-implementing or stalling. Citation-by-test reports are the deliverable; zero source modifications.
  Source: M003-umluob-VALIDATION.md/Verdict Rationale

- Escalated the M003-umluob ≡ M002-jy6pde reconciliation hand-off to MILESTONE-LEVEL at S06/T01 by naming the two valid next moves explicitly: `gsd_complete_milestone` (RECOMMENDED — every S0X demo is byte-for-byte covered by tests on main) vs `gsd_reassess_roadmap` (ALTERNATIVE — replan toward R009–R012 Projects/GitHub scope). Auto-mode followed the recommended path.
  Source: S06-SUMMARY.md/Follow-ups

- Carried forward two M002-era accepted divergences without change: MEM203 (`nano_cpus=1_000_000_000` 1.0 vCPU shipped vs M003 spec's 2_000_000_000 2.0 vCPU) and MEM204 (`workspace_volume_size_gb` default 4 GiB via boot-time fallback shipped vs M003 spec's 10 GiB seed). Recorded in slice verification reports as Known Accepted Divergences, not failures.
  Source: S01-SUMMARY.md/Known limitations

### Lessons

- Verification-only milestones are a real, productive category — not a planning failure. When success criteria are byte-for-byte already met by prior shipped code, citation-by-test (file:line + verbatim PASSED lines + grep-stable hand-off) is mechanically defensible and cheaper than re-implementation.
  Source: M003-umluob-VALIDATION.md/Verdict Rationale

- Auto-mode CANNOT autonomously decide to re-scope a milestone or replan its roadmap toward different requirements. The right move is to file a grep-stable hand-off block in EVERY slice's verification artifact and escalate to milestone-level at the final slice by naming the two valid next moves. Stalling or guessing both produce worse outcomes.
  Source: S06-SUMMARY.md/What Happened

- Environmental flakes (Docker Desktop linuxkit /dev/loopN pool exhaustion at MEM210/MEM214) are real and recurrent on long e2e days; record them honestly as `## Verification gap:` sections with verbatim pytest output and probe evidence. Never modify test or source to mask. Find alternative-proof PASSED tests at the unit/integration boundary for affected criteria.
  Source: S04-SUMMARY.md/Verification gaps recorded honestly (not papered over)

- REQUIREMENTS.md mappings can drift from delivered scope without invalidating the milestone. M003-umluob mapped R009–R012 to its slices but actually delivered terminal-infra verification. Validation verdict `needs-attention` (vs `pass` or `needs-remediation`) is the right shape — surfaces the mismatch without faking unrelated requirements and without requiring a remediation slice that wouldn't actually advance R009–R012.
  Source: M003-umluob-VALIDATION.md/Requirement Coverage

- Pre-existing test-scaffolding bugs surface predictably when verification-only slices run a wider test net than the original implementing slice. MEM209 (`test_ws_bridge.py::_seed_session` FK seeding gap) was committed at bfc9cc6 BEFORE the workspace_volume FK was wired at a4de0d1; sibling test files seed correctly via `_create_pg_user_team` — `test_ws_bridge.py` was simply never updated. Found by S04/T01 and re-confirmed by S05/T01.
  Source: S04-SUMMARY.md/Verification gaps recorded honestly (not papered over)

- The bundled e2e (`test_m002_s05_full_acceptance` PASS 31.43s) is load-bearing for verification-only milestones because it exercises ALL boundaries from the ROADMAP Boundary Map in one run. If it fails environmentally (e.g. linuxkit loop pool), find unit/integration alternative proofs for each criterion rather than skipping the slice — but the bundled e2e itself ran first on the S06 turn while one loop slot was still free, so the load-bearing proof landed.
  Source: S06-SUMMARY.md/Verification

### Patterns

- Verification-only slice pattern (locked across S01/T01, S03/T02, S04/T01, S05/T01, S06/T01): T0X-VERIFICATION.md with one `## Criterion:` section per success-criterion sub-bullet (≥6 typical), file-and-line citations into source modules + bundled e2e, ≥6 verbatim PASSED lines from a live compose-stack run, top-level grep-stable `<this-milestone> duplicates <prior-milestone>` reconciliation hand-off block, optional `## Verification gap:` sections.
  Source: S04-SUMMARY.md/patterns_established

- Slice-plan grep gate as the mechanical stopping condition for verification slices: `[criterion-section-count >= N] AND [duplication-note grep] AND [PASSED-count >= M] AND [no non-.gsd git changes] AND [cited tests exit 0]` — keeps the gate enforceable without a human in the loop while still parking real decisions for a human owner.
  Source: S03-SUMMARY.md/patterns_established

- Milestone-level escalation pattern: when N consecutive verification-only slices file the same hand-off and no further slices remain, escalate to milestone-level at the final slice by naming the two valid next moves explicitly (`gsd_complete_milestone` or `gsd_reassess_roadmap`) in the verification report.
  Source: S06-SUMMARY.md/patterns_established

- MEM214 escape-clause discipline: environmental flakes (linuxkit loop-pool exhaustion) are recorded as `## Verification gap:` sections with verbatim pytest output, NEVER masked by modifying test or source; alternative-proof tests are run for affected criteria.
  Source: S06-SUMMARY.md/patterns_established

- Static + live evidence pattern: combine `grep`-based static citations against source-of-truth files with verbatim PASSED lines from live test runs. Cite the file paths and line numbers in the report so future readers can independently verify.
  Source: S01-SUMMARY.md/patterns_established

### Surprises

- The bundled M002/S05 e2e (`test_m002_s05_full_acceptance`) is byte-for-byte the literal demo for THREE separate M003 slices (S04 step (a) DURABILITY, S05 cookie-authed WS bridge end-to-end, S06 final integrated acceptance). One bundled test load-bearing for nine sub-criteria across three slices was a surprising amount of leverage from a single e2e.
  Source: S04-SUMMARY.md/What this slice delivered

- The M003-umluob roadmap was generated independently from its own requirements (R009–R012 Projects/GitHub) and ended up byte-for-byte duplicating M002-jy6pde's terminal-infra scope. The disconnect surfaced in S01/T01 and locked across all six slices — a planning-vs-requirements drift that auto-mode could surface but not autonomously repair.
  Source: S01-SUMMARY.md/Follow-ups

- Docker Desktop linuxkit's /dev/loopN pool gets exhausted on long e2e days (45–47/47 in use on bad runs) because orphan workspace .img mounts don't auto-cleanup between test runs. Same HEAD passed `test_m002_s05_full_acceptance` earlier in the day but failed `test_m002_s05_two_key_rotation` later — environmental, not a code regression. Filed as MEM210/MEM214.
  Source: S05-SUMMARY.md/What Happened
