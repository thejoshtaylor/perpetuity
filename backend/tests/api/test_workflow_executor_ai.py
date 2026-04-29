"""Unit tests for `app.workflows.executors.ai.run_ai_step` (M005/S02/T03).

Covers the full per-step lifecycle for the claude / codex executor:
  * happy path — secret fetched, orchestrator returns exit 0, step succeeds.
  * `MissingTeamSecretError` → step failed with `error_class='missing_team_secret'`.
  * `TeamSecretDecryptError` → step failed with `error_class='team_secret_decrypt_failed'`.
  * orchestrator HTTP error → step failed with `error_class='orchestrator_exec_failed'`.
  * orchestrator non-200 status → same error_class, status code in stderr.
  * orchestrator returns exit_code != 0 → step failed with `error_class='cli_nonzero'`.
  * codex action uses `OPENAI_API_KEY` env key + `openai_api_key` secret.
  * `derive_session_id` is deterministic for the same (user, team, run).
  * prompt template substitution swaps `{prompt}` from trigger_payload.

Test isolation mirrors the existing M005/S01 helper test (autouse Fernet
key + autouse table cleaner). The orchestrator HTTP boundary is mocked
with `monkeypatch.setattr` on `httpx.Client` so no live socket is needed.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, delete

from app.api.team_secrets import set_team_secret
from app.api.team_secrets_registry import CLAUDE_API_KEY, OPENAI_API_KEY
from app.models import StepRun, Team, TeamSecret, WorkflowRun
from app.workflows.executors import ai as ai_executor
from app.workflows.executors.ai import derive_session_id, run_ai_step

# Same Fernet key the M005/S01 unit tests pin — keeps encrypt/decrypt
# round-trips deterministic without depending on whatever env was loaded
# elsewhere in the session.
_TEST_FERNET_KEY = "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="

_VALID_CLAUDE_KEY = "sk-ant-" + ("A" * 40)
_VALID_OPENAI_KEY = "sk-" + ("B" * 40)


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


@pytest.fixture(autouse=True)
def _clean_workflow_rows(db: Session) -> Generator[None, None, None]:
    """Wipe every workflow-engine row before AND after each test.

    step_runs cascades on workflow_runs delete; workflow_steps cascades on
    workflows delete. Order matters: step_runs first so the FK tree
    unwinds cleanly even if workflow_runs grew rows mid-test.
    """
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.execute(delete(TeamSecret))
    db.commit()
    yield
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.execute(delete(TeamSecret))
    db.commit()


def _make_team(db: Session) -> Team:
    suffix = uuid.uuid4().hex[:8]
    team = Team(
        name=f"ai-exec-test-{suffix}",
        slug=f"ai-exec-test-{suffix}",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_user(db: Session) -> uuid.UUID:
    """Insert a minimal user row and return its id.

    The AI executor reads `target_user_id` off the WorkflowRun; we don't
    need a full User object, just a row that satisfies the FK.
    """
    from app.core.security import get_password_hash
    from app.models import User

    user = User(
        email=f"ai-exec-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("not-checked-here"),
        full_name="AI Exec Test",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _make_workflow_and_run(
    db: Session,
    team: Team,
    user_id: uuid.UUID,
    *,
    action: str = "claude",
    prompt: str = "List the files in this repo",
    workflow_name: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a workflow + step + workflow_run + return (run_id, step_run_id).

    The step_run is inserted in `running` status with the snapshot frozen
    so `run_ai_step` has the row it expects to drive to terminal status.
    """
    workflow_id = uuid.uuid4()
    step_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_run_id = uuid.uuid4()
    name = workflow_name or f"_direct_{action}_{uuid.uuid4().hex[:6]}"

    db.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name, scope, system_owned)
            VALUES (:id, :team, :name, 'user', TRUE)
            """
        ),
        {"id": workflow_id, "team": team.id, "name": name},
    )
    db.execute(
        text(
            """
            INSERT INTO workflow_steps
                (id, workflow_id, step_index, action, config)
            VALUES
                (:id, :wf, 0, :action, CAST(:cfg AS JSONB))
            """
        ),
        {
            "id": step_id,
            "wf": workflow_id,
            "action": action,
            "cfg": '{"prompt_template":"{prompt}"}',
        },
    )
    db.execute(
        text(
            """
            INSERT INTO workflow_runs
                (id, workflow_id, team_id, trigger_type,
                 triggered_by_user_id, target_user_id,
                 trigger_payload, status)
            VALUES
                (:id, :wf, :team, 'button',
                 :user, :user,
                 CAST(:payload AS JSONB), 'running')
            """
        ),
        {
            "id": run_id,
            "wf": workflow_id,
            "team": team.id,
            "user": user_id,
            "payload": f'{{"prompt": "{prompt}"}}',
        },
    )
    snapshot = (
        '{"id":"'
        + str(step_id)
        + '","workflow_id":"'
        + str(workflow_id)
        + '","step_index":0,"action":"'
        + action
        + '","config":{"prompt_template":"{prompt}"}}'
    )
    db.execute(
        text(
            """
            INSERT INTO step_runs
                (id, workflow_run_id, step_index, snapshot, status)
            VALUES
                (:id, :run, 0, CAST(:snap AS JSONB), 'running')
            """
        ),
        {"id": step_run_id, "run": run_id, "snap": snapshot},
    )
    db.commit()
    return run_id, step_run_id


class _FakeResponse:
    """Minimal `httpx.Response` stand-in for the executor's call site."""

    def __init__(self, status_code: int, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Records the last POST and replays a scripted response.

    Drop-in for `httpx.Client(timeout=...)` — supports the context manager
    protocol the executor uses.
    """

    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.captured: dict[str, Any] = {}

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.captured = {
            "url": url,
            "json": json,
            "headers": headers,
        }
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch, fake: _FakeClient
) -> None:
    """Replace `httpx.Client` (as referenced from the executor module) with
    a factory returning the supplied fake.
    """

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return fake

    monkeypatch.setattr(ai_executor.httpx, "Client", _factory)


# ---------------------------------------------------------------------------
# derive_session_id
# ---------------------------------------------------------------------------


def test_derive_session_id_is_deterministic() -> None:
    """Same (user, team, run) triple → same session_id forever.

    Re-runs / Celery double-deliveries hit the same workspace container
    so logs grep clean and we don't blow per-team container counts.
    """
    user_id = uuid.uuid4()
    team_id = uuid.uuid4()
    run_id = uuid.uuid4()
    a = derive_session_id(user_id, team_id, run_id)
    b = derive_session_id(user_id, team_id, run_id)
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_derive_session_id_changes_with_run_id() -> None:
    """Different runs → different session_ids (so logs separate cleanly)."""
    user_id = uuid.uuid4()
    team_id = uuid.uuid4()
    a = derive_session_id(user_id, team_id, uuid.uuid4())
    b = derive_session_id(user_id, team_id, uuid.uuid4())
    assert a != b


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_ai_step_happy_path_claude(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Secret present + orchestrator returns exit 0 → step succeeded.

    Asserts the row carries the merged stdout, exit_code=0, no error_class,
    and that the request body sent to the orchestrator carries the right
    cmd shape, the API key in env (NOT in cmd), and the deterministic
    session_id in the URL.
    """
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    run_id, step_run_id = _make_workflow_and_run(
        db, team, user_id, action="claude", prompt="Hello from claude"
    )
    expected_session_id = derive_session_id(user_id, team.id, run_id)

    fake = _FakeClient(
        _FakeResponse(
            200,
            {"stdout": "file1.txt\nfile2.txt", "exit_code": 0, "duration_ms": 123},
        )
    )
    _patch_httpx_client(monkeypatch, fake)

    with caplog.at_level(logging.INFO, logger="app.workflows.executors.ai"):
        run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "succeeded"
    assert row.exit_code == 0
    assert row.stdout == "file1.txt\nfile2.txt"
    assert row.stderr == ""
    assert row.error_class is None
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.finished_at is not None

    # Outgoing HTTP shape: deterministic session_id in URL, API key in env.
    assert str(expected_session_id) in fake.captured["url"]
    assert fake.captured["json"]["env"]["ANTHROPIC_API_KEY"] == _VALID_CLAUDE_KEY
    assert fake.captured["json"]["env"]["PROMPT"] == "Hello from claude"
    assert "$PROMPT" in fake.captured["json"]["cmd"]
    # API key NEVER appears in the cmd argv list.
    assert _VALID_CLAUDE_KEY not in " ".join(fake.captured["json"]["cmd"])
    # Shared-secret header is set.
    assert "X-Orchestrator-Key" in fake.captured["headers"]

    # Observability: step_run_started + step_run_succeeded land, neither
    # logs the prompt or the API key.
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "step_run_started" in log_text
    assert "step_run_succeeded" in log_text
    assert "Hello from claude" not in log_text
    assert _VALID_CLAUDE_KEY not in log_text


def test_run_ai_step_happy_path_codex(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex action → OPENAI_API_KEY env key + `codex` cli, same lifecycle."""
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, OPENAI_API_KEY, _VALID_OPENAI_KEY)
    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="codex")

    fake = _FakeClient(
        _FakeResponse(200, {"stdout": "ok", "exit_code": 0, "duration_ms": 10})
    )
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "succeeded"
    assert fake.captured["json"]["env"]["OPENAI_API_KEY"] == _VALID_OPENAI_KEY
    # Cmd starts with the codex binary, not claude.
    assert fake.captured["json"]["cmd"][0] == "codex"
    assert fake.captured["json"]["action"] == "codex"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_run_ai_step_missing_team_secret(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No row in team_secrets for claude_api_key → step failed,
    error_class='missing_team_secret', stderr names the missing key.

    The orchestrator HTTP path MUST NOT be reached.
    """
    team = _make_team(db)
    user_id = _make_user(db)
    # Deliberately no set_team_secret call.
    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="claude")

    fake = _FakeClient(_FakeResponse(200, {"stdout": "", "exit_code": 0, "duration_ms": 0}))
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "missing_team_secret"
    assert "claude_api_key" in row.stderr
    # Orchestrator was never called — secret check happens before HTTP.
    assert fake.captured == {}


def test_run_ai_step_decrypt_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tampered ciphertext → step failed with team_secret_decrypt_failed.

    The decrypt failure path mirrors the M005/S01 unit-test technique:
    write valid ciphertext, then SQL-overwrite with garbage so
    `get_team_secret` raises `TeamSecretDecryptError`.
    """
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    db.execute(
        text(
            """
            UPDATE team_secrets
            SET value_encrypted = :ct
            WHERE team_id = :t AND key = :k
            """
        ),
        {
            "t": team.id,
            "k": CLAUDE_API_KEY,
            "ct": b"not-a-valid-fernet-token",
        },
    )
    db.commit()

    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="claude")
    fake = _FakeClient(_FakeResponse(200, {"stdout": "", "exit_code": 0, "duration_ms": 0}))
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "team_secret_decrypt_failed"
    # Orchestrator never called.
    assert fake.captured == {}


def test_run_ai_step_orchestrator_http_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """httpx.ConnectError → step failed with orchestrator_exec_failed.

    Asserts stderr is the exception class NAME (not the str(exc) which
    can leak request URLs in httpx error messages).
    """
    import httpx

    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="claude")

    fake = _FakeClient(httpx.ConnectError("connection refused — host is gone"))
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "orchestrator_exec_failed"
    assert row.stderr == "ConnectError"
    # The leaky exception message MUST NOT land on the row.
    assert "host is gone" not in row.stderr


def test_run_ai_step_orchestrator_5xx_status(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator returns 503 → step failed, status code in stderr."""
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="claude")

    fake = _FakeClient(_FakeResponse(503, {"detail": "docker_unavailable"}))
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "orchestrator_exec_failed"
    assert "503" in row.stderr


def test_run_ai_step_cli_nonzero_exit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator returns 200 + exit_code=1 → step failed, error_class=cli_nonzero,
    stdout persisted (R018 forever-debuggable history).
    """
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    _, step_run_id = _make_workflow_and_run(db, team, user_id, action="claude")

    fake = _FakeClient(
        _FakeResponse(
            200,
            {
                "stdout": "Error: Anthropic credit balance is too low\n",
                "exit_code": 1,
                "duration_ms": 250,
            },
        )
    )
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "cli_nonzero"
    assert row.exit_code == 1
    assert "credit balance" in row.stdout


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_run_ai_step_substitutes_prompt_into_template(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`{prompt}` inside `prompt_template` is replaced with trigger_payload['prompt'].

    Asserts the env's PROMPT is the rendered text, and the rendering
    behaves as a literal-substitution (str.replace), not str.format.
    """
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)

    # Build a custom workflow with a non-trivial template.
    workflow_id = uuid.uuid4()
    step_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_run_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name, scope, system_owned)
            VALUES (:id, :t, :n, 'user', FALSE)
            """
        ),
        {"id": workflow_id, "t": team.id, "n": f"custom-{uuid.uuid4().hex[:6]}"},
    )
    db.execute(
        text(
            """
            INSERT INTO workflow_steps (id, workflow_id, step_index, action, config)
            VALUES (:id, :wf, 0, 'claude', CAST(:cfg AS JSONB))
            """
        ),
        {
            "id": step_id,
            "wf": workflow_id,
            "cfg": '{"prompt_template":"You are a code reviewer. Review: {prompt}"}',
        },
    )
    db.execute(
        text(
            """
            INSERT INTO workflow_runs
                (id, workflow_id, team_id, trigger_type,
                 triggered_by_user_id, target_user_id,
                 trigger_payload, status)
            VALUES
                (:id, :wf, :t, 'button', :u, :u,
                 CAST(:p AS JSONB), 'running')
            """
        ),
        {
            "id": run_id,
            "wf": workflow_id,
            "t": team.id,
            "u": user_id,
            "p": '{"prompt": "the M005 PR"}',
        },
    )
    db.execute(
        text(
            """
            INSERT INTO step_runs
                (id, workflow_run_id, step_index, snapshot, status)
            VALUES
                (:id, :r, 0, CAST(:snap AS JSONB), 'running')
            """
        ),
        {
            "id": step_run_id,
            "r": run_id,
            "snap": (
                '{"id":"' + str(step_id) + '","workflow_id":"'
                + str(workflow_id)
                + '","step_index":0,"action":"claude",'
                + '"config":{"prompt_template":"You are a code reviewer. Review: {prompt}"}}'
            ),
        },
    )
    db.commit()

    fake = _FakeClient(
        _FakeResponse(200, {"stdout": "ok", "exit_code": 0, "duration_ms": 1})
    )
    _patch_httpx_client(monkeypatch, fake)

    run_ai_step(db, step_run_id)

    rendered = fake.captured["json"]["env"]["PROMPT"]
    assert rendered == "You are a code reviewer. Review: the M005 PR"
