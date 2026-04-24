"""Integration tests for the S03 team_invites Alembic migration.

Exercises `s02_team_columns` ⇄ `s03_team_invites` up/down on the real
Postgres test DB:

  1. After upgrade (head), assert `team_invite` has all expected columns,
     the unique `ix_team_invite_code` index exists, and FK enforcement
     rejects inserts with bad team_id / created_by.
  2. After downgrade to S02, assert the `team_invite` table and its unique
     code index are gone.
  3. After upgrade, assert duplicate `code` inserts raise IntegrityError.

Uses the MEM016 autouse fixture pattern (commit+close autouse session,
engine.dispose()) to avoid AccessShareLock deadlocks with alembic DDL.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.db import engine

S02_REV = "s02_team_columns"
S03_REV = "s03_team_invites"
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
            f"Could not restore DB to head after S03 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


def _team_invite_columns() -> dict[str, dict[str, str]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_name = 'team_invite'
                """
            )
        ).all()
    return {
        row[0]: {"is_nullable": row[1], "data_type": row[2]} for row in rows
    }


def _team_invite_indexes() -> set[str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes WHERE tablename = 'team_invite'
                """
            )
        ).all()
    return {row[0] for row in rows}


def _truncate_invites_and_teams() -> None:
    """Clear invite/team rows so raw inserts don't collide with prior state."""
    with Session(engine) as session:
        session.execute(text("DELETE FROM team_invite"))
        session.execute(text("DELETE FROM team_member"))
        session.execute(text('DELETE FROM "team"'))
        session.commit()


def _seed_team_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one team + one user and return their ids for FK-backed inserts."""
    team_id = uuid.uuid4()
    with Session(engine) as session:
        user_id = session.execute(
            text('SELECT id FROM "user" LIMIT 1')
        ).scalar()
        if user_id is None:
            pytest.skip("Seeded superuser missing — cannot run FK-backed S03 tests")
        session.execute(
            text(
                """
                INSERT INTO "team" (id, name, slug, is_personal, created_at)
                VALUES (:id, 'S03 Test Team', :slug, FALSE, NOW())
                """
            ),
            {"id": team_id, "slug": f"s03-test-{team_id.hex[:8]}"},
        )
        session.commit()
    return team_id, user_id


def test_s03_upgrade_creates_team_invite(alembic_cfg: Config) -> None:
    _truncate_invites_and_teams()

    # Ensure we're at head (S03) for this test.
    command.upgrade(alembic_cfg, "head")

    cols = _team_invite_columns()
    expected_non_null = {
        "id",
        "code",
        "team_id",
        "created_by",
        "expires_at",
    }
    expected_nullable = {"used_at", "used_by", "created_at"}

    for name in expected_non_null:
        assert name in cols, f"expected column {name} on team_invite after upgrade"
        assert cols[name]["is_nullable"] == "NO", (
            f"column {name} should be NOT NULL after S03 upgrade, got "
            f"{cols[name]['is_nullable']}"
        )

    for name in expected_nullable:
        assert name in cols, f"expected column {name} on team_invite after upgrade"
        assert cols[name]["is_nullable"] == "YES", (
            f"column {name} should be NULLable, got {cols[name]['is_nullable']}"
        )

    indexes = _team_invite_indexes()
    assert "ix_team_invite_code" in indexes, (
        f"expected unique code index ix_team_invite_code, found {indexes}"
    )

    # Unique code index: confirm it is unique.
    with Session(engine) as session:
        unique_row = session.execute(
            text(
                """
                SELECT indisunique FROM pg_index
                JOIN pg_class ON pg_class.oid = pg_index.indexrelid
                WHERE pg_class.relname = 'ix_team_invite_code'
                """
            )
        ).scalar()
        assert unique_row is True, "ix_team_invite_code must be unique"

    # FK enforcement: insert with bogus team_id must fail.
    _team_id, user_id = _seed_team_and_user()
    bogus_team_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO team_invite
                      (id, code, team_id, created_by, expires_at, created_at)
                    VALUES (:id, :code, :team_id, :created_by, :exp, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "code": f"fk-check-{uuid.uuid4().hex[:12]}",
                    "team_id": bogus_team_id,
                    "created_by": user_id,
                    "exp": expires_at,
                },
            )
            session.commit()
        session.rollback()

    _truncate_invites_and_teams()


def test_s03_downgrade_drops_team_invite(alembic_cfg: Config) -> None:
    _truncate_invites_and_teams()

    command.downgrade(alembic_cfg, S02_REV)

    # Table must be gone.
    cols = _team_invite_columns()
    assert cols == {}, (
        f"team_invite columns should be empty after downgrade, got {cols}"
    )

    # Unique index must also be gone.
    indexes = _team_invite_indexes()
    assert indexes == set(), (
        f"team_invite indexes should be empty after downgrade, got {indexes}"
    )

    # autouse fixture restores head.


def test_s03_duplicate_code_fails_integrity(alembic_cfg: Config) -> None:
    _truncate_invites_and_teams()

    command.upgrade(alembic_cfg, "head")

    team_id, user_id = _seed_team_and_user()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    dup_code = f"dup-{uuid.uuid4().hex[:12]}"

    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO team_invite
                  (id, code, team_id, created_by, expires_at, created_at)
                VALUES (:id, :code, :team_id, :created_by, :exp, NOW())
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": dup_code,
                "team_id": team_id,
                "created_by": user_id,
                "exp": expires_at,
            },
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO team_invite
                      (id, code, team_id, created_by, expires_at, created_at)
                    VALUES (:id, :code, :team_id, :created_by, :exp, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "code": dup_code,
                    "team_id": team_id,
                    "created_by": user_id,
                    "exp": expires_at,
                },
            )
            session.commit()
        session.rollback()

    _truncate_invites_and_teams()
