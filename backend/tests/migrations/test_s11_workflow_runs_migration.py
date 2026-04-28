"""Integration tests for the S11 workflow_runs + step_runs migration.

Exercises ``s10_workflows`` ⇄ ``s11_workflow_runs`` against the real
Postgres test DB:

  1. After upgrade (head), assert ``workflow_runs`` and ``step_runs``
     exist with the expected columns + types + nullability, the FK
     CASCADE / SET NULL semantics on each edge, and the expected
     CHECK / UNIQUE constraints.
  2. ``trigger_type`` CHECK accepts the five documented values; rejects
     anything else.
  3. ``status`` CHECK on workflow_runs accepts the five documented values;
     ``status`` CHECK on step_runs accepts the five documented values.
  4. UNIQUE (workflow_run_id, step_index) on ``step_runs`` rejects
     duplicate step indexes within a single run.
  5. Deleting a parent workflow CASCADEs to workflow_runs and from there
     to step_runs.
  6. Deleting the triggering / target user nulls the FK column rather
     than dropping the run history (SET NULL).
  7. Server-defaults: ``trigger_payload`` → ``{}``, run ``status`` →
     ``'pending'``, step ``status`` → ``'pending'``, step ``stdout`` →
     ``''``, step ``stderr`` → ``''``.
  8. Downgrade to ``s10_workflows`` drops both tables cleanly.
  9. Downgrade then re-upgrade leaves the schema byte-identical.

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

S10_REV = "s10_workflows"
S11_REV = "s11_workflow_runs"
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
            f"Could not restore DB to head after S11 migration test: {restore_err}"
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
            text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
            {"t": table},
        ).all()
    return {row[0] for row in rows}


def _fk_actions(table: str) -> dict[str, str]:
    """Return {fk_name: confdeltype} where 'c'=CASCADE, 'n'=SET NULL."""
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
        "workflow_runs_columns": _columns("workflow_runs"),
        "step_runs_columns": _columns("step_runs"),
        "workflow_runs_constraints": _constraints("workflow_runs"),
        "step_runs_constraints": _constraints("step_runs"),
        "workflow_runs_fks": _fk_actions("workflow_runs"),
        "step_runs_fks": _fk_actions("step_runs"),
        "workflow_runs_indexes": _indexes("workflow_runs"),
        "step_runs_indexes": _indexes("step_runs"),
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


def _make_user(session: Session, *, email_suffix: str) -> uuid.UUID:
    """Insert a user row whose email is unique within the test session."""
    user_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO "user"
                (id, email, is_active, role, hashed_password, created_at)
            VALUES (:id, :email, TRUE, 'user', 'x', NOW())
            """
        ),
        {
            "id": user_id,
            "email": f"u-{email_suffix}-{uuid.uuid4().hex[:8]}@test.local",
        },
    )
    return user_id


def _make_workflow(
    session: Session,
    *,
    team_id: uuid.UUID,
    name: str = "_direct_claude",
) -> uuid.UUID:
    wf_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name)
            VALUES (:id, :team, :name)
            """
        ),
        {"id": wf_id, "team": team_id, "name": name},
    )
    return wf_id


def _truncate_runs() -> None:
    with Session(engine) as session:
        session.execute(text("DELETE FROM step_runs"))
        session.execute(text("DELETE FROM workflow_runs"))
        session.execute(text("DELETE FROM workflow_steps"))
        session.execute(text("DELETE FROM workflows"))
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s11_upgrade_creates_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    # workflow_runs columns
    cols = _columns("workflow_runs")
    assert set(cols) == {
        "id",
        "workflow_id",
        "team_id",
        "trigger_type",
        "triggered_by_user_id",
        "target_user_id",
        "trigger_payload",
        "status",
        "error_class",
        "started_at",
        "finished_at",
        "duration_ms",
        "last_heartbeat_at",
        "created_at",
    }, f"unexpected workflow_runs columns: {sorted(cols)}"
    assert cols["id"]["is_nullable"] == "NO"
    assert cols["workflow_id"]["is_nullable"] == "NO"
    assert cols["team_id"]["is_nullable"] == "NO"
    assert cols["trigger_type"]["is_nullable"] == "NO"
    assert cols["triggered_by_user_id"]["is_nullable"] == "YES"
    assert cols["target_user_id"]["is_nullable"] == "YES"
    assert cols["trigger_payload"]["is_nullable"] == "NO"
    assert cols["status"]["is_nullable"] == "NO"
    assert cols["error_class"]["is_nullable"] == "YES"
    assert cols["started_at"]["is_nullable"] == "YES"
    assert cols["finished_at"]["is_nullable"] == "YES"
    assert cols["duration_ms"]["is_nullable"] == "YES"
    assert cols["last_heartbeat_at"]["is_nullable"] == "YES"
    assert cols["trigger_payload"]["data_type"] == "jsonb"
    assert cols["duration_ms"]["data_type"] == "bigint"

    # step_runs columns
    cols = _columns("step_runs")
    assert set(cols) == {
        "id",
        "workflow_run_id",
        "step_index",
        "snapshot",
        "status",
        "stdout",
        "stderr",
        "exit_code",
        "error_class",
        "duration_ms",
        "started_at",
        "finished_at",
        "created_at",
    }, f"unexpected step_runs columns: {sorted(cols)}"
    assert cols["snapshot"]["is_nullable"] == "NO"
    assert cols["snapshot"]["data_type"] == "jsonb"
    assert cols["stdout"]["is_nullable"] == "NO"
    assert cols["stderr"]["is_nullable"] == "NO"
    assert cols["status"]["is_nullable"] == "NO"
    assert cols["exit_code"]["is_nullable"] == "YES"
    assert cols["duration_ms"]["data_type"] == "bigint"

    # FK semantics: workflow_id + team_id CASCADE ('c'); user FKs SET NULL ('n')
    runs_fks = _fk_actions("workflow_runs")
    assert runs_fks.get("fk_workflow_runs_workflow_id") == "c"
    assert runs_fks.get("fk_workflow_runs_team_id") == "c"
    assert runs_fks.get("fk_workflow_runs_triggered_by_user_id") == "n"
    assert runs_fks.get("fk_workflow_runs_target_user_id") == "n"

    step_fks = _fk_actions("step_runs")
    assert step_fks.get("fk_step_runs_workflow_run_id") == "c"

    # Constraints
    runs_constraints = _constraints("workflow_runs")
    assert "ck_workflow_runs_trigger_type" in runs_constraints
    assert "ck_workflow_runs_status" in runs_constraints

    steps_constraints = _constraints("step_runs")
    assert "uq_step_runs_workflow_run_id_step_index" in steps_constraints
    assert "ck_step_runs_status" in steps_constraints

    # Indexes
    runs_indexes = _indexes("workflow_runs")
    assert "ix_workflow_runs_team_id_created_at" in runs_indexes
    assert "ix_workflow_runs_status" in runs_indexes
    assert "ix_step_runs_workflow_run_id" in _indexes("step_runs")


def test_s11_trigger_type_check(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="trigger")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        for trig in (
            "button",
            "webhook",
            "schedule",
            "manual",
            "admin_manual",
        ):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_runs
                        (id, workflow_id, team_id, trigger_type)
                    VALUES (:id, :wf, :team, :trig)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "wf": wf_id,
                    "team": team_id,
                    "trig": trig,
                },
            )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_runs
                        (id, workflow_id, team_id, trigger_type)
                    VALUES (:id, :wf, :team, 'bogus')
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id, "team": team_id},
            )
            session.commit()
        session.rollback()

    _truncate_runs()


def test_s11_status_checks(alembic_cfg: Config) -> None:
    """Both run and step status CHECKs hold the documented closed sets."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="status")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        # Run status: every documented value lands; bogus rejected.
        for status in (
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        ):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_runs
                        (id, workflow_id, team_id, trigger_type, status)
                    VALUES (:id, :wf, :team, 'button', :status)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "wf": wf_id,
                    "team": team_id,
                    "status": status,
                },
            )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_runs
                        (id, workflow_id, team_id, trigger_type, status)
                    VALUES (:id, :wf, :team, 'button', 'bogus')
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id, "team": team_id},
            )
            session.commit()
        session.rollback()

        # Step status: every documented value lands; bogus rejected.
        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, team_id, trigger_type)
                VALUES (:id, :wf, :team, 'button')
                """
            ),
            {"id": run_id, "wf": wf_id, "team": team_id},
        )
        for idx, status in enumerate(
            ("pending", "running", "succeeded", "failed", "skipped")
        ):
            session.execute(
                text(
                    """
                    INSERT INTO step_runs
                        (id, workflow_run_id, step_index, snapshot, status)
                    VALUES (:id, :run, :idx, '{}'::jsonb, :status)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "run": run_id,
                    "idx": idx,
                    "status": status,
                },
            )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO step_runs
                        (id, workflow_run_id, step_index, snapshot, status)
                    VALUES (:id, :run, 99, '{}'::jsonb, 'bogus')
                    """
                ),
                {"id": uuid.uuid4(), "run": run_id},
            )
            session.commit()
        session.rollback()

    _truncate_runs()


def test_s11_step_runs_unique_step_index(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="step-uniq")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()
        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, team_id, trigger_type)
                VALUES (:id, :wf, :team, 'button')
                """
            ),
            {"id": run_id, "wf": wf_id, "team": team_id},
        )
        session.execute(
            text(
                """
                INSERT INTO step_runs
                    (id, workflow_run_id, step_index, snapshot)
                VALUES (:id, :run, 0, '{"action": "claude"}'::jsonb)
                """
            ),
            {"id": uuid.uuid4(), "run": run_id},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO step_runs
                        (id, workflow_run_id, step_index, snapshot)
                    VALUES (:id, :run, 0, '{}'::jsonb)
                    """
                ),
                {"id": uuid.uuid4(), "run": run_id},
            )
            session.commit()
        session.rollback()

    _truncate_runs()


def test_s11_workflow_delete_cascades_runs_and_steps(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()
        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, team_id, trigger_type)
                VALUES (:id, :wf, :team, 'button')
                """
            ),
            {"id": run_id, "wf": wf_id, "team": team_id},
        )
        session.execute(
            text(
                """
                INSERT INTO step_runs
                    (id, workflow_run_id, step_index, snapshot)
                VALUES (:id, :run, 0, '{}'::jsonb)
                """
            ),
            {"id": uuid.uuid4(), "run": run_id},
        )
        session.commit()

        # Sanity: rows exist
        assert session.execute(
            text("SELECT COUNT(*) FROM workflow_runs WHERE workflow_id = :w"),
            {"w": wf_id},
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT COUNT(*) FROM step_runs WHERE workflow_run_id = :r"),
            {"r": run_id},
        ).scalar_one() == 1

        session.execute(text("DELETE FROM workflows WHERE id = :id"), {"id": wf_id})
        session.commit()

        assert session.execute(
            text("SELECT COUNT(*) FROM workflow_runs WHERE workflow_id = :w"),
            {"w": wf_id},
        ).scalar_one() == 0, (
            "workflow_runs should cascade-delete with parent workflow"
        )
        assert session.execute(
            text("SELECT COUNT(*) FROM step_runs WHERE workflow_run_id = :r"),
            {"r": run_id},
        ).scalar_one() == 0, (
            "step_runs should cascade through workflow_runs"
        )

    _truncate_runs()


def test_s11_user_delete_sets_run_user_fks_null(alembic_cfg: Config) -> None:
    """Triggering / target user delete must NULL the FK, not drop the run."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="user-del")
        triggered_by = _make_user(session, email_suffix="trig")
        target_user = _make_user(session, email_suffix="targ")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, team_id, trigger_type,
                     triggered_by_user_id, target_user_id)
                VALUES (:id, :wf, :team, 'button', :trig, :targ)
                """
            ),
            {
                "id": run_id,
                "wf": wf_id,
                "team": team_id,
                "trig": triggered_by,
                "targ": target_user,
            },
        )
        session.commit()

        # Delete both users — FK columns should null out, run row stays.
        session.execute(
            text('DELETE FROM "user" WHERE id IN (:a, :b)'),
            {"a": triggered_by, "b": target_user},
        )
        session.commit()

        row = session.execute(
            text(
                """
                SELECT triggered_by_user_id, target_user_id
                FROM workflow_runs WHERE id = :id
                """
            ),
            {"id": run_id},
        ).one()
        assert row[0] is None, (
            f"triggered_by_user_id should be NULLed, got {row[0]}"
        )
        assert row[1] is None, (
            f"target_user_id should be NULLed, got {row[1]}"
        )

    _truncate_runs()


def test_s11_server_defaults(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="defaults")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, team_id, trigger_type)
                VALUES (:id, :wf, :team, 'button')
                """
            ),
            {"id": run_id, "wf": wf_id, "team": team_id},
        )
        session.execute(
            text(
                """
                INSERT INTO step_runs (id, workflow_run_id, step_index, snapshot)
                VALUES (:id, :run, 0, '{}'::jsonb)
                """
            ),
            {"id": uuid.uuid4(), "run": run_id},
        )
        session.commit()

        run = session.execute(
            text(
                """
                SELECT trigger_payload, status FROM workflow_runs WHERE id = :id
                """
            ),
            {"id": run_id},
        ).one()
        assert run[0] == {}, (
            f"trigger_payload server default should be {{}}, got {run[0]}"
        )
        assert run[1] == "pending", (
            f"status server default should be 'pending', got {run[1]}"
        )

        step = session.execute(
            text(
                """
                SELECT status, stdout, stderr FROM step_runs
                WHERE workflow_run_id = :run
                """
            ),
            {"run": run_id},
        ).one()
        assert step[0] == "pending", (
            f"step status server default should be 'pending', got {step[0]}"
        )
        assert step[1] == "", (
            f"stdout server default should be '', got {step[1]!r}"
        )
        assert step[2] == "", (
            f"stderr server default should be '', got {step[2]!r}"
        )

    _truncate_runs()


def test_s11_downgrade_drops_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    command.downgrade(alembic_cfg, S10_REV)

    assert _columns("workflow_runs") == {}, (
        "workflow_runs columns should be empty after downgrade"
    )
    assert _columns("step_runs") == {}, (
        "step_runs columns should be empty after downgrade"
    )
    # autouse fixture restores head.


def test_s11_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    before = _schema_snapshot()
    assert before["workflow_runs_columns"], (
        "precondition: workflow_runs table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S10_REV)
    command.upgrade(alembic_cfg, S11_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )


def test_s11_models_import_clean() -> None:
    """SQLModel rows + DTOs must import without raising."""
    from app.models import (  # noqa: F401
        StepRun,
        StepRunPublic,
        StepRunStatus,
        WorkflowRun,
        WorkflowRunCreate,
        WorkflowRunDispatched,
        WorkflowRunPublic,
        WorkflowRunStatus,
        WorkflowRunTriggerType,
    )

    assert {e.value for e in WorkflowRunTriggerType} == {
        "button",
        "webhook",
        "schedule",
        "manual",
        "admin_manual",
    }
    assert {e.value for e in WorkflowRunStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }
    assert {e.value for e in StepRunStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "skipped",
    }
