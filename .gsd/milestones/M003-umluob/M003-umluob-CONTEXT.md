# BLOCKER — auto-mode recovery failed

Unit `discuss-milestone` for `M003-umluob` failed to produce this artifact after idle recovery exhausted all retries.

**Reason**: Deterministic policy rejection for discuss-milestone "M003-umluob": gsd_summary_save: Error saving artifact: HARD BLOCK: Cannot save milestone CONTEXT without depth verification for M003-umluob. This is a mechanical gate — you MUST NOT proceed, retry, or rationalize past this block. Required action: call ask_user_questions with question id containing "depth_verification_M003-umluob". The user MUST select the "(Recommended)" confirmation option to unlock this gate.. Retrying cannot resolve this gate — writing blocker placeholder to advance pipeline.

This placeholder was written by auto-mode so the pipeline can advance.
Review and replace this file before relying on downstream artifacts.