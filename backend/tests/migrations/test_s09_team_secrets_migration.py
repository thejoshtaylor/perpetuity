"""Integration tests for the S09 team_secrets Alembic migration.

Exercises ``s08_push_subscriptions`` ⇄ ``s09_team_secrets`` against the
real Postgres test DB:

  1. After upgrade (head), assert ``team_secrets`` exists with the
     expected columns + types + nullability, the composite PK on
     (team_id, key), and the team FK CASCADE.
  2. Inserting a duplicate (team_id, key) fails with IntegrityError —
     confirms the composite PK holds at the storage layer.
  3. Two rows with the same team_id but different key coexist, and two
     rows with the same key but different team_id coexist — confirms
     the PK pair is correctly composite.
  4. Deleting a team CASCADEs to its secrets.
  5. ``has_value`` and ``sensitive`` server-defaults land TRUE on
     insert when the column is omitted.
  6. Downgrade to ``s08_push_subscriptions`` drops the table cleanly.
  7. Downgrade then re-upgrade leaves the schema byte-identical.

Uses the MEM016 autouse fixture pattern (commit + close autouse session,
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

S08_REV = "s08_push_subscriptions"
S09_REV = "s09_team_secrets"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend

TABLE = "team_secrets"


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
            f"Could not restore DB to head after S09 migration test: {restore_err}"
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


# Fernet-shaped opaque bytes used as fake ciphertext. The migration
# does NOT decrypt — it only stores BYTEA — so any non-empty bytes
# satisfy the NOT NULL constraint here.
_FAKE_CIPHERTEXT = b"gAAAAAB-fake-not-real-fernet-token-padding"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s09_upgrade_creates_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns()
    assert set(cols) == {
        "team_id",
        "key",
        "value_encrypted",
        "has_value",
        "sensitive",
        "created_at",
        "updated_at",
    }, f"unexpected column set: {sorted(cols)}"

    # Nullability — every column NOT NULL per the success criteria.
    assert cols["team_id"]["is_nullable"] == "NO"
    assert cols["key"]["is_nullable"] == "NO"
    assert cols["value_encrypted"]["is_nullable"] == "NO"
    assert cols["has_value"]["is_nullable"] == "NO"
    assert cols["sensitive"]["is_nullable"] == "NO"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["updated_at"]["is_nullable"] == "NO"

    # Types + bounded lengths.
    assert cols["team_id"]["data_type"] == "uuid"
    assert cols["key"]["data_type"] in {"character varying", "varchar"}
    assert cols["key"]["char_max_length"] == 64
    assert cols["value_encrypted"]["data_type"] == "bytea"
    assert cols["has_value"]["data_type"] == "boolean"
    assert cols["sensitive"]["data_type"] == "boolean"
    assert cols["created_at"]["data_type"] == "timestamp with time zone"
    assert cols["updated_at"]["data_type"] == "timestamp with time zone"

    # Composite PK on (team_id, key).
    pk_cols = _pk_columns()
    assert pk_cols == ["team_id", "key"], (
        f"expected composite PK (team_id, key), got {pk_cols}"
    )

    constraints = _constraints()
    pk = [n for n, t in constraints.items() if t == "p"]
    fk = [n for n, t in constraints.items() if t == "f"]
    assert len(pk) == 1, f"expected one PK, got {pk}"
    assert "fk_team_secrets_team_id" in fk

    # FK on team_id must CASCADE on parent delete (confdeltype='c').
    fks = _fk_actions()
    assert fks["fk_team_secrets_team_id"] == "c", (
        f"team FK should ON DELETE CASCADE, got {fks}"
    )


def test_s09_duplicate_team_id_key_fails_pk(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup")
        session.commit()

        session.execute(
            text(
                f"""
                INSERT INTO {TABLE}
                    (team_id, key, value_encrypted)
                VALUES (:team, :key, :ct)
                """
            ),
            {"team": team_id, "key": "claude_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (team_id, key, value_encrypted)
                    VALUES (:team, :key, :ct)
                    """
                ),
                {
                    "team": team_id,
                    "key": "claude_api_key",
                    "ct": _FAKE_CIPHERTEXT,
                },
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s09_composite_pk_allows_distinct_pairs(alembic_cfg: Config) -> None:
    """Same team + different key, and different team + same key, must coexist."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_a = _make_team(session, slug_suffix="multi-a")
        team_b = _make_team(session, slug_suffix="multi-b")
        session.commit()

        # Same team, two different keys.
        session.execute(
            text(
                f"INSERT INTO {TABLE} (team_id, key, value_encrypted) "
                f"VALUES (:t, :k, :ct)"
            ),
            {"t": team_a, "k": "claude_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.execute(
            text(
                f"INSERT INTO {TABLE} (team_id, key, value_encrypted) "
                f"VALUES (:t, :k, :ct)"
            ),
            {"t": team_a, "k": "openai_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        # Different team, same key as team_a.
        session.execute(
            text(
                f"INSERT INTO {TABLE} (team_id, key, value_encrypted) "
                f"VALUES (:t, :k, :ct)"
            ),
            {"t": team_b, "k": "claude_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.commit()

        count = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE}")
        ).scalar_one()
        assert count == 3, (
            f"expected 3 rows across 2 teams + 2 keys, got {count}"
        )

    _truncate()


def test_s09_team_delete_cascades(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        session.commit()

        session.execute(
            text(
                f"INSERT INTO {TABLE} (team_id, key, value_encrypted) "
                f"VALUES (:t, :k, :ct)"
            ),
            {"t": team_id, "k": "claude_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.execute(
            text(
                f"INSERT INTO {TABLE} (team_id, key, value_encrypted) "
                f"VALUES (:t, :k, :ct)"
            ),
            {"t": team_id, "k": "openai_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.commit()

        before = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE team_id = :t"),
            {"t": team_id},
        ).scalar_one()
        assert before == 2

        session.execute(text("DELETE FROM team WHERE id = :id"), {"id": team_id})
        session.commit()

        after = session.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE team_id = :t"),
            {"t": team_id},
        ).scalar_one()
        assert after == 0, "team_secrets rows should cascade-delete with parent team"

    _truncate()


def test_s09_has_value_and_sensitive_default_true(alembic_cfg: Config) -> None:
    """Insert without has_value/sensitive must land TRUE via the server defaults."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="default")
        session.commit()

        # Note: omit has_value + sensitive entirely so the server defaults fire.
        session.execute(
            text(
                f"""
                INSERT INTO {TABLE} (team_id, key, value_encrypted)
                VALUES (:t, :k, :ct)
                """
            ),
            {"t": team_id, "k": "claude_api_key", "ct": _FAKE_CIPHERTEXT},
        )
        session.commit()

        row = session.execute(
            text(
                f"SELECT has_value, sensitive FROM {TABLE} "
                f"WHERE team_id = :t AND key = :k"
            ),
            {"t": team_id, "k": "claude_api_key"},
        ).one()
        assert row[0] is True, f"has_value should default TRUE, got {row[0]}"
        assert row[1] is True, f"sensitive should default TRUE, got {row[1]}"

    _truncate()


def test_s09_downgrade_drops_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S08_REV)

    cols = _columns()
    assert cols == {}, f"{TABLE} columns should be empty after downgrade, got {cols}"
    constraints = _constraints()
    assert constraints == {}, (
        f"{TABLE} constraints should be empty after downgrade, got {constraints}"
    )
    # autouse fixture restores head.


def test_s09_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["columns"], "precondition: table should exist before round-trip"

    command.downgrade(alembic_cfg, S08_REV)
    command.upgrade(alembic_cfg, S09_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )


def test_s09_team_secret_public_excludes_value_encrypted() -> None:
    """TeamSecretStatus DTO must never expose value_encrypted, even when populated."""
    from app.models import TeamSecret, TeamSecretPublic, TeamSecretStatus

    fully_populated = TeamSecret(
        team_id=uuid.uuid4(),
        key="claude_api_key",
        value_encrypted=b"\x01\x02\x03 should not leak",
        has_value=True,
        sensitive=True,
    )

    # Public DTO: value_encrypted must not appear at all.
    public = TeamSecretPublic.model_validate(
        fully_populated, from_attributes=True
    ).model_dump()
    assert "value_encrypted" not in public, (
        f"TeamSecretPublic must not expose value_encrypted, got keys: {sorted(public)}"
    )

    # Status DTO: value_encrypted must not appear at all.
    status = TeamSecretStatus(
        key=fully_populated.key,
        has_value=fully_populated.has_value,
        sensitive=fully_populated.sensitive,
        updated_at=fully_populated.updated_at,
    ).model_dump()
    assert "value_encrypted" not in status, (
        f"TeamSecretStatus must not expose value_encrypted, got keys: {sorted(status)}"
    )
    assert set(status.keys()) == {"key", "has_value", "sensitive", "updated_at"}, (
        f"TeamSecretStatus shape drifted: {sorted(status)}"
    )
