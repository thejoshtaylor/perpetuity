"""Integration tests for the S06c team_mirror_volumes Alembic migration.

Exercises ``s06b_github_app_installations`` ⇄ ``s06c_team_mirror_volumes``
on the real Postgres test DB:

  1. After upgrade (head), assert ``team_mirror_volumes`` exists with the
     expected columns/types/nullability, the PK is on ``id``, the UNIQUE
     constraints are on ``team_id`` and ``volume_path``, the FK on
     ``team_id`` cascades on parent delete, and ``always_on`` defaults
     to FALSE.
  2. Inserting a second row for the same ``team_id`` MUST raise
     ``IntegrityError`` (UNIQUE).
  3. Deleting the parent team MUST cascade-delete the mirror row.
  4. Insert without specifying ``always_on`` must land FALSE (server default).
  5. After downgrade to ``s06b_github_app_installations``, the table is gone.
  6. Downgrade then re-upgrade must leave the schema byte-identical.

Uses the MEM016 autouse fixture pattern (commit+close autouse session,
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

S06B_REV = "s06b_github_app_installations"
S06C_REV = "s06c_team_mirror_volumes"
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
    """Release session + engine pool before alembic runs (see MEM014/MEM016)."""
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
            f"Could not restore DB to head after S06c migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DB introspection helpers
# ---------------------------------------------------------------------------

TABLE = "team_mirror_volumes"


def _columns() -> dict[str, dict[str, str | int | None]]:
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
            {"t": TABLE},
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


def _constraints() -> dict[str, str]:
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
            {"t": TABLE},
        ).all()
    return {row[0]: row[1] for row in rows}


def _fk_actions() -> dict[str, str]:
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
            {"t": TABLE},
        ).all()
    return {row[0]: row[1] for row in rows}


def _schema_snapshot() -> dict[str, object]:
    return {
        "columns": _columns(),
        "constraints": _constraints(),
        "fks": _fk_actions(),
    }


def _make_team(session: Session, *, slug_suffix: str) -> uuid.UUID:
    """Insert a real team row so FK inserts can attach to it."""
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
        session.execute(text(f"DELETE FROM {TABLE}"))
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s06c_upgrade_creates_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns()
    assert set(cols) == {
        "id",
        "team_id",
        "volume_path",
        "container_id",
        "last_started_at",
        "last_idle_at",
        "always_on",
        "created_at",
    }, f"unexpected column set: {sorted(cols)}"

    # Nullability.
    assert cols["id"]["is_nullable"] == "NO"
    assert cols["team_id"]["is_nullable"] == "NO"
    assert cols["volume_path"]["is_nullable"] == "NO"
    assert cols["always_on"]["is_nullable"] == "NO"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["container_id"]["is_nullable"] == "YES"
    assert cols["last_started_at"]["is_nullable"] == "YES"
    assert cols["last_idle_at"]["is_nullable"] == "YES"

    # Types + bounded lengths.
    assert cols["id"]["data_type"] == "uuid"
    assert cols["team_id"]["data_type"] == "uuid"
    assert cols["volume_path"]["data_type"] in {"character varying", "varchar"}
    assert cols["volume_path"]["char_max_length"] == 512
    assert cols["container_id"]["data_type"] in {"character varying", "varchar"}
    assert cols["container_id"]["char_max_length"] == 64
    assert cols["last_started_at"]["data_type"] == "timestamp with time zone"
    assert cols["last_idle_at"]["data_type"] == "timestamp with time zone"
    assert cols["always_on"]["data_type"] == "boolean"
    assert cols["created_at"]["data_type"] == "timestamp with time zone"

    constraints = _constraints()
    pk = [n for n, t in constraints.items() if t == "p"]
    uq = [n for n, t in constraints.items() if t == "u"]
    fk = [n for n, t in constraints.items() if t == "f"]
    assert len(pk) == 1, f"expected one PK, got {pk}"
    assert "uq_team_mirror_volumes_team_id" in uq
    assert "uq_team_mirror_volumes_volume_path" in uq
    assert "fk_team_mirror_volumes_team_id" in fk

    # FK on team_id must CASCADE on parent delete (confdeltype='c').
    fks = _fk_actions()
    assert fks["fk_team_mirror_volumes_team_id"] == "c", (
        f"team FK should ON DELETE CASCADE, got {fks}"
    )


def test_s06c_duplicate_team_id_fails_unique(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (id, team_id, volume_path, always_on)
                VALUES (:id, :team, :vp, FALSE)
                """
            ),
            {
                "id": uuid.uuid4(),
                "team": team_id,
                "vp": f"/var/lib/perpetuity/team-mirrors/{uuid.uuid4()}",
            },
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (id, team_id, volume_path, always_on)
                    VALUES (:id, :team, :vp, FALSE)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "team": team_id,
                    "vp": f"/var/lib/perpetuity/team-mirrors/{uuid.uuid4()}",
                },
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06c_team_delete_cascades(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        session.commit()

        row_pk = uuid.uuid4()
        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (id, team_id, volume_path, always_on)
                VALUES (:id, :team, :vp, FALSE)
                """
            ),
            {
                "id": row_pk,
                "team": team_id,
                "vp": f"/var/lib/perpetuity/team-mirrors/{uuid.uuid4()}",
            },
        )
        session.commit()

        before = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE id = :id"),
            {"id": row_pk},
        ).scalar_one()
        assert before == 1

        session.execute(text("DELETE FROM team WHERE id = :id"), {"id": team_id})
        session.commit()

        after = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE id = :id"),
            {"id": row_pk},
        ).scalar_one()
        assert after == 0, "mirror row should cascade-delete with parent team"

    _truncate()


def test_s06c_always_on_defaults_false_on_insert(alembic_cfg: Config) -> None:
    """Insert without always_on must land FALSE via the server default."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="default")
        session.commit()

        row_pk = uuid.uuid4()
        # Note: omit always_on entirely so the server default fires.
        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (id, team_id, volume_path)
                VALUES (:id, :team, :vp)
                """
            ),
            {
                "id": row_pk,
                "team": team_id,
                "vp": f"/var/lib/perpetuity/team-mirrors/{uuid.uuid4()}",
            },
        )
        session.commit()

        always_on = session.execute(
            text(f"SELECT always_on FROM {TABLE} WHERE id = :id"),
            {"id": row_pk},
        ).scalar_one()
        assert always_on is False, (
            f"always_on should default to FALSE, got {always_on}"
        )

    _truncate()


def test_s06c_downgrade_drops_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S06B_REV)

    cols = _columns()
    assert cols == {}, f"{TABLE} columns should be empty after downgrade, got {cols}"
    constraints = _constraints()
    assert constraints == {}, (
        f"{TABLE} constraints should be empty after downgrade, got {constraints}"
    )
    # autouse fixture restores head.


def test_s06c_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["columns"], "precondition: table should exist before round-trip"

    command.downgrade(alembic_cfg, S06B_REV)
    command.upgrade(alembic_cfg, S06C_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
