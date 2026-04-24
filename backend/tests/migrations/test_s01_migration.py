"""Integration tests for the S01 Alembic migration.

Exercises the full `fe56fa70289e` ⇄ `s01_auth_and_roles` up/down cycle against
the real Postgres test DB:

  1. Downgrade to the pre-S01 revision.
  2. Seed one `is_superuser=True` and one `is_superuser=False` user via raw SQL
     (so the seed does NOT depend on the post-S01 model, which is what we're
     testing the migration against).
  3. Upgrade to head.
  4. Assert the data migration mapped is_superuser -> role correctly.
  5. Assert userrole/teamrole enum types exist.
  6. Assert is_superuser column is gone, team + team_member tables exist.
  7. Downgrade one step and assert is_superuser came back + role is gone.
  8. Leave the DB on `head` so the rest of the suite keeps working (the
     autouse session-scoped `db` fixture expects head schema).

This test mutates the schema of the same DB the rest of the tests use, so we
run it in a module-scoped fixture that restores head at teardown. We guard
against partial failures by always calling `upgrade head` in a finally block.
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

PRE_S01_REV = "fe56fa70289e"
S01_REV = "s01_auth_and_roles"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend


def _alembic_config() -> Config:
    """Build an Alembic Config object pointing at the checked-in alembic.ini.

    We use the real config rather than hand-rolling env.py so the test
    faithfully exercises the same migration path as production.
    """
    ini = BACKEND_ROOT / "alembic.ini"
    if not ini.exists():
        pytest.skip(f"alembic.ini not found at {ini}; cannot bootstrap alembic")
    cfg = Config(str(ini))
    # Alembic's script_location in the ini is relative to the backend root.
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "app" / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def alembic_cfg() -> Config:
    return _alembic_config()


@pytest.fixture(autouse=True)
def _restore_head_after(alembic_cfg: Config) -> Generator[None, None, None]:
    """Ensure every test in this module leaves the DB on head.

    The rest of the suite assumes head schema. Even if a test raises, we force
    the DB back to head so the next test / module doesn't see a downgraded DB.
    """
    yield
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as restore_err:  # pragma: no cover - defensive
        pytest.fail(
            f"Could not restore DB to head after migration test: {restore_err}"
        )


def _truncate_user_table() -> None:
    """Remove any rows that might block the down-migration path.

    After downgrade, the only rows present are those we inserted via raw SQL.
    We wipe first so earlier tests' init_db superuser doesn't interfere with
    the row-count assertions.
    """
    with Session(engine) as session:
        # Delete dependent rows first — team_member FKs into user.
        session.execute(text("DELETE FROM item"))
        session.execute(text("DELETE FROM team_member"))
        session.execute(text("DELETE FROM \"user\""))
        session.commit()


def test_s01_upgrade_maps_is_superuser_to_role(alembic_cfg: Config) -> None:
    _truncate_user_table()

    # 1. Downgrade to pre-S01.
    command.downgrade(alembic_cfg, PRE_S01_REV)

    # 2. Seed one admin and one normal user via raw SQL against the pre-S01 schema.
    admin_id = uuid.uuid4()
    normal_id = uuid.uuid4()
    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO "user" (id, email, is_active, is_superuser,
                                    full_name, hashed_password, created_at)
                VALUES (:id, :email, TRUE, TRUE, 'Admin', 'x', NOW())
                """
            ),
            {"id": admin_id, "email": f"admin_{admin_id.hex}@example.com"},
        )
        session.execute(
            text(
                """
                INSERT INTO "user" (id, email, is_active, is_superuser,
                                    full_name, hashed_password, created_at)
                VALUES (:id, :email, TRUE, FALSE, 'Normal', 'x', NOW())
                """
            ),
            {"id": normal_id, "email": f"normal_{normal_id.hex}@example.com"},
        )
        session.commit()

    # 3. Upgrade to head (runs S01 upgrade with our two seed rows).
    command.upgrade(alembic_cfg, "head")

    # 4. Role mapping is correct.
    with Session(engine) as session:
        rows = session.execute(
            text(
                "SELECT id, role::text FROM \"user\" WHERE id IN (:a, :n)"
            ),
            {"a": admin_id, "n": normal_id},
        ).all()
        role_by_id = {row[0]: row[1] for row in rows}
        assert role_by_id[admin_id] == "system_admin"
        assert role_by_id[normal_id] == "user"

        # 5. Enum types exist (lowercase in pg_type — MEM012).
        enum_names = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT typname FROM pg_type "
                    "WHERE typname IN ('userrole', 'teamrole')"
                )
            ).all()
        }
        assert enum_names == {"userrole", "teamrole"}

        # 6. is_superuser column is gone, team + team_member exist.
        user_cols = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user'"
                )
            ).all()
        }
        assert "is_superuser" not in user_cols
        assert "role" in user_cols

        tables = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('team', 'team_member')"
                )
            ).all()
        }
        assert tables == {"team", "team_member"}


def test_s01_downgrade_restores_is_superuser(alembic_cfg: Config) -> None:
    _truncate_user_table()

    # Seed two users via raw SQL while we're on head (post-S01 schema).
    admin_id = uuid.uuid4()
    normal_id = uuid.uuid4()
    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO "user" (id, email, is_active, role,
                                    full_name, hashed_password, created_at)
                VALUES (:id, :email, TRUE, 'system_admin'::userrole,
                        'Admin', 'x', NOW())
                """
            ),
            {"id": admin_id, "email": f"dgadmin_{admin_id.hex}@example.com"},
        )
        session.execute(
            text(
                """
                INSERT INTO "user" (id, email, is_active, role,
                                    full_name, hashed_password, created_at)
                VALUES (:id, :email, TRUE, 'user'::userrole,
                        'Normal', 'x', NOW())
                """
            ),
            {"id": normal_id, "email": f"dgnormal_{normal_id.hex}@example.com"},
        )
        session.commit()

    # Downgrade one step back to pre-S01.
    command.downgrade(alembic_cfg, PRE_S01_REV)

    # is_superuser column should exist again, role should be gone.
    with Session(engine) as session:
        user_cols = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user'"
                )
            ).all()
        }
        assert "is_superuser" in user_cols
        assert "role" not in user_cols

        # Data mapping: system_admin → TRUE, user → FALSE.
        rows = session.execute(
            text(
                'SELECT id, is_superuser FROM "user" WHERE id IN (:a, :n)'
            ),
            {"a": admin_id, "n": normal_id},
        ).all()
        flag_by_id = {row[0]: row[1] for row in rows}
        assert flag_by_id[admin_id] is True
        assert flag_by_id[normal_id] is False

        # Enum types should have been dropped.
        enum_names = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT typname FROM pg_type "
                    "WHERE typname IN ('userrole', 'teamrole')"
                )
            ).all()
        }
        assert enum_names == set()

        # team / team_member tables should be gone.
        tables = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('team', 'team_member')"
                )
            ).all()
        }
        assert tables == set()

    # autouse fixture will restore head.
