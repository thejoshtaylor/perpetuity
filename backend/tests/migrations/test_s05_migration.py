"""Integration tests for the S05 system_settings Alembic migration.

Exercises `s04_workspace_volume` ⇄ `s05_system_settings` up/down on the real
Postgres test DB:

  1. After upgrade (head), assert `system_settings` has all expected columns
     with the right types (key VARCHAR(255), value JSONB, updated_at
     TIMESTAMPTZ), the primary-key constraint exists on `key`, and round-trip
     insert+select preserves a JSONB payload.
  2. After downgrade to S04, assert the `system_settings` table is gone.
  3. After upgrade, assert duplicate `key` inserts raise IntegrityError (PK
     uniqueness).

Uses the MEM016 autouse fixture pattern (commit+close autouse session,
engine.dispose()) to avoid AccessShareLock deadlocks with alembic DDL.
"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.db import engine

S04_REV = "s04_workspace_volume"
S05_REV = "s05_system_settings"
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
    """Release session + engine pool before alembic runs (see MEM016)."""
    db.commit()
    db.expire_all()
    db.close()
    engine.dispose()
    yield


@pytest.fixture(autouse=True)
def _restore_head_after(
    alembic_cfg: Config, _release_autouse_db_session: None
) -> Generator[None, None, None]:
    """Ensure each test leaves the DB on head, regardless of failure."""
    yield
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as restore_err:  # pragma: no cover - defensive
        pytest.fail(
            f"Could not restore DB to head after S05 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


def _system_settings_columns() -> dict[str, dict[str, str]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_name = 'system_settings'
                """
            )
        ).all()
    return {
        row[0]: {"is_nullable": row[1], "data_type": row[2]} for row in rows
    }


def _system_settings_constraints() -> dict[str, str]:
    """Return {constraint_name: contype} for all constraints on system_settings."""
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT conname, contype FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                WHERE pg_class.relname = 'system_settings'
                """
            )
        ).all()
    return {row[0]: row[1] for row in rows}


def _truncate_system_settings() -> None:
    """Clear system_settings rows so raw inserts don't collide with prior state."""
    with Session(engine) as session:
        session.execute(text("DELETE FROM system_settings"))
        session.commit()


def test_s05_upgrade_creates_system_settings(alembic_cfg: Config) -> None:
    # Ensure we're at head (S05) for this test.
    command.upgrade(alembic_cfg, "head")
    _truncate_system_settings()

    cols = _system_settings_columns()
    expected_non_null = {"key", "value"}
    expected_nullable = {"updated_at"}

    for name in expected_non_null:
        assert name in cols, (
            f"expected column {name} on system_settings after upgrade"
        )
        assert cols[name]["is_nullable"] == "NO", (
            f"column {name} should be NOT NULL after S05 upgrade, got "
            f"{cols[name]['is_nullable']}"
        )

    for name in expected_nullable:
        assert name in cols, (
            f"expected column {name} on system_settings after upgrade"
        )
        assert cols[name]["is_nullable"] == "YES", (
            f"column {name} should be NULLable, got {cols[name]['is_nullable']}"
        )

    # Type sanity-checks.
    assert cols["key"]["data_type"] in {"character varying", "varchar"}
    assert cols["value"]["data_type"] == "jsonb"
    assert cols["updated_at"]["data_type"] == "timestamp with time zone"

    # Primary key constraint must exist on `key`.
    constraints = _system_settings_constraints()
    pk_constraints = [name for name, ctype in constraints.items() if ctype == "p"]
    assert len(pk_constraints) == 1, (
        f"expected exactly one primary key on system_settings, found {constraints}"
    )

    # Round-trip a JSONB payload to prove the column type is functional.
    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (:key, CAST(:value AS jsonb), NOW())
                """
            ),
            {"key": "workspace_volume_size_gb", "value": "4"},
        )
        session.commit()

        row = session.execute(
            text("SELECT key, value FROM system_settings WHERE key = :key"),
            {"key": "workspace_volume_size_gb"},
        ).one()
        assert row[0] == "workspace_volume_size_gb"
        # JSONB scalars come back as native Python types via psycopg.
        assert row[1] == 4

    _truncate_system_settings()


def test_s05_downgrade_drops_system_settings(alembic_cfg: Config) -> None:
    _truncate_system_settings()

    command.downgrade(alembic_cfg, S04_REV)

    # Table must be gone.
    cols = _system_settings_columns()
    assert cols == {}, (
        f"system_settings columns should be empty after downgrade, got {cols}"
    )

    # No constraints should remain on the dropped table.
    constraints = _system_settings_constraints()
    assert constraints == {}, (
        f"system_settings constraints should be empty after downgrade, got {constraints}"
    )

    # autouse fixture restores head.


def test_s05_duplicate_key_fails_integrity(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate_system_settings()

    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (:key, CAST(:value AS jsonb), NOW())
                """
            ),
            {"key": "workspace_volume_size_gb", "value": "4"},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (:key, CAST(:value AS jsonb), NOW())
                    """
                ),
                {"key": "workspace_volume_size_gb", "value": "8"},
            )
            session.commit()
        session.rollback()

    _truncate_system_settings()
