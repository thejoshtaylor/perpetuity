---
phase: completion
phase_name: M006-ydo2ce Completion
project: perpetuity
generated: 2026-05-13T18:30:00.000Z
counts:
  decisions: 5
  lessons: 4
  patterns: 3
  surprises: 2
missing_artifacts: []
---

# M006-ydo2ce Learnings & Insights

## Decisions

- **Fernet encryption for user OAuth tokens at rest using existing SYSTEM_SETTINGS_ENCRYPTION_KEY.** Chose to reuse the existing encryption key and helpers from M004/S01 rather than introducing a new key material or encryption scheme. This decision minimizes key rotation complexity, reduces cryptographic surface area, and leverages already-audited encryption infrastructure. Source: M006-ydo2ce-ROADMAP.md/Success Criteria

- **Personal-install detection branches on `account_type` field at the backend route level.** The backend route decision tree examines `GitHubAppInstallation.account_type` to determine token forwarding, not installation scope or OAuth response metadata. This centralizes account-type understanding at the route boundary and prevents mixing concerns across service layers. Source: S04-SUMMARY.md/What Happened

- **X-GitHub-User-Token header forwarding only for personal installs; org installs remain unchanged.** The orchestrator reads the optional header and branches: personal installs require the header and use it for `/user/repos`; org installs ignore the header entirely and use the installation token for `/orgs/{login}/repos`. This preserves byte-identical org-install behavior and avoids cross-account token leakage. Source: S05-SUMMARY.md/What Happened

- **409 Conflict response with nested detail shape for user-token unavailability.** Backend returns `{"detail": {"code": "github_user_token_required", "installation_id": <int>, "reason": "..."}}` on missing/expired tokens. This nested shape allows the frontend to distinguish this specific 409 from other 409 scenarios and render the reinstall CTA. The S08 remediation confirmed the nested shape matches orchestrator expectations. Source: S04-SUMMARY.md/Exception Mapping; S08-SUMMARY.md/What Happened

- **Token refresh state machine with three explicit expiry classes: fresh (no refresh call), expired-access (refresh), expired-refresh (delete + error).** The refresh helper maintains this state machine to avoid unnecessary GitHub calls on fresh tokens, refresh on rotatable access tokens, and surface clean failures on unrecoverable expiry. This pattern reduces external API calls and improves observability of token lifecycle. Source: S03-SUMMARY.md/State Machine

## Lessons

- **SQLAlchemy session-scoped autouse fixtures hold implicit AccessShareLock on referenced tables, blocking alembic DDL operations.** Any migration test that tries to DROP COLUMN, ALTER COLUMN, or DROP TABLE will hang indefinitely if the session-scoped `db` fixture is still active. The solution is to copy the fixture pattern from M004/S09 (`_release_autouse_db_session` + `_restore_head_after`) which commits, expires, closes the session, and calls `engine.dispose()` before alembic runs. This pattern solved the migration DDL hang that plagued earlier iterations. Source: M004 context snapshot / M006-ydo2ce-ROADMAP.md/Boundary Map S01→S02

- **Nested Playwright mock shapes must match backend response structure exactly, including intermediate `detail` wrappers.** The initial S06 Playwright tests consumed the flat shape `{"detail": "...", "code": "..."}`, but the actual backend returns nested `{"detail": {"code": "...", ...}}`. Frontend parsing failed silently until S08 corrected all three 409 mocks to match the nested structure. This asymmetry between mock and implementation was caught by integration test failures and required careful trace to isolate. Source: S08-SUMMARY.md/What Happened

- **Token plaintext must never appear in logs or error responses; only prefixes or metadata.** All 5 slices (S02–S07) include explicit caplog assertions or log inspection to verify that token plaintext, ciphertext, and refresh tokens are never captured in any log level. Redaction is implemented at the exception class level, not the logging filter level, to ensure the contract is enforced across all log sites. Source: S04-SUMMARY.md/Verification; S03-SUMMARY.md/Logging Contract

- **Defense-in-depth assertions catch cross-account token forwarding bugs at compile time.** The backend route includes an assertion that org installs never forward a user token, catching any logic inversion bugs before they leak to the network. This pattern proved its value when the route logic was initially under-specified and needed tightening. Source: S04-SUMMARY.md/Defense-in-Depth

## Patterns

- **State machine pattern for resource lifecycle with multiple terminal states.** The token refresh helper implements a clear state machine (fresh/expired-access/expired-refresh/missing/corrupt/transient) with explicit transitions and terminal error states. Each state has a distinct observability signal and HTTP response code. This pattern scales to other resource lifecycles (install tokens, deployment tokens) and provides a reusable model for state-driven decision trees. Source: S03-SUMMARY.md/State Machine; S04-SUMMARY.md/Exception Mapping

- **Encrypted column with round-trip helpers and DTOs that redact ciphertext from serialization.** The pattern of `set_<field>() / get_<field>()` helper methods on the model, paired with a DTO (`GitHubUserOAuthTokenStatus`) that explicitly excludes `*_encrypted` fields, ensures that (1) encryption is transparent to business logic, (2) plaintext is never serialized to JSON, and (3) DTO consumers cannot accidentally leak ciphertext. This pattern applies to any encrypted storage requirement. Source: S01-SUMMARY.md/T02 SQLModel & DTO

- **Cross-slice integration testing with respx mocks validates boundary contracts.** Each slice (S02–S07) includes integration tests that exercise the full code path with respx-mocked GitHub and backend HTTP assertions. These tests catch shape mismatches, missing headers, and exception mapping errors at the boundary. The pattern proved its value when S08's nested-detail fix required updating all three Playwright mock shapes in lockstep. Source: S07-SUMMARY.md/Integration Testing

## Surprises

- **Org-install regression test required negative-assertion logging inspection.** S07/T04 initially relied on absence of `token_class=user_token` in logs to prove org installs were unchanged, not on explicit affirmative assertions about the install-token path. This was a discovery during S07 that the code-path verification required checking for what *doesn't* happen, not just what does. Negative tests can be harder to write and maintain. Source: M006-ydo2ce-VALIDATION.md/Scenario 3

- **Real-GitHub UAT was blocked by network access from the test environment.** S07 planned to execute the three acceptance scenarios against real GitHub accounts and capture end-to-end evidence, but the integration test environment has no outbound network access to GitHub. The team pivoted to code-path verification via respx mocks and documented an operator runbook for manual real-GitHub completion post-deployment. This is a deployment-time blocker, not a code-quality issue, but it delayed final UAT closure. Source: M006-ydo2ce-VALIDATION.md/Verdict Rationale
