"""Integration tests for the S02 team_columns Alembic migration.

Exercises `s01_auth_and_roles` ⇄ `s02_team_columns` up/down on the real
Postgres test DB:

  1. After upgrade (head), assert name/slug/is_personal exist and are NOT NULL,
     slug has a unique index, and inserting a duplicate slug fails with
     IntegrityError.
  2. After downgrade to S01, assert the three columns are gone.
  3. Seed a `team` row via raw SQL at S01 schema, upgrade, assert the backfill
     gave the row a unique slug starting with 'legacy-' and name starting with
     'Legacy Team '.

Uses the MEM016 autouse fixture pattern (commit+close autouse session,
engine.dispose()) to avoid AccessShareLock deadlocks with alembic DDL.
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

S01_REV = "s01_auth_and_roles"
S02_REV = "s02_team_columns"
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
            f"Could not restore DB to head after S02 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


def _truncate_teams() -> None:
    """Clear team-related rows so raw inserts don't collide with prior test state."""
    with Session(engine) as session:
        session.execute(text("DELETE FROM team_member"))
        session.execute(text('DELETE FROM "team"'))
        session.commit()


def _team_columns() -> dict[str, dict[str, str]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_name = 'team'
                """
            )
        ).all()
    return {
        row[0]: {"is_nullable": row[1], "data_type": row[2]} for row in rows
    }


def test_s02_upgrade_adds_columns_not_null_and_unique_slug(
    alembic_cfg: Config,
) -> None:
    _truncate_teams()

    # Ensure we're at head (S02) for this test.
    command.upgrade(alembic_cfg, "head")

    cols = _team_columns()
    for name in ("name", "slug", "is_personal"):
        assert name in cols, f"expected column {name} on team after S02 upgrade"
        assert cols[name]["is_nullable"] == "NO", (
            f"column {name} should be NOT NULL after S02 upgrade, got "
            f"{cols[name]['is_nullable']}"
        )

    # Unique index on slug must exist.
    with Session(engine) as session:
        indexes = {
            r[0]
            for r in session.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'team' AND indexname = 'ix_team_slug'
                    """
                )
            ).all()
        }
        assert indexes == {"ix_team_slug"}

        # Insert a row, then an identical slug — second insert must fail.
        t1_id = uuid.uuid4()
        t2_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO "team" (id, name, slug, is_personal, created_at)
                VALUES (:id, 'One', 'dup-slug', FALSE, NOW())
                """
            ),
            {"id": t1_id},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO "team" (id, name, slug, is_personal, created_at)
                    VALUES (:id, 'Two', 'dup-slug', FALSE, NOW())
                    """
                ),
                {"id": t2_id},
            )
            session.commit()
        session.rollback()

    _truncate_teams()


def test_s02_downgrade_drops_columns(alembic_cfg: Config) -> None:
    _truncate_teams()

    # Downgrade one step back to S01.
    command.downgrade(alembic_cfg, S01_REV)

    cols = _team_columns()
    for name in ("name", "slug", "is_personal"):
        assert name not in cols, (
            f"column {name} should be gone after S02 downgrade, found {cols.get(name)}"
        )

    # Unique index should be gone too.
    with Session(engine) as session:
        indexes = {
            r[0]
            for r in session.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'team' AND indexname = 'ix_team_slug'
                    """
                )
            ).all()
        }
        assert indexes == set()

    # autouse fixture restores head.


def test_s02_backfills_preexisting_row_with_legacy_name_and_slug(
    alembic_cfg: Config,
) -> None:
    _truncate_teams()

    # Go back to S01 schema where team has only id + created_at.
    command.downgrade(alembic_cfg, S01_REV)

    seeded_id = uuid.uuid4()
    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO "team" (id, created_at)
                VALUES (:id, NOW())
                """
            ),
            {"id": seeded_id},
        )
        session.commit()

    # Upgrade to S02; backfill should fill in name/slug/is_personal.
    command.upgrade(alembic_cfg, "head")

    with Session(engine) as session:
        row = session.execute(
            text(
                """
                SELECT name, slug, is_personal
                FROM "team" WHERE id = :id
                """
            ),
            {"id": seeded_id},
        ).one()
        name, slug, is_personal = row[0], row[1], row[2]
        expected_stem = seeded_id.hex[:8]
        assert name == f"Legacy Team {expected_stem}"
        assert slug == f"legacy-{expected_stem}"
        assert is_personal is False

    _truncate_teams()
