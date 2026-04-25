"""Integration tests for the S04 workspace_volume Alembic migration.

Exercises `s03_team_invites` ⇄ `s04_workspace_volume` up/down on the real
Postgres test DB:

  1. After upgrade (head), assert `workspace_volume` has all expected columns,
     the named (user_id, team_id) unique constraint exists, both lookup
     indexes exist, and FK enforcement rejects bad user_id / team_id inserts.
  2. After downgrade to S03, assert the `workspace_volume` table and both
     named indexes are gone.
  3. After upgrade, assert duplicate (user_id, team_id) inserts and duplicate
     img_path inserts both raise IntegrityError.

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

S03_REV = "s03_team_invites"
S04_REV = "s04_workspace_volume"
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
            f"Could not restore DB to head after S04 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


def _workspace_volume_columns() -> dict[str, dict[str, str]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_name = 'workspace_volume'
                """
            )
        ).all()
    return {
        row[0]: {"is_nullable": row[1], "data_type": row[2]} for row in rows
    }


def _workspace_volume_indexes() -> set[str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'workspace_volume'
                """
            )
        ).all()
    return {row[0] for row in rows}


def _workspace_volume_constraints() -> set[str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT conname FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                WHERE pg_class.relname = 'workspace_volume'
                """
            )
        ).all()
    return {row[0] for row in rows}


def _truncate_workspace_volume_and_teams() -> None:
    """Clear volume/team rows so raw inserts don't collide with prior state."""
    with Session(engine) as session:
        session.execute(text("DELETE FROM workspace_volume"))
        session.execute(text("DELETE FROM team_invite"))
        session.execute(text("DELETE FROM team_member"))
        session.execute(text('DELETE FROM "team"'))
        session.commit()


def _seed_team_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one team + return its id alongside an existing user id."""
    team_id = uuid.uuid4()
    with Session(engine) as session:
        user_id = session.execute(
            text('SELECT id FROM "user" LIMIT 1')
        ).scalar()
        if user_id is None:
            pytest.skip("Seeded superuser missing — cannot run FK-backed S04 tests")
        session.execute(
            text(
                """
                INSERT INTO "team" (id, name, slug, is_personal, created_at)
                VALUES (:id, 'S04 Test Team', :slug, FALSE, NOW())
                """
            ),
            {"id": team_id, "slug": f"s04-test-{team_id.hex[:8]}"},
        )
        session.commit()
    return team_id, user_id


def test_s04_upgrade_creates_workspace_volume(alembic_cfg: Config) -> None:
    _truncate_workspace_volume_and_teams()

    # Ensure we're at head (S04) for this test.
    command.upgrade(alembic_cfg, "head")

    cols = _workspace_volume_columns()
    expected_non_null = {"id", "user_id", "team_id", "size_gb", "img_path"}
    expected_nullable = {"created_at"}

    for name in expected_non_null:
        assert name in cols, (
            f"expected column {name} on workspace_volume after upgrade"
        )
        assert cols[name]["is_nullable"] == "NO", (
            f"column {name} should be NOT NULL after S04 upgrade, got "
            f"{cols[name]['is_nullable']}"
        )

    for name in expected_nullable:
        assert name in cols, (
            f"expected column {name} on workspace_volume after upgrade"
        )
        assert cols[name]["is_nullable"] == "YES", (
            f"column {name} should be NULLable, got {cols[name]['is_nullable']}"
        )

    # Type sanity-checks: img_path is a 512-char varchar, size_gb is integer.
    assert cols["size_gb"]["data_type"] == "integer"
    assert cols["img_path"]["data_type"] in {
        "character varying",
        "varchar",
    }

    indexes = _workspace_volume_indexes()
    assert "ix_workspace_volume_user_id" in indexes, (
        f"expected ix_workspace_volume_user_id, found {indexes}"
    )
    assert "ix_workspace_volume_team_id" in indexes, (
        f"expected ix_workspace_volume_team_id, found {indexes}"
    )

    constraints = _workspace_volume_constraints()
    assert "uq_workspace_volume_user_team" in constraints, (
        f"expected uq_workspace_volume_user_team, found {constraints}"
    )

    # FK enforcement: insert with bogus user_id must fail.
    team_id, _user_id = _seed_team_and_user()
    bogus_user_id = uuid.uuid4()
    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workspace_volume
                      (id, user_id, team_id, size_gb, img_path, created_at)
                    VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": bogus_user_id,
                    "team_id": team_id,
                    "size_gb": 4,
                    "img_path": f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img",
                },
            )
            session.commit()
        session.rollback()

    # FK enforcement: insert with bogus team_id must fail.
    _team_id, user_id = _seed_team_and_user()
    bogus_team_id = uuid.uuid4()
    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workspace_volume
                      (id, user_id, team_id, size_gb, img_path, created_at)
                    VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "team_id": bogus_team_id,
                    "size_gb": 4,
                    "img_path": f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img",
                },
            )
            session.commit()
        session.rollback()

    _truncate_workspace_volume_and_teams()


def test_s04_downgrade_drops_workspace_volume(alembic_cfg: Config) -> None:
    _truncate_workspace_volume_and_teams()

    command.downgrade(alembic_cfg, S03_REV)

    # Table must be gone.
    cols = _workspace_volume_columns()
    assert cols == {}, (
        f"workspace_volume columns should be empty after downgrade, got {cols}"
    )

    # Both named lookup indexes must also be gone.
    indexes = _workspace_volume_indexes()
    assert indexes == set(), (
        f"workspace_volume indexes should be empty after downgrade, got {indexes}"
    )

    # autouse fixture restores head.


def test_s04_duplicate_user_team_fails_integrity(alembic_cfg: Config) -> None:
    _truncate_workspace_volume_and_teams()

    command.upgrade(alembic_cfg, "head")

    team_id, user_id = _seed_team_and_user()

    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO workspace_volume
                  (id, user_id, team_id, size_gb, img_path, created_at)
                VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                """
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "team_id": team_id,
                "size_gb": 4,
                "img_path": f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img",
            },
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workspace_volume
                      (id, user_id, team_id, size_gb, img_path, created_at)
                    VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "team_id": team_id,
                    "size_gb": 8,
                    "img_path": f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img",
                },
            )
            session.commit()
        session.rollback()

    _truncate_workspace_volume_and_teams()


def test_s04_duplicate_img_path_fails_integrity(alembic_cfg: Config) -> None:
    _truncate_workspace_volume_and_teams()

    command.upgrade(alembic_cfg, "head")

    team_id_a, user_id = _seed_team_and_user()
    team_id_b, _user_id = _seed_team_and_user()
    shared_img_path = f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img"

    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO workspace_volume
                  (id, user_id, team_id, size_gb, img_path, created_at)
                VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                """
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "team_id": team_id_a,
                "size_gb": 4,
                "img_path": shared_img_path,
            },
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workspace_volume
                      (id, user_id, team_id, size_gb, img_path, created_at)
                    VALUES (:id, :user_id, :team_id, :size_gb, :img_path, NOW())
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "team_id": team_id_b,
                    "size_gb": 4,
                    "img_path": shared_img_path,
                },
            )
            session.commit()
        session.rollback()

    _truncate_workspace_volume_and_teams()
