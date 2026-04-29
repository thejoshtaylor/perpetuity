"""AI-step executor: claude / codex CLI invocation through orchestrator.

`run_ai_step(session, step_run_id)` is the per-step entrypoint the runner
in `app.workflows.tasks` calls when `WorkflowStep.action in {claude, codex}`.
It owns the full lifecycle of one `step_runs` row from `running` to
`succeeded` | `failed` and is responsible for the `error_class` taxonomy
the slice plan locks in:

  * `missing_team_secret`         — `get_team_secret` raised Missing.
  * `team_secret_decrypt_failed`  — `get_team_secret` raised Decrypt.
  * `orchestrator_exec_failed`    — httpx connect/read/timeout/non-2xx.
  * `cli_nonzero`                 — orchestrator returned exit_code != 0.

Secret discipline (MEM274 / MEM164 / S01 boundary):
  * The plaintext API key only ever lives in this function's frame, in the
    `env` dict passed to the orchestrator (which forwards it to the
    container exec frame). It is NEVER logged, NEVER written to
    `step_runs.snapshot`, and NEVER attached to an exception message.
  * The rendered prompt body is also never logged. `step_runs.stdout` is
    persisted (R018: forever-debuggable history) but the rest of the
    system never reads it back into a log line.

Session id (re-run fairness, MEM092 / D012 alignment):
  * The orchestrator's one-shot exec endpoint uses `session_id` purely as
    a correlation handle for logs — there is no tmux session involved.
    We compute a deterministic UUID5 from `(target_user_id, team_id,
    workflow_run_id)` so a Celery double-deliver or a manual re-run hits
    the same workspace container without spinning up a new one.

Failure modes (matches the task plan's failure-modes table):
  * `httpx.HTTPError` family (connect, read, timeout, write, network)
    → status='failed', error_class='orchestrator_exec_failed', stderr =
    `type(exc).__name__`. The `str(exc)` for httpx errors can leak request
    URLs but never headers (which would carry the X-Orchestrator-Key);
    we still keep stderr to the class name to be safe.
  * Non-2xx response from orchestrator (4xx/5xx, including 504 timeout)
    → same as transport error: error_class='orchestrator_exec_failed',
    stderr = `orchestrator_status_<n>`. The 5xx case is what shows up
    when docker is unreachable (existing handler in routes_exec.py).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from sqlmodel import Session

from app.api.team_secrets import (
    MissingTeamSecretError,
    TeamSecretDecryptError,
    get_team_secret,
)
from app.core.config import settings
from app.models import StepRun, Team, WorkflowRun, get_datetime_utc
from app.workflows.executors._retry import (
    OrchestratorExecFailed,
    _orchestrator_exec_with_retry,
)

logger = logging.getLogger("app.workflows.executors.ai")

# Stable namespace for deterministic per-run session ids. Generated once
# (uuid.uuid4().hex) and pinned here so re-runs always map to the same
# workspace container.
_SESSION_ID_NAMESPACE = uuid.UUID("a3f9c1f8-1b25-4f2a-9e3a-7d4e7c0b9f01")

# Map workflow action → (cli argv prefix, env key for the API key, secret
# registry key). Adding a new AI provider in S03+ is a single dict entry.
_ACTION_SPEC: dict[str, dict[str, str]] = {
    "claude": {
        "cli": "claude",
        "env_key": "ANTHROPIC_API_KEY",
        "secret_key": "claude_api_key",
    },
    "codex": {
        "cli": "codex",
        "env_key": "OPENAI_API_KEY",
        "secret_key": "openai_api_key",
    },
}

# Per-step orchestrator wall-clock cap. The orchestrator itself caps at
# 600s; we send 300s so a single step can never starve the worker
# indefinitely. S05 will make this configurable per workflow.
_DEFAULT_TIMEOUT_SECONDS = 300

# httpx client timeouts. Connect short so a dead orchestrator surfaces
# fast; total long enough to cover the in-container CLI.
_HTTP_TIMEOUT = httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS + 30, connect=5.0)


class UnsupportedActionError(Exception):
    """Raised when the runner dispatches an action this executor doesn't own.

    Should never fire in practice — `app.workflows.tasks.run_workflow`
    routes by action — but defends against a future action ('claude_v2'
    say) being added to the model enum without an executor wired up.
    """


def derive_session_id(
    target_user_id: uuid.UUID, team_id: uuid.UUID, run_id: uuid.UUID
) -> uuid.UUID:
    """Deterministic UUID5 for the orchestrator session correlation handle.

    The orchestrator only uses this for log correlation — it provisions
    the workspace container by `(user_id, team_id)` regardless of
    `session_id`. Making it deterministic keeps re-run logs grep-able.
    """
    name = f"{target_user_id}:{team_id}:{run_id}"
    return uuid.uuid5(_SESSION_ID_NAMESPACE, name)


def _render_prompt(template: str, payload: dict[str, Any]) -> str:
    """Substitute `{prompt}` (and only `{prompt}`) in the template.

    Uses single-key `str.format` rather than the full format DSL so a
    user-supplied prompt with `{` characters can't trip a KeyError or
    leak format-spec features. Missing `prompt` in payload → empty string
    so the executor still calls the CLI (and the CLI surfaces its own
    "no prompt" failure rather than a backend-side crash).
    """
    prompt_text = payload.get("prompt", "") if isinstance(payload, dict) else ""
    if not isinstance(prompt_text, str):
        prompt_text = str(prompt_text)
    if "{prompt}" not in template:
        return template
    return template.replace("{prompt}", prompt_text)


def _mark_failed(
    session: Session,
    step_run: StepRun,
    *,
    error_class: str,
    stderr: str,
    started_monotonic: float,
    exit_code: int | None = None,
) -> None:
    """Stamp a step_run with a terminal `failed` status. Commits.

    Always logs `step_run_failed` with the run/step/exit/error_class/duration
    tuple — the slice's observability contract. The `stderr` argument lands
    on the row (so the run-page UI can render it) but is not logged.
    """
    finished_at = get_datetime_utc()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    step_run.status = "failed"
    step_run.error_class = error_class
    step_run.stderr = stderr
    step_run.exit_code = exit_code
    step_run.finished_at = finished_at
    step_run.duration_ms = duration_ms
    session.add(step_run)
    session.commit()
    logger.info(
        "step_run_failed run_id=%s step_index=%s exit=%s error_class=%s duration_ms=%d",
        step_run.workflow_run_id,
        step_run.step_index,
        "none" if exit_code is None else exit_code,
        error_class,
        duration_ms,
    )


def _mark_succeeded(
    session: Session,
    step_run: StepRun,
    *,
    stdout: str,
    exit_code: int,
    started_monotonic: float,
) -> None:
    """Stamp a step_run with a terminal `succeeded` status. Commits."""
    finished_at = get_datetime_utc()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    step_run.status = "succeeded"
    step_run.stdout = stdout
    step_run.exit_code = exit_code
    step_run.finished_at = finished_at
    step_run.duration_ms = duration_ms
    step_run.error_class = None
    step_run.stderr = ""
    session.add(step_run)
    session.commit()
    logger.info(
        "step_run_succeeded run_id=%s step_index=%s exit=%d duration_ms=%d",
        step_run.workflow_run_id,
        step_run.step_index,
        exit_code,
        duration_ms,
    )


def run_ai_step(session: Session, step_run_id: uuid.UUID) -> None:
    """Execute a single `ai` step (claude or codex).

    Lifecycle:
      1. Load `step_run` + parent `workflow_run` + parent `team`.
      2. Transition to `running` and stamp `started_at` (the runner did
         this for us if it transitioned the row, but we re-stamp
         `last_heartbeat_at`-shape signals here defensively).
      3. Read the action spec, fetch the team's API key via
         `get_team_secret`. On Missing/Decrypt → mark step failed with the
         right `error_class` and return.
      4. Render the prompt from `snapshot.config.prompt_template`.
      5. POST the orchestrator one-shot exec endpoint with the rendered
         prompt in `env["PROMPT"]` (so secrets / prompt body never appear
         in the cmd argv list — MEM274).
      6. Persist stdout, exit_code, duration_ms; mark succeeded if
         `exit_code == 0`, else failed with error_class='cli_nonzero'.
    """
    step_run = session.get(StepRun, step_run_id)
    if step_run is None:
        # Safety net: the runner only calls us with rows it just created,
        # but log + return rather than raising so the runner doesn't trip
        # the `worker_crash` discriminator on a stale row.
        logger.warning("ai_step_missing step_run_id=%s", step_run_id)
        return

    workflow_run = session.get(WorkflowRun, step_run.workflow_run_id)
    if workflow_run is None:
        # Cascade-deletion of the parent run mid-step. Same shape as
        # missing step_run.
        logger.warning(
            "ai_step_orphan_workflow_run step_run_id=%s", step_run_id
        )
        return

    team = session.get(Team, workflow_run.team_id)
    if team is None:
        # Team gone (CASCADE on team delete fires before the worker picked
        # up the task). Mark failed so the run page shows something.
        _mark_failed(
            session,
            step_run,
            error_class="missing_team",
            stderr="team no longer exists",
            started_monotonic=time.monotonic(),
        )
        return

    snapshot = step_run.snapshot or {}
    action = snapshot.get("action") or ""
    spec = _ACTION_SPEC.get(action)
    if spec is None:
        # Plan invariant violation — only claude / codex come through
        # `run_ai_step`. The runner already routes shell/git elsewhere,
        # so reaching here means a stale snapshot or a future action. Fail
        # the step rather than crash the worker.
        _mark_failed(
            session,
            step_run,
            error_class="unsupported_action",
            stderr=f"action {action!r} not supported by ai executor",
            started_monotonic=time.monotonic(),
        )
        return

    config = snapshot.get("config") or {}
    prompt_template = config.get("prompt_template") or "{prompt}"
    rendered_prompt = _render_prompt(
        prompt_template, workflow_run.trigger_payload or {}
    )

    # Step is now "running" — log the start. The runner already flipped
    # the status; this log line is the slice's observability contract.
    logger.info(
        "step_run_started run_id=%s step_index=%s action=%s",
        step_run.workflow_run_id,
        step_run.step_index,
        action,
    )

    started_monotonic = time.monotonic()

    # --- 3. Read the team secret -----------------------------------------
    secret_key = spec["secret_key"]
    try:
        api_key_plaintext = get_team_secret(session, team.id, secret_key)
    except MissingTeamSecretError:
        _mark_failed(
            session,
            step_run,
            error_class="missing_team_secret",
            stderr=f"Team secret {secret_key} not set",
            started_monotonic=started_monotonic,
        )
        return
    except TeamSecretDecryptError:
        _mark_failed(
            session,
            step_run,
            error_class="team_secret_decrypt_failed",
            stderr=f"Team secret {secret_key} decrypt failed",
            started_monotonic=started_monotonic,
        )
        return

    # --- 4 & 5. Build cmd + env, POST orchestrator -----------------------
    # cmd uses bare `$PROMPT` so the orchestrator's _build_script_cmd can
    # leave it unquoted and `sh -c` expands it from the env dict at exec
    # time. The API key never appears in the cmd argv — only in env.
    cli = spec["cli"]
    env_key = spec["env_key"]
    cmd = [cli, "-p", "$PROMPT", "--dangerously-skip-permissions"]
    env = {
        env_key: api_key_plaintext,
        "PROMPT": rendered_prompt,
    }

    target_user_id = workflow_run.target_user_id or workflow_run.triggered_by_user_id
    if target_user_id is None:
        # Both the trigger user and target user can be NULL after a user
        # delete (SET NULL FK). M005's auto-seeded direct workflows always
        # carry `target_user_id`, so this is defensive. Fail with a clear
        # error_class.
        _mark_failed(
            session,
            step_run,
            error_class="missing_target_user",
            stderr="workflow_run has no target_user_id",
            started_monotonic=started_monotonic,
        )
        return

    session_id = derive_session_id(
        target_user_id, workflow_run.team_id, workflow_run.id
    )

    body = {
        "user_id": str(target_user_id),
        "team_id": str(workflow_run.team_id),
        "cmd": cmd,
        "env": env,
        "timeout_seconds": _DEFAULT_TIMEOUT_SECONDS,
        "action": action,
    }
    headers = {"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY}
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    url = f"{base}/v1/sessions/{session_id}/exec"

    def _client_factory() -> Any:
        return httpx.Client(timeout=_HTTP_TIMEOUT)

    try:
        response = _orchestrator_exec_with_retry(
            _client_factory,
            url,
            body,
            headers,
            run_id=step_run.workflow_run_id,
            step_index=step_run.step_index,
        )
    except OrchestratorExecFailed as exc:
        _mark_failed(
            session,
            step_run,
            error_class=exc.error_class,
            stderr=exc.stderr_hint,
            started_monotonic=started_monotonic,
        )
        return

    try:
        data = response.json()
    except ValueError:
        _mark_failed(
            session,
            step_run,
            error_class="orchestrator_exec_failed",
            stderr="orchestrator_returned_non_json",
            started_monotonic=started_monotonic,
        )
        return

    stdout = data.get("stdout") or ""
    exit_code = int(data.get("exit_code") or 0)

    if exit_code != 0:
        # CLI ran but exited non-zero. stdout has the merged output (script
        # -q discipline). Persist exactly that — the run page UI shows
        # stdout regardless of pass/fail.
        finished_at = get_datetime_utc()
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        step_run.status = "failed"
        step_run.error_class = "cli_nonzero"
        step_run.stdout = stdout
        # No separate stderr (script -q merges it into stdout); leave
        # stderr empty so the dashboard doesn't double-render.
        step_run.stderr = ""
        step_run.exit_code = exit_code
        step_run.finished_at = finished_at
        step_run.duration_ms = duration_ms
        session.add(step_run)
        session.commit()
        logger.info(
            "step_run_failed run_id=%s step_index=%s exit=%d error_class=cli_nonzero duration_ms=%d",
            step_run.workflow_run_id,
            step_run.step_index,
            exit_code,
            duration_ms,
        )
        return

    _mark_succeeded(
        session,
        step_run,
        stdout=stdout,
        exit_code=exit_code,
        started_monotonic=started_monotonic,
    )
