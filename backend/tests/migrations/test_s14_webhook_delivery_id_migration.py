"""Integration tests for the S14 webhook_delivery_id migration.

Exercises ``s13_workflow_crud_extensions`` ⇄ ``s14_webhook_delivery_id``
against the real Postgres test DB.

Coverage:
  1. Column added — ``webhook_delivery_id`` is VARCHAR(64) NULLABLE on
     ``workflow_runs`` after upgrade.
  2. Unique constraint present — ``uq_workflow_runs_webhook_delivery_id``
     exists in pg_constraint after upgrade.
  3. Duplicate delivery_id raises IntegrityError; distinct values are
     accepted; NULL values coexist (PostgreSQL NULL uniqueness semantics).
  4. Downgrade removes the column and unique constraint cleanly.
  5. Downgrade + re-upgrade leaves the schema identical (round-trip).

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

S13_REV = "s13_workflow_crud_extensions"
S14_REV = "s14_webhook_delivery_id"
BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
            f"Could not restore DB to head after S14 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns(table: str) -> dict[str, dict]:
    with Session(engine) as s:
        rows = s.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type,
                       character_maximum_length
                FROM information_schema.columns
                WHERE table_name = :t
                ORDER BY column_name
                """
            ),
            {"t": table},
        ).all()
    return {
        r[0]: {
            "is_nullable": r[1],
            "data_type": r[2],
            "char_max_length": r[3],
        }
        for r in rows
    }


def _constraints(table: str) -> dict[str, str]:
    with Session(engine) as s:
        rows = s.execute(
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
    return {r[0]: r[1] for r in rows}


def _make_team(session: Session, *, slug_suffix: str) -> uuid.UUID:
    team_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO team (id, name, slug, is_personal, created_at) "
            "VALUES (:id, :name, :slug, FALSE, NOW())"
        ),
        {
            "id": team_id,
            "name": f"t-{slug_suffix}",
            "slug": f"t-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    return team_id


def _make_workflow(session: Session, *, team_id: uuid.UUID) -> uuid.UUID:
    wf_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO workflows (id, team_id, name) "
            "VALUES (:id, :team, :name)"
        ),
        {"id": wf_id, "team": team_id, "name": f"wf-{uuid.uuid4().hex[:8]}"},
    )
    return wf_id


def _insert_run(
    session: Session,
    *,
    wf_id: uuid.UUID,
    team_id: uuid.UUID,
    delivery_id: str | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, team_id, trigger_type, webhook_delivery_id) "
            "VALUES (:id, :wf, :team, 'webhook', :did)"
        ),
        {"id": run_id, "wf": wf_id, "team": team_id, "did": delivery_id},
    )
    return run_id


def _truncate_runs() -> None:
    with Session(engine) as s:
        s.execute(text("DELETE FROM step_runs"))
        s.execute(text("DELETE FROM workflow_runs"))
        s.execute(text("DELETE FROM workflow_steps"))
        s.execute(text("DELETE FROM workflows"))
        s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s14_column_added(alembic_cfg: Config) -> None:
    """After upgrade, webhook_delivery_id is VARCHAR(64) NULLABLE."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    cols = _columns("workflow_runs")
    assert "webhook_delivery_id" in cols, (
        "webhook_delivery_id column not found in workflow_runs"
    )
    col = cols["webhook_delivery_id"]
    assert col["is_nullable"] == "YES", (
        f"webhook_delivery_id should be NULLABLE, got {col['is_nullable']}"
    )
    assert col["data_type"] == "character varying", (
        f"Expected character varying, got {col['data_type']}"
    )
    assert col["char_max_length"] == 64, (
        f"Expected max length 64, got {col['char_max_length']}"
    )

    _truncate_runs()


def test_s14_unique_constraint_present(alembic_cfg: Config) -> None:
    """The unique constraint uq_workflow_runs_webhook_delivery_id is present."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    constraints = _constraints("workflow_runs")
    assert "uq_workflow_runs_webhook_delivery_id" in constraints, (
        f"uq_workflow_runs_webhook_delivery_id not found in {list(constraints)}"
    )
    # 'u' = unique constraint in pg_constraint.contype
    assert constraints["uq_workflow_runs_webhook_delivery_id"] == "u", (
        "constraint is not of type UNIQUE"
    )

    _truncate_runs()


def test_s14_unique_constraint_blocks_duplicate(alembic_cfg: Config) -> None:
    """Two rows with the same delivery_id raises IntegrityError."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        delivery_id = f"test-delivery-{uuid.uuid4().hex}"
        _insert_run(session, wf_id=wf_id, team_id=team_id, delivery_id=delivery_id)
        session.commit()

        with pytest.raises(IntegrityError):
            _insert_run(
                session, wf_id=wf_id, team_id=team_id, delivery_id=delivery_id
            )
            session.commit()
        session.rollback()

    _truncate_runs()


def test_s14_null_values_coexist(alembic_cfg: Config) -> None:
    """Multiple NULL webhook_delivery_id values coexist (PostgreSQL NULL semantics)."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="null")
        session.commit()
        wf_id = _make_workflow(session, team_id=team_id)
        session.commit()

        # Insert three rows with NULL delivery_id — should all succeed.
        for _ in range(3):
            _insert_run(session, wf_id=wf_id, team_id=team_id, delivery_id=None)
        session.commit()

        count = session.execute(
            text(
                "SELECT COUNT(*) FROM workflow_runs "
                "WHERE webhook_delivery_id IS NULL AND workflow_id = :wf"
            ),
            {"wf": wf_id},
        ).scalar_one()
        assert count == 3, f"Expected 3 NULL rows, got {count}"

    _truncate_runs()


def test_s14_downgrade_removes_column(alembic_cfg: Config) -> None:
    """Downgrade to s13 removes webhook_delivery_id and its unique constraint."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    command.downgrade(alembic_cfg, S13_REV)

    cols = _columns("workflow_runs")
    assert "webhook_delivery_id" not in cols, (
        "webhook_delivery_id should be removed after downgrade"
    )
    constraints = _constraints("workflow_runs")
    assert "uq_workflow_runs_webhook_delivery_id" not in constraints, (
        "unique constraint should be removed after downgrade"
    )
    # autouse fixture restores head.


def test_s14_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate_runs()

    before = {
        "cols": _columns("workflow_runs"),
        "constraints": _constraints("workflow_runs"),
    }
    assert "webhook_delivery_id" in before["cols"], (
        "precondition: webhook_delivery_id should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S13_REV)
    command.upgrade(alembic_cfg, S14_REV)

    after = {
        "cols": _columns("workflow_runs"),
        "constraints": _constraints("workflow_runs"),
    }
    assert after == before, (
        f"Schema diverged after downgrade+re-upgrade:\nbefore={before}\nafter={after}"
    )
