"""Integration tests for the S08 push_subscriptions migration.

Exercises ``s07_notifications`` ⇄ ``s08_push_subscriptions`` against the
real Postgres test DB:

  1. After upgrade (head), assert ``push_subscriptions`` exists with the
     expected columns + types + nullability, the UNIQUE(user_id, endpoint)
     constraint, and the user_id index.
  2. UNIQUE(user_id, endpoint) collides on the second INSERT — confirms the
     re-subscribe-as-upsert contract holds at the storage layer.
  3. Two rows with the same user_id but different endpoints coexist —
     confirms phone+laptop multi-device registration is allowed.
  4. Deleting a user CASCADEs to their subscriptions.
  5. Downgrade drops the table cleanly.
  6. Downgrade then re-upgrade leaves the schema byte-identical.

Uses the MEM016 autouse fixture pattern (commit + close autouse session,
``engine.dispose()``) to avoid AccessShareLock deadlocks with alembic DDL.
"""
from __future__ import annotations

import json
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

S07_REV = "s07_notifications"
S08_REV = "s08_push_subscriptions"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend

PUSH_SUBSCRIPTIONS = "push_subscriptions"


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
            f"Could not restore DB to head after S08 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DB introspection helpers
# ---------------------------------------------------------------------------


def _columns(table: str) -> dict[str, dict[str, str | int | None]]:
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
            {"t": table},
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


def _indexes(table: str) -> dict[str, dict[str, str | bool | None]]:
    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT i.relname AS index_name,
                       ix.indisunique AS is_unique,
                       pg_get_indexdef(ix.indexrelid) AS indexdef
                FROM pg_class t
                JOIN pg_index ix ON t.oid = ix.indrelid
                JOIN pg_class i  ON i.oid = ix.indexrelid
                WHERE t.relname = :t
                ORDER BY i.relname
                """
            ),
            {"t": table},
        ).all()
    return {
        row[0]: {"is_unique": bool(row[1]), "indexdef": row[2]} for row in rows
    }


def _constraints(table: str) -> dict[str, str]:
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
            {"t": table},
        ).all()
    return {row[0]: row[1] for row in rows}


def _fk_actions(table: str) -> dict[str, str]:
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
            {"t": table},
        ).all()
    return {row[0]: row[1] for row in rows}


def _schema_snapshot() -> dict[str, object]:
    return {
        "columns": _columns(PUSH_SUBSCRIPTIONS),
        "constraints": _constraints(PUSH_SUBSCRIPTIONS),
        "fks": _fk_actions(PUSH_SUBSCRIPTIONS),
        "indexes": _indexes(PUSH_SUBSCRIPTIONS),
    }


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _make_user(session: Session, *, suffix: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO "user" (id, email, is_active, role,
                                full_name, hashed_password, created_at)
            VALUES (:id, :email, TRUE, 'user'::userrole,
                    :name, 'x', NOW())
            """
        ),
        {
            "id": user_id,
            "email": f"s08_{suffix}_{user_id.hex[:8]}@example.com",
            "name": f"S08 {suffix}",
        },
    )
    return user_id


def _make_subscription(
    session: Session,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    keys: dict | None = None,
    user_agent: str | None = None,
) -> uuid.UUID:
    sid = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO push_subscriptions
                (id, user_id, endpoint, keys, user_agent)
            VALUES (:id, :user, :endpoint, CAST(:keys AS JSONB), :ua)
            """
        ),
        {
            "id": sid,
            "user": user_id,
            "endpoint": endpoint,
            "keys": json.dumps(keys or {"p256dh": "x", "auth": "y"}),
            "ua": user_agent,
        },
    )
    return sid


def _truncate() -> None:
    """Wipe rows so we can downgrade cleanly."""
    with Session(engine) as session:
        session.execute(text(f"DELETE FROM {PUSH_SUBSCRIPTIONS}"))
        session.execute(
            text("DELETE FROM \"user\" WHERE email LIKE 's08_%@example.com'")
        )
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s08_upgrade_creates_table_with_expected_columns(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns(PUSH_SUBSCRIPTIONS)
    assert set(cols) == {
        "id",
        "user_id",
        "endpoint",
        "keys",
        "user_agent",
        "created_at",
        "last_seen_at",
        "last_status_code",
        "consecutive_failures",
    }, f"unexpected push_subscriptions column set: {sorted(cols)}"

    # Required (NOT NULL) columns.
    assert cols["id"]["is_nullable"] == "NO"
    assert cols["user_id"]["is_nullable"] == "NO"
    assert cols["endpoint"]["is_nullable"] == "NO"
    assert cols["keys"]["is_nullable"] == "NO"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["last_seen_at"]["is_nullable"] == "NO"
    assert cols["consecutive_failures"]["is_nullable"] == "NO"

    # Optional columns.
    assert cols["user_agent"]["is_nullable"] == "YES"
    assert cols["last_status_code"]["is_nullable"] == "YES"

    # Types.
    assert cols["id"]["data_type"] == "uuid"
    assert cols["user_id"]["data_type"] == "uuid"
    assert cols["endpoint"]["data_type"] == "text"
    assert cols["keys"]["data_type"] == "jsonb"
    assert cols["user_agent"]["data_type"] in {
        "character varying",
        "varchar",
    }
    assert cols["user_agent"]["char_max_length"] == 500
    assert cols["created_at"]["data_type"] == "timestamp with time zone"
    assert cols["last_seen_at"]["data_type"] == "timestamp with time zone"
    assert cols["last_status_code"]["data_type"] == "integer"
    assert cols["consecutive_failures"]["data_type"] == "integer"


def test_s08_upgrade_creates_constraints_and_index(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    constraints = _constraints(PUSH_SUBSCRIPTIONS)
    pk = [n for n, t in constraints.items() if t == "p"]
    fk = [n for n, t in constraints.items() if t == "f"]
    uq = [n for n, t in constraints.items() if t == "u"]
    assert len(pk) == 1
    assert "fk_push_subscriptions_user_id" in fk
    assert "uq_push_subscriptions_user_id_endpoint" in uq, constraints

    fks = _fk_actions(PUSH_SUBSCRIPTIONS)
    assert fks["fk_push_subscriptions_user_id"] == "c", (
        f"user FK should ON DELETE CASCADE, got {fks}"
    )

    idx = _indexes(PUSH_SUBSCRIPTIONS)
    assert "ix_push_subscriptions_user_id" in idx, idx
    user_idx = idx["ix_push_subscriptions_user_id"]
    assert user_idx["is_unique"] is False, (
        "ix_push_subscriptions_user_id should be a non-unique btree"
    )
    assert "user_id" in user_idx["indexdef"], user_idx


def test_s08_unique_user_endpoint_collides_on_duplicate(
    alembic_cfg: Config,
) -> None:
    """Same (user, endpoint) pair must collide on the second INSERT."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="dup")
        session.commit()
        endpoint = "https://fcm.googleapis.com/fcm/send/dup-token-1"
        _make_subscription(session, user_id=user_id, endpoint=endpoint)
        session.commit()

        with pytest.raises(IntegrityError):
            _make_subscription(session, user_id=user_id, endpoint=endpoint)
            session.commit()
        session.rollback()

    _truncate()


def test_s08_same_user_different_endpoints_coexist(
    alembic_cfg: Config,
) -> None:
    """phone + laptop = two rows; same user_id, distinct endpoints."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="multi")
        session.commit()
        _make_subscription(
            session,
            user_id=user_id,
            endpoint="https://fcm.googleapis.com/fcm/send/phone-token",
            user_agent="Mozilla/5.0 (Android)",
        )
        _make_subscription(
            session,
            user_id=user_id,
            endpoint="https://updates.push.services.mozilla.com/wpush/v2/laptop",
            user_agent="Mozilla/5.0 (Macintosh)",
        )
        session.commit()

        observed = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_SUBSCRIPTIONS} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        assert observed == 2

    _truncate()


def test_s08_user_delete_cascades_subscriptions(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="cascade")
        session.commit()
        _make_subscription(
            session,
            user_id=user_id,
            endpoint="https://fcm.googleapis.com/fcm/send/cascade-tok",
        )
        session.commit()

        before = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_SUBSCRIPTIONS} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        assert before == 1

        session.execute(
            text('DELETE FROM "user" WHERE id = :u'), {"u": user_id}
        )
        session.commit()

        after = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_SUBSCRIPTIONS} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        assert after == 0

    _truncate()


def test_s08_downgrade_drops_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S07_REV)

    cols = _columns(PUSH_SUBSCRIPTIONS)
    assert cols == {}, (
        f"{PUSH_SUBSCRIPTIONS} columns should be empty after downgrade,"
        f" got {cols}"
    )
    # autouse fixture restores head.


def test_s08_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade leaves the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["columns"], (
        "precondition: push_subscriptions table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S07_REV)
    command.upgrade(alembic_cfg, S08_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
