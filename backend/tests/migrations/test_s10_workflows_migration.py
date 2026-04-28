"""Integration tests for the S10 workflows + workflow_steps migration.

Exercises ``s09_team_secrets`` ⇄ ``s10_workflows`` against the real
Postgres test DB:

  1. After upgrade (head), assert ``workflows`` and ``workflow_steps``
     exist with the expected columns + types + nullability, the team /
     workflow FK CASCADE, and the composite unique constraints.
  2. UNIQUE (team_id, name) on ``workflows`` rejects duplicate seed
     attempts. UNIQUE (workflow_id, step_index) on ``workflow_steps``
     rejects duplicate step indexes.
  3. ``scope`` CHECK accepts the three documented values and rejects
     anything else; ``action`` CHECK accepts the four documented values
     and rejects anything else.
  4. Deleting a parent team CASCADEs to workflows and from there to
     workflow_steps.
  5. ``system_owned`` server-default lands FALSE; ``scope`` server-default
     lands ``'user'``; ``config`` server-default lands ``{}``.
  6. Downgrade to ``s09_team_secrets`` drops both tables cleanly.
  7. Downgrade then re-upgrade leaves the schema byte-identical.

Uses the MEM016 autouse fixture pattern (commit + close autouse session,
``engine.dispose()``) to avoid AccessShareLock deadlocks with alembic DDL.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.db import engine

S09_REV = "s09_team_secrets"
S10_REV = "s10_workflows"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend


def _alembic_config() -> Config:
    ini = BACKEND_ROOT / "alembic.ini"
    if not ini.exists():
        pytest.skip(f"alembic.ini not found at {ini}; cannot bootstrap alembic")
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "app" / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def alembic_cfg() -> Config:
    return _alembic_config()


@pytest.fixture(autouse=True)
def _release_autouse_db_session(db: Session) -> Generator[None, None, None]:
    """Release session + engine pool before alembic runs (MEM014/MEM016)."""
    db.commit()
    db.expire_all()
    db.close()
    engine.dispose()
    yield


@pytest.fixture(autouse=True)
def _restore_head_after(
    alembic_cfg: Config, _release_autouse_db_session: None
) -> Generator[None, None, None]:
    """Force the DB back to head after every test, even on failure."""
    yield
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as restore_err:  # pragma: no cover - defensive
        pytest.fail(
            f"Could not restore DB to head after S10 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DB introspection helpers
# ---------------------------------------------------------------------------


def _columns(table: str) -> dict[str, dict[str, str | int | None]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type,
                       character_maximum_length, column_default
                FROM information_schema.columns
                WHERE table_name = :t
                ORDER BY column_name
                """
            ),
            {"t": table},
        ).all()
    return {
        row[0]: {
            "is_nullable": row[1],
            "data_type": row[2],
            "char_max_length": row[3],
            "column_default": row[4],
        }
        for row in rows
    }


def _constraints(table: str) -> dict[str, str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT conname, contype FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                WHERE pg_class.relname = :t
                ORDER BY conname
                """
            ),
            {"t": table},
        ).all()
    return {row[0]: row[1] for row in rows}


def _indexes(table: str) -> set[str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes WHERE tablename = :t
                """
            ),
            {"t": table},
        ).all()
    return {row[0] for row in rows}


def _fk_actions(table: str) -> dict[str, str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT con.conname, con.confdeltype
                FROM pg_constraint con
                JOIN pg_class cls ON cls.oid = con.conrelid
                WHERE cls.relname = :t AND con.contype = 'f'
                ORDER BY con.conname
                """
            ),
            {"t": table},
        ).all()
    return {row[0]: row[1] for row in rows}


def _schema_snapshot() -> dict[str, object]:
    return {
        "workflows_columns": _columns("workflows"),
        "workflow_steps_columns": _columns("workflow_steps"),
        "workflows_constraints": _constraints("workflows"),
        "workflow_steps_constraints": _constraints("workflow_steps"),
        "workflows_fks": _fk_actions("workflows"),
        "workflow_steps_fks": _fk_actions("workflow_steps"),
        "workflows_indexes": _indexes("workflows"),
        "workflow_steps_indexes": _indexes("workflow_steps"),
    }


def _make_team(session: Session, *, slug_suffix: str) -> uuid.UUID:
    team_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO team (id, name, slug, is_personal, created_at)
            VALUES (:id, :name, :slug, FALSE, NOW())
            """
        ),
        {
            "id": team_id,
            "name": f"t-{slug_suffix}",
            "slug": f"t-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    return team_id


def _truncate() -> None:
    with Session(engine) as session:
        # workflow_steps depends on workflows via FK; drop in order
        session.execute(text("DELETE FROM workflow_steps"))
        session.execute(text("DELETE FROM workflows"))
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s10_upgrade_creates_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    # workflows columns
    cols = _columns("workflows")
    assert set(cols) == {
        "id",
        "team_id",
        "name",
        "description",
        "scope",
        "system_owned",
        "created_at",
        "updated_at",
    }, f"unexpected workflows columns: {sorted(cols)}"
    assert cols["id"]["is_nullable"] == "NO"
    assert cols["team_id"]["is_nullable"] == "NO"
    assert cols["name"]["is_nullable"] == "NO"
    assert cols["description"]["is_nullable"] == "YES"
    assert cols["scope"]["is_nullable"] == "NO"
    assert cols["system_owned"]["is_nullable"] == "NO"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["updated_at"]["is_nullable"] == "NO"
    assert cols["id"]["data_type"] == "uuid"
    assert cols["team_id"]["data_type"] == "uuid"
    assert cols["name"]["data_type"] in {"character varying", "varchar"}
    assert cols["name"]["char_max_length"] == 255
    assert cols["description"]["data_type"] == "text"
    assert cols["scope"]["data_type"] in {"character varying", "varchar"}
    assert cols["scope"]["char_max_length"] == 32
    assert cols["system_owned"]["data_type"] == "boolean"

    # workflow_steps columns
    cols = _columns("workflow_steps")
    assert set(cols) == {
        "id",
        "workflow_id",
        "step_index",
        "action",
        "config",
        "created_at",
        "updated_at",
    }, f"unexpected workflow_steps columns: {sorted(cols)}"
    assert cols["id"]["is_nullable"] == "NO"
    assert cols["workflow_id"]["is_nullable"] == "NO"
    assert cols["step_index"]["is_nullable"] == "NO"
    assert cols["action"]["is_nullable"] == "NO"
    assert cols["config"]["is_nullable"] == "NO"
    assert cols["id"]["data_type"] == "uuid"
    assert cols["workflow_id"]["data_type"] == "uuid"
    assert cols["step_index"]["data_type"] == "integer"
    assert cols["action"]["data_type"] in {"character varying", "varchar"}
    assert cols["action"]["char_max_length"] == 64
    assert cols["config"]["data_type"] == "jsonb"

    # FK CASCADE on team and workflow
    workflows_fks = _fk_actions("workflows")
    assert workflows_fks.get("fk_workflows_team_id") == "c", (
        f"workflows.team_id FK should ON DELETE CASCADE, got {workflows_fks}"
    )
    steps_fks = _fk_actions("workflow_steps")
    assert steps_fks.get("fk_workflow_steps_workflow_id") == "c", (
        f"workflow_steps.workflow_id FK should ON DELETE CASCADE, got {steps_fks}"
    )

    # Unique + check constraints exist by name
    workflows_constraints = _constraints("workflows")
    assert "uq_workflows_team_id_name" in workflows_constraints
    assert "ck_workflows_scope" in workflows_constraints
    steps_constraints = _constraints("workflow_steps")
    assert "uq_workflow_steps_workflow_id_step_index" in steps_constraints
    assert "ck_workflow_steps_action" in steps_constraints

    # Indexes
    assert "ix_workflows_team_id" in _indexes("workflows")
    assert "ix_workflow_steps_workflow_id" in _indexes("workflow_steps")


def test_s10_workflows_unique_team_id_name(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="uniq")
        session.commit()

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name, scope, system_owned)
                VALUES (:id, :team, :name, 'user', TRUE)
                """
            ),
            {"id": wf_id, "team": team_id, "name": "_direct_claude"},
        )
        session.commit()

        # duplicate (team_id, name) must fail
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflows (id, team_id, name, scope, system_owned)
                    VALUES (:id, :team, :name, 'user', TRUE)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "team": team_id,
                    "name": "_direct_claude",
                },
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s10_workflow_steps_unique_step_index(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="step-uniq")
        session.commit()

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name)
                VALUES (:id, :team, :name)
                """
            ),
            {"id": wf_id, "team": team_id, "name": "_direct_claude"},
        )
        session.execute(
            text(
                """
                INSERT INTO workflow_steps
                    (id, workflow_id, step_index, action, config)
                VALUES (:id, :wf, 0, 'claude', '{"prompt_template": "{prompt}"}'::jsonb)
                """
            ),
            {"id": uuid.uuid4(), "wf": wf_id},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_steps
                        (id, workflow_id, step_index, action, config)
                    VALUES (:id, :wf, 0, 'codex', '{}'::jsonb)
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id},
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s10_scope_check_constraint(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="scope")
        session.commit()

        # Three valid scope values land successfully
        for scope in ("user", "team", "round_robin"):
            session.execute(
                text(
                    """
                    INSERT INTO workflows (id, team_id, name, scope)
                    VALUES (:id, :team, :name, :scope)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "team": team_id,
                    "name": f"wf-{scope}",
                    "scope": scope,
                },
            )
        session.commit()

        # Bogus scope value must fail
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflows (id, team_id, name, scope)
                    VALUES (:id, :team, :name, 'bogus')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "team": team_id,
                    "name": "wf-bogus",
                },
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s10_action_check_constraint(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="action")
        session.commit()

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name)
                VALUES (:id, :team, :name)
                """
            ),
            {"id": wf_id, "team": team_id, "name": "wf-action"},
        )
        session.commit()

        for idx, action in enumerate(("claude", "codex", "shell", "git")):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_steps
                        (id, workflow_id, step_index, action)
                    VALUES (:id, :wf, :idx, :action)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "wf": wf_id,
                    "idx": idx,
                    "action": action,
                },
            )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_steps
                        (id, workflow_id, step_index, action)
                    VALUES (:id, :wf, 99, 'bogus')
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id},
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s10_team_delete_cascades(alembic_cfg: Config) -> None:
    """Deleting a parent team must cascade workflows -> workflow_steps."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        session.commit()

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name)
                VALUES (:id, :team, :name)
                """
            ),
            {"id": wf_id, "team": team_id, "name": "_direct_claude"},
        )
        session.execute(
            text(
                """
                INSERT INTO workflow_steps
                    (id, workflow_id, step_index, action)
                VALUES (:id, :wf, 0, 'claude')
                """
            ),
            {"id": uuid.uuid4(), "wf": wf_id},
        )
        session.commit()

        before_workflows = session.execute(
            text("SELECT COUNT(*) FROM workflows WHERE team_id = :t"),
            {"t": team_id},
        ).scalar_one()
        before_steps = session.execute(
            text("SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = :w"),
            {"w": wf_id},
        ).scalar_one()
        assert before_workflows == 1
        assert before_steps == 1

        session.execute(text("DELETE FROM team WHERE id = :id"), {"id": team_id})
        session.commit()

        after_workflows = session.execute(
            text("SELECT COUNT(*) FROM workflows WHERE team_id = :t"),
            {"t": team_id},
        ).scalar_one()
        after_steps = session.execute(
            text("SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = :w"),
            {"w": wf_id},
        ).scalar_one()
        assert after_workflows == 0, (
            "workflows should cascade-delete with parent team"
        )
        assert after_steps == 0, (
            "workflow_steps should cascade through workflows on team delete"
        )

    _truncate()


def test_s10_server_defaults(alembic_cfg: Config) -> None:
    """Insert without scope/system_owned/config must land defaults."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="defaults")
        session.commit()

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name)
                VALUES (:id, :team, :name)
                """
            ),
            {"id": wf_id, "team": team_id, "name": "wf-defaults"},
        )
        session.execute(
            text(
                """
                INSERT INTO workflow_steps (id, workflow_id, step_index, action)
                VALUES (:id, :wf, 0, 'claude')
                """
            ),
            {"id": uuid.uuid4(), "wf": wf_id},
        )
        session.commit()

        wf_row = session.execute(
            text(
                "SELECT scope, system_owned FROM workflows WHERE id = :id"
            ),
            {"id": wf_id},
        ).one()
        assert wf_row[0] == "user", f"scope default should be 'user', got {wf_row[0]}"
        assert wf_row[1] is False, (
            f"system_owned default should be FALSE, got {wf_row[1]}"
        )

        step_row = session.execute(
            text(
                "SELECT config FROM workflow_steps WHERE workflow_id = :w"
            ),
            {"w": wf_id},
        ).one()
        assert step_row[0] == {}, f"config default should be {{}}, got {step_row[0]}"

    _truncate()


def test_s10_downgrade_drops_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S09_REV)

    assert _columns("workflows") == {}, (
        "workflows columns should be empty after downgrade"
    )
    assert _columns("workflow_steps") == {}, (
        "workflow_steps columns should be empty after downgrade"
    )
    # autouse fixture restores head.


def test_s10_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["workflows_columns"], (
        "precondition: workflows table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S09_REV)
    command.upgrade(alembic_cfg, S10_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )


def test_s10_models_import_clean() -> None:
    """SQLModel rows + DTOs must import without raising — guards models.py drift."""
    from app.models import (  # noqa: F401
        StepRunStatus,
        Workflow,
        WorkflowAction,
        WorkflowPublic,
        WorkflowRunStatus,
        WorkflowRunTriggerType,
        WorkflowScope,
        WorkflowStep,
        WorkflowStepPublic,
        WorkflowWithStepsPublic,
        WorkflowsPublic,
    )

    # Sanity-check enum literal sets match the migration's CHECK constraints.
    assert {e.value for e in WorkflowScope} == {"user", "team", "round_robin"}
    assert {e.value for e in WorkflowAction} == {"claude", "codex", "shell", "git"}
