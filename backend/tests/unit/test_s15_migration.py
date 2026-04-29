"""Unit tests for the S15 workflow_operational_caps migration.

Exercises ``s14_webhook_delivery_id`` ⇄ ``s15_workflow_operational_caps``
against the real Postgres test DB.

Coverage:
  1. max_concurrent_runs column added — INTEGER NULLABLE on ``workflows``.
  2. max_runs_per_hour column added — INTEGER NULLABLE on ``workflows``.
  3. Composite index ``ix_workflow_runs_workflow_id_status_created_at``
     exists after upgrade.
  4. NULL values and integer values round-trip correctly on both columns.
  5. Downgrade removes both columns and the composite index cleanly.
  6. Downgrade + re-upgrade leaves the schema identical (round-trip).

Uses the MEM016 autouse fixture pattern to avoid AccessShareLock deadlocks.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine

S14_REV = "s14_webhook_delivery_id"
S15_REV = "s15_workflow_operational_caps"
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
            f"Could not restore DB to head after S15 migration test: {restore_err}"
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


def _indexes(table: str) -> set[str]:
    with Session(engine) as s:
        rows = s.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = :t
                """
            ),
            {"t": table},
        ).all()
    return {r[0] for r in rows}


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


def _make_workflow(
    session: Session,
    *,
    team_id: uuid.UUID,
    max_concurrent_runs: int | None = None,
    max_runs_per_hour: int | None = None,
) -> uuid.UUID:
    wf_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO workflows "
            "(id, team_id, name, max_concurrent_runs, max_runs_per_hour) "
            "VALUES (:id, :team, :name, :mcr, :mrph)"
        ),
        {
            "id": wf_id,
            "team": team_id,
            "name": f"wf-{uuid.uuid4().hex[:8]}",
            "mcr": max_concurrent_runs,
            "mrph": max_runs_per_hour,
        },
    )
    return wf_id


def _truncate_workflows() -> None:
    with Session(engine) as s:
        s.execute(text("DELETE FROM step_runs"))
        s.execute(text("DELETE FROM workflow_runs"))
        s.execute(text("DELETE FROM workflow_steps"))
        s.execute(text("DELETE FROM workflows"))
        s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s15_max_concurrent_runs_column_added(alembic_cfg: Config) -> None:
    """After upgrade, max_concurrent_runs is INTEGER NULLABLE on workflows."""
    command.upgrade(alembic_cfg, "head")
    _truncate_workflows()

    cols = _columns("workflows")
    assert "max_concurrent_runs" in cols, (
        "max_concurrent_runs column not found in workflows"
    )
    col = cols["max_concurrent_runs"]
    assert col["is_nullable"] == "YES", (
        f"max_concurrent_runs should be NULLABLE, got {col['is_nullable']}"
    )
    assert col["data_type"] == "integer", (
        f"Expected integer, got {col['data_type']}"
    )

    _truncate_workflows()


def test_s15_max_runs_per_hour_column_added(alembic_cfg: Config) -> None:
    """After upgrade, max_runs_per_hour is INTEGER NULLABLE on workflows."""
    command.upgrade(alembic_cfg, "head")
    _truncate_workflows()

    cols = _columns("workflows")
    assert "max_runs_per_hour" in cols, (
        "max_runs_per_hour column not found in workflows"
    )
    col = cols["max_runs_per_hour"]
    assert col["is_nullable"] == "YES", (
        f"max_runs_per_hour should be NULLABLE, got {col['is_nullable']}"
    )
    assert col["data_type"] == "integer", (
        f"Expected integer, got {col['data_type']}"
    )

    _truncate_workflows()


def test_s15_composite_index_on_workflow_runs(alembic_cfg: Config) -> None:
    """Composite index ix_workflow_runs_workflow_id_status_created_at exists."""
    command.upgrade(alembic_cfg, "head")

    idx = _indexes("workflow_runs")
    assert "ix_workflow_runs_workflow_id_status_created_at" in idx, (
        f"Expected composite index not found; found: {idx}"
    )


def test_s15_cap_values_round_trip(alembic_cfg: Config) -> None:
    """Integer cap values and NULL both round-trip correctly."""
    command.upgrade(alembic_cfg, "head")
    _truncate_workflows()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="caps")
        session.commit()

        # Workflow with both caps set
        wf_id = _make_workflow(
            session, team_id=team_id,
            max_concurrent_runs=2, max_runs_per_hour=10,
        )
        session.commit()

        row = session.execute(
            text(
                "SELECT max_concurrent_runs, max_runs_per_hour "
                "FROM workflows WHERE id = :id"
            ),
            {"id": wf_id},
        ).one()
        assert row[0] == 2, f"max_concurrent_runs expected 2, got {row[0]}"
        assert row[1] == 10, f"max_runs_per_hour expected 10, got {row[1]}"

        # Workflow with both caps NULL
        wf_null_id = _make_workflow(session, team_id=team_id)
        session.commit()

        null_row = session.execute(
            text(
                "SELECT max_concurrent_runs, max_runs_per_hour "
                "FROM workflows WHERE id = :id"
            ),
            {"id": wf_null_id},
        ).one()
        assert null_row[0] is None, f"max_concurrent_runs expected None, got {null_row[0]}"
        assert null_row[1] is None, f"max_runs_per_hour expected None, got {null_row[1]}"

    _truncate_workflows()


def test_s15_downgrade_removes_columns_and_index(alembic_cfg: Config) -> None:
    """Downgrade to s14 removes both columns and the composite index."""
    command.upgrade(alembic_cfg, "head")
    _truncate_workflows()

    command.downgrade(alembic_cfg, S14_REV)

    cols = _columns("workflows")
    assert "max_concurrent_runs" not in cols, (
        "max_concurrent_runs should be removed after downgrade"
    )
    assert "max_runs_per_hour" not in cols, (
        "max_runs_per_hour should be removed after downgrade"
    )

    idx = _indexes("workflow_runs")
    assert "ix_workflow_runs_workflow_id_status_created_at" not in idx, (
        "composite index should be removed after downgrade"
    )
    # autouse fixture restores head.


def test_s15_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate_workflows()

    before_wf = _columns("workflows")
    before_idx = _indexes("workflow_runs")

    assert "max_concurrent_runs" in before_wf, (
        "precondition: max_concurrent_runs should exist before round-trip"
    )
    assert "ix_workflow_runs_workflow_id_status_created_at" in before_idx, (
        "precondition: composite index should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S14_REV)
    command.upgrade(alembic_cfg, S15_REV)

    after_wf = _columns("workflows")
    after_idx = _indexes("workflow_runs")

    assert after_wf == before_wf, (
        f"Workflow schema diverged:\nbefore={before_wf}\nafter={after_wf}"
    )
    assert after_idx == before_idx, (
        f"Index set diverged:\nbefore={before_idx}\nafter={after_idx}"
    )
