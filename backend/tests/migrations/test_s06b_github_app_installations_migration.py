"""Integration tests for the S06b github_app_installations Alembic migration.

Exercises ``s06_system_settings_sensitive`` ⇄ ``s06b_github_app_installations``
on the real Postgres test DB:

  1. After upgrade (head), assert ``github_app_installations`` exists with the
     expected columns/types/nullability, the PK is on ``id``, the UNIQUE
     constraint is on ``installation_id``, the FK on ``team_id`` cascades on
     parent delete, and the CHECK constraint pins ``account_type`` to
     {Organization, User}.
  2. Inserting a duplicate ``installation_id`` MUST raise ``IntegrityError``
     (UNIQUE).
  3. Inserting ``account_type='Bot'`` MUST raise ``IntegrityError``
     (CheckViolation).
  4. Deleting the parent team MUST cascade-delete the installation row.
  5. After downgrade to ``s06_system_settings_sensitive``, the table is gone.
  6. Downgrade then re-upgrade must leave the schema byte-identical (snapshot
     the relevant ``information_schema`` + ``pg_constraint`` views and assert
     equality).

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

S06_REV = "s06_system_settings_sensitive"
S06B_REV = "s06b_github_app_installations"
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
    """Release session + engine pool before alembic runs (see MEM014/MEM016).

    The session-scoped autouse ``db`` fixture in ``tests/conftest.py`` keeps a
    SQLAlchemy Session open for the whole pytest session and implicitly holds
    an AccessShareLock on the ``user`` table. Alembic's DDL statements would
    block on that lock indefinitely. Commit + expire + close + dispose gives
    alembic a fresh, lock-free pool to work with.
    """
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
            f"Could not restore DB to head after S06b migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DB introspection helpers
# ---------------------------------------------------------------------------

TABLE = "github_app_installations"


def _columns() -> dict[str, dict[str, str | int | None]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name, is_nullable, data_type,
                       character_maximum_length, numeric_precision
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
            "numeric_precision": row[4],
        }
        for row in rows
    }


def _constraints() -> dict[str, str]:
    """Return {constraint_name: contype} for all constraints on the table."""
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


def _check_clauses() -> dict[str, str]:
    """Return {check_constraint_name: check_clause} for the table."""
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT con.conname, pg_get_constraintdef(con.oid)
                FROM pg_constraint con
                JOIN pg_class cls ON cls.oid = con.conrelid
                WHERE cls.relname = :t AND con.contype = 'c'
                ORDER BY con.conname
                """
            ),
            {"t": TABLE},
        ).all()
    return {row[0]: row[1] for row in rows}


def _fk_actions() -> dict[str, str]:
    """Return {fk_name: confdeltype} (e.g. 'c' for CASCADE)."""
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
    """Capture the bits of schema we care about for the round-trip test."""
    return {
        "columns": _columns(),
        "constraints": _constraints(),
        "checks": _check_clauses(),
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
        # Don't blanket-DELETE the team table — other tests rely on the
        # init_db superuser's personal team. We only delete teams we made.
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_s06b_upgrade_creates_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns()
    assert set(cols) == {
        "id",
        "team_id",
        "installation_id",
        "account_login",
        "account_type",
        "created_at",
    }, f"unexpected column set: {sorted(cols)}"

    # Nullability — every column is NOT NULL.
    for name in cols:
        assert cols[name]["is_nullable"] == "NO", (
            f"{name} should be NOT NULL, got {cols[name]['is_nullable']}"
        )

    # Types.
    assert cols["id"]["data_type"] == "uuid"
    assert cols["team_id"]["data_type"] == "uuid"
    # BIGINT shows up as 'bigint' in information_schema; numeric_precision=64.
    assert cols["installation_id"]["data_type"] == "bigint"
    assert cols["installation_id"]["numeric_precision"] == 64
    assert cols["account_login"]["data_type"] in {"character varying", "varchar"}
    assert cols["account_login"]["char_max_length"] == 255
    assert cols["account_type"]["data_type"] in {"character varying", "varchar"}
    assert cols["account_type"]["char_max_length"] == 64
    assert cols["created_at"]["data_type"] == "timestamp with time zone"

    constraints = _constraints()
    pk = [n for n, t in constraints.items() if t == "p"]
    uq = [n for n, t in constraints.items() if t == "u"]
    fk = [n for n, t in constraints.items() if t == "f"]
    ck = [n for n, t in constraints.items() if t == "c"]
    assert len(pk) == 1, f"expected one PK, got {pk}"
    assert "uq_github_app_installations_installation_id" in uq
    assert "fk_github_app_installations_team_id" in fk
    assert "ck_github_app_installations_account_type" in ck

    # FK on team_id must CASCADE on parent delete (confdeltype='c').
    fks = _fk_actions()
    assert fks["fk_github_app_installations_team_id"] == "c", (
        f"team FK should ON DELETE CASCADE, got {fks}"
    )

    # CHECK clause must mention both allowed values.
    checks = _check_clauses()
    clause = checks["ck_github_app_installations_account_type"]
    assert "Organization" in clause and "User" in clause, (
        f"unexpected CHECK clause: {clause}"
    )


def test_s06b_duplicate_installation_id_fails_unique(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (id, team_id, installation_id, account_login, account_type)
                VALUES (:id, :team, :inst, 'acme', 'Organization')
                """
            ),
            {"id": uuid.uuid4(), "team": team_id, "inst": 12345},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (id, team_id, installation_id, account_login, account_type)
                    VALUES (:id, :team, :inst, 'acme2', 'Organization')
                    """
                ),
                {"id": uuid.uuid4(), "team": team_id, "inst": 12345},
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06b_invalid_account_type_fails_check(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="check")
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (id, team_id, installation_id, account_login, account_type)
                    VALUES (:id, :team, :inst, 'octocat', 'Bot')
                    """
                ),
                {"id": uuid.uuid4(), "team": team_id, "inst": 67890},
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06b_team_delete_cascades(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        session.commit()

        inst_pk = uuid.uuid4()
        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (id, team_id, installation_id, account_login, account_type)
                VALUES (:id, :team, :inst, 'acme', 'Organization')
                """
            ),
            {"id": inst_pk, "team": team_id, "inst": 11111},
        )
        session.commit()

        # Sanity: row exists.
        before = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE id = :id"),
            {"id": inst_pk},
        ).scalar_one()
        assert before == 1

        # Drop the parent team.
        session.execute(text("DELETE FROM team WHERE id = :id"), {"id": team_id})
        session.commit()

        after = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE id = :id"),
            {"id": inst_pk},
        ).scalar_one()
        assert after == 0, "installation row should cascade-delete with parent team"

    _truncate()


def test_s06b_downgrade_drops_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S06_REV)

    cols = _columns()
    assert cols == {}, f"{TABLE} columns should be empty after downgrade, got {cols}"
    constraints = _constraints()
    assert constraints == {}, (
        f"{TABLE} constraints should be empty after downgrade, got {constraints}"
    )
    # autouse fixture restores head.


def test_s06b_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["columns"], "precondition: table should exist before round-trip"

    command.downgrade(alembic_cfg, S06_REV)
    command.upgrade(alembic_cfg, S06B_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
