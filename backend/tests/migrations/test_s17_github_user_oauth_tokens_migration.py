"""Integration tests for the S17 github_user_oauth_tokens Alembic migration.

Exercises ``s16_workflow_run_rejected_status`` ⇄ ``s17_github_user_oauth_tokens``
against the real Postgres test DB:

  1. After upgrade (head), assert ``github_user_oauth_tokens`` exists with
     the expected columns + types + nullability, the PK on user_id, and
     the user FK CASCADE.
  2. Inserting a duplicate user_id fails with IntegrityError — confirms
     the PK holds at the storage layer.
  3. server-defaults for created_at and updated_at land non-NULL on
     insert when the columns are omitted.
  4. Deleting a user CASCADEs to their token row.
  5. Downgrade to ``s16_workflow_run_rejected_status`` drops the table
     cleanly.
  6. Downgrade then re-upgrade leaves the schema byte-identical.
  7. The FK action is ON DELETE CASCADE (confdeltype='c').

Uses the MEM016 autouse fixture pattern (commit + close autouse session,
``engine.dispose()``) to avoid AccessShareLock deadlocks with alembic DDL.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.db import engine

S16_REV = "s16_workflow_run_rejected_status"
S17_REV = "s17_github_user_oauth_tokens"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend

TABLE = "github_user_oauth_tokens"

# Fernet-shaped opaque bytes used as fake ciphertext. The migration
# does NOT decrypt — it only stores BYTEA — so any non-empty bytes
# satisfy the NOT NULL constraint here.
_FAKE_CIPHERTEXT = b"gAAAAAB-fake-not-real-fernet-token-padding"


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
            f"Could not restore DB to head after S17 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DB introspection helpers
# ---------------------------------------------------------------------------


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


def _pk_columns() -> list[str]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT a.attname
                FROM pg_constraint con
                JOIN pg_class cls ON cls.oid = con.conrelid
                JOIN pg_attribute a
                  ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
                WHERE cls.relname = :t AND con.contype = 'p'
                ORDER BY array_position(con.conkey, a.attnum)
                """
            ),
            {"t": TABLE},
        ).all()
    return [row[0] for row in rows]


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
        "pk_columns": _pk_columns(),
    }


def _make_user(session: Session, *, email_suffix: str) -> uuid.UUID:
    """Insert a real user row so FK inserts can attach to it."""
    user_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO "user" (id, email, hashed_password, is_active, full_name, role)
            VALUES (:id, :email, :pw, TRUE, :name, 'user')
            """
        ),
        {
            "id": user_id,
            "email": f"test-s17-{email_suffix}-{uuid.uuid4().hex[:8]}@example.com",
            "pw": "hashed-pw-not-real",
            "name": f"Test S17 {email_suffix}",
        },
    )
    return user_id


def _truncate() -> None:
    with Session(engine) as session:
        session.execute(text(f"DELETE FROM {TABLE}"))
        session.commit()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s17_upgrade_creates_table_with_correct_columns(alembic_cfg: Config) -> None:
    """Table exists with 10 columns of the correct types and nullability."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns()
    assert set(cols) == {
        "user_id",
        "installation_id",
        "github_user_id",
        "access_token_encrypted",
        "refresh_token_encrypted",
        "access_token_expires_at",
        "refresh_token_expires_at",
        "scope",
        "created_at",
        "updated_at",
    }, f"unexpected column set: {sorted(cols)}"

    # Every column is NOT NULL.
    for col_name in cols:
        assert cols[col_name]["is_nullable"] == "NO", (
            f"column {col_name!r} should be NOT NULL"
        )

    # Types.
    assert cols["user_id"]["data_type"] == "uuid"
    assert cols["installation_id"]["data_type"] in {"bigint", "int8"}
    assert cols["github_user_id"]["data_type"] in {"bigint", "int8"}
    assert cols["access_token_encrypted"]["data_type"] == "bytea"
    assert cols["refresh_token_encrypted"]["data_type"] == "bytea"
    assert cols["access_token_expires_at"]["data_type"] == "timestamp with time zone"
    assert cols["refresh_token_expires_at"]["data_type"] == "timestamp with time zone"
    assert cols["scope"]["data_type"] in {"character varying", "varchar"}
    assert cols["scope"]["char_max_length"] == 255
    assert cols["created_at"]["data_type"] == "timestamp with time zone"
    assert cols["updated_at"]["data_type"] == "timestamp with time zone"


def test_s17_primary_key_is_user_id(alembic_cfg: Config) -> None:
    """PK is a single-column PK on user_id."""
    command.upgrade(alembic_cfg, "head")

    pk_cols = _pk_columns()
    assert pk_cols == ["user_id"], (
        f"expected single-column PK (user_id), got {pk_cols}"
    )

    constraints = _constraints()
    pk = [n for n, t in constraints.items() if t == "p"]
    assert len(pk) == 1, f"expected exactly one PK constraint, got {pk}"


def test_s17_fk_is_cascade_on_delete(alembic_cfg: Config) -> None:
    """FK on user_id must ON DELETE CASCADE (confdeltype='c')."""
    command.upgrade(alembic_cfg, "head")

    constraints = _constraints()
    fk = [n for n, t in constraints.items() if t == "f"]
    assert "fk_github_user_oauth_tokens_user_id" in fk, (
        f"expected FK fk_github_user_oauth_tokens_user_id, found: {fk}"
    )

    fks = _fk_actions()
    assert fks["fk_github_user_oauth_tokens_user_id"] == "c", (
        f"user FK should ON DELETE CASCADE (confdeltype='c'), got {fks}"
    )


def test_s17_duplicate_user_id_violates_pk(alembic_cfg: Config) -> None:
    """Inserting two rows with the same user_id raises IntegrityError."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    expires = _now_utc()

    with Session(engine) as session:
        user_id = _make_user(session, email_suffix="dup")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (user_id, installation_id, github_user_id,
                     access_token_encrypted, refresh_token_encrypted,
                     access_token_expires_at, refresh_token_expires_at, scope)
                VALUES
                    (:uid, :iid, :gid, :act, :rft, :ate, :rte, :sc)
                """
            ),
            {
                "uid": user_id,
                "iid": 1001,
                "gid": 42,
                "act": _FAKE_CIPHERTEXT,
                "rft": _FAKE_CIPHERTEXT,
                "ate": expires,
                "rte": expires,
                "sc": "repo,read:user",
            },
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (user_id, installation_id, github_user_id,
                         access_token_encrypted, refresh_token_encrypted,
                         access_token_expires_at, refresh_token_expires_at, scope)
                    VALUES
                        (:uid, :iid, :gid, :act, :rft, :ate, :rte, :sc)
                    """
                ),
                {
                    "uid": user_id,
                    "iid": 1002,
                    "gid": 43,
                    "act": _FAKE_CIPHERTEXT,
                    "rft": _FAKE_CIPHERTEXT,
                    "ate": expires,
                    "rte": expires,
                    "sc": "repo",
                },
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s17_cascade_on_user_delete(alembic_cfg: Config) -> None:
    """Deleting a user row cascades to the github_user_oauth_tokens row."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    expires = _now_utc()

    with Session(engine) as session:
        user_id = _make_user(session, email_suffix="cascade")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (user_id, installation_id, github_user_id,
                     access_token_encrypted, refresh_token_encrypted,
                     access_token_expires_at, refresh_token_expires_at, scope)
                VALUES
                    (:uid, :iid, :gid, :act, :rft, :ate, :rte, :sc)
                """
            ),
            {
                "uid": user_id,
                "iid": 2001,
                "gid": 99,
                "act": _FAKE_CIPHERTEXT,
                "rft": _FAKE_CIPHERTEXT,
                "ate": expires,
                "rte": expires,
                "sc": "repo",
            },
        )
        session.commit()

        before = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar_one()
        assert before == 1, f"expected 1 token row before delete, got {before}"

        session.execute(
            text('DELETE FROM "user" WHERE id = :id'), {"id": user_id}
        )
        session.commit()

        after = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar_one()
        assert after == 0, (
            "github_user_oauth_tokens row should cascade-delete with parent user"
        )

    _truncate()


def test_s17_server_defaults_created_and_updated_at(alembic_cfg: Config) -> None:
    """Insert without created_at/updated_at must land non-NULL via server defaults."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    expires = _now_utc()

    with Session(engine) as session:
        user_id = _make_user(session, email_suffix="defaults")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (user_id, installation_id, github_user_id,
                     access_token_encrypted, refresh_token_encrypted,
                     access_token_expires_at, refresh_token_expires_at, scope)
                VALUES
                    (:uid, :iid, :gid, :act, :rft, :ate, :rte, :sc)
                """
            ),
            {
                "uid": user_id,
                "iid": 3001,
                "gid": 77,
                "act": _FAKE_CIPHERTEXT,
                "rft": _FAKE_CIPHERTEXT,
                "ate": expires,
                "rte": expires,
                "sc": "repo,read:user",
            },
        )
        session.commit()

        row = session.execute(
            text(
                f"SELECT created_at, updated_at FROM {TABLE} WHERE user_id = :uid"
            ),
            {"uid": user_id},
        ).one()
        assert row[0] is not None, "created_at should be non-NULL via server default"
        assert row[1] is not None, "updated_at should be non-NULL via server default"

    _truncate()


def test_s17_downgrade_drops_table(alembic_cfg: Config) -> None:
    """Downgrade to s16 drops github_user_oauth_tokens cleanly."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S16_REV)

    cols = _columns()
    assert cols == {}, (
        f"{TABLE} columns should be empty after downgrade, got {cols}"
    )
    constraints = _constraints()
    assert constraints == {}, (
        f"{TABLE} constraints should be empty after downgrade, got {constraints}"
    )
    # autouse fixture restores head.


def test_s17_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["columns"], (
        "precondition: table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S16_REV)
    command.upgrade(alembic_cfg, S17_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
