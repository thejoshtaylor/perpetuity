"""Integration tests for the S07 notifications + notification_preferences migration.

Exercises ``s06e_github_webhook_events`` ⇄ ``s07_notifications`` against
the real Postgres test DB:

  1. After upgrade (head), assert ``notifications`` and
     ``notification_preferences`` exist with the expected columns, the
     CHECK constraints pin ``kind`` / ``event_type`` to the seven enum
     values, and the indexes (``ix_notifications_user_id_created_at``,
     partial ``ix_notifications_unread_count``,
     ``ix_notification_preferences_pk``) exist with the right shape.
  2. The COALESCE-aware UNIQUE INDEX collides two NULL workflow_id rows
     for the same (user, event_type) — confirms the team-default
     uniqueness contract.
  3. Two rows differing only in ``workflow_id`` (NULL vs UUID) coexist —
     confirms the override path is allowed.
  4. CHECK constraints reject unknown ``kind`` and ``event_type`` values.
  5. Deleting a user cascades to their notifications and preferences.
  6. Deleting the source team SET-NULLs ``source_team_id`` (audit-trail
     preservation, mirrors the s06e installation_id contract).
  7. After downgrade to ``s06e_github_webhook_events``, both tables are gone.
  8. Downgrade then re-upgrade leaves the schema byte-identical.

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

S06E_REV = "s06e_github_webhook_events"
S07_REV = "s07_notifications"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend

NOTIFICATIONS = "notifications"
PREFERENCES = "notification_preferences"

ALL_KINDS = (
    "workflow_run_started",
    "workflow_run_succeeded",
    "workflow_run_failed",
    "workflow_step_completed",
    "team_invite_accepted",
    "project_created",
    "system",
)


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
            f"Could not restore DB to head after S07 migration test: {restore_err}"
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
    """Return name → {is_unique, indexdef} for every index on `table`."""
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
        "notifications_columns": _columns(NOTIFICATIONS),
        "notifications_constraints": _constraints(NOTIFICATIONS),
        "notifications_fks": _fk_actions(NOTIFICATIONS),
        "notifications_indexes": _indexes(NOTIFICATIONS),
        "preferences_columns": _columns(PREFERENCES),
        "preferences_constraints": _constraints(PREFERENCES),
        "preferences_fks": _fk_actions(PREFERENCES),
        "preferences_indexes": _indexes(PREFERENCES),
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
            "email": f"s07_{suffix}_{user_id.hex[:8]}@example.com",
            "name": f"S07 {suffix}",
        },
    )
    return user_id


def _make_team(session: Session, *, suffix: str) -> uuid.UUID:
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
            "name": f"s07-team-{suffix}",
            "slug": f"s07-team-{suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    return team_id


def _make_notification(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: str = "system",
    source_team_id: uuid.UUID | None = None,
) -> uuid.UUID:
    nid = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO notifications
                (id, user_id, kind, payload, source_team_id)
            VALUES
                (:id, :user, :kind, '{}'::jsonb, :team)
            """
        ),
        {"id": nid, "user": user_id, "kind": kind, "team": source_team_id},
    )
    return nid


def _make_preference(
    session: Session,
    *,
    user_id: uuid.UUID,
    workflow_id: uuid.UUID | None,
    event_type: str = "system",
    in_app: bool = True,
    push: bool = False,
) -> uuid.UUID:
    pid = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO notification_preferences
                (id, user_id, workflow_id, event_type, in_app, push)
            VALUES (:id, :user, :wf, :evt, :in_app, :push)
            """
        ),
        {
            "id": pid,
            "user": user_id,
            "wf": workflow_id,
            "evt": event_type,
            "in_app": in_app,
            "push": push,
        },
    )
    return pid


def _truncate() -> None:
    """Wipe rows so we can downgrade cleanly. Order respects FKs."""
    with Session(engine) as session:
        # notifications + preferences both FK to user; clear them first
        # then drop seeded users we created (matched by email prefix).
        session.execute(text(f"DELETE FROM {PREFERENCES}"))
        session.execute(text(f"DELETE FROM {NOTIFICATIONS}"))
        session.execute(
            text("DELETE FROM team WHERE slug LIKE 's07-team-%'")
        )
        session.execute(
            text("DELETE FROM \"user\" WHERE email LIKE 's07_%@example.com'")
        )
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s07_upgrade_creates_notifications_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns(NOTIFICATIONS)
    assert set(cols) == {
        "id",
        "user_id",
        "kind",
        "payload",
        "read_at",
        "created_at",
        "source_team_id",
        "source_project_id",
        "source_workflow_run_id",
    }, f"unexpected notifications column set: {sorted(cols)}"

    assert cols["id"]["is_nullable"] == "NO"
    assert cols["user_id"]["is_nullable"] == "NO"
    assert cols["kind"]["is_nullable"] == "NO"
    assert cols["payload"]["is_nullable"] == "NO"
    assert cols["read_at"]["is_nullable"] == "YES"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["source_team_id"]["is_nullable"] == "YES"
    assert cols["source_project_id"]["is_nullable"] == "YES"
    assert cols["source_workflow_run_id"]["is_nullable"] == "YES"

    assert cols["id"]["data_type"] == "uuid"
    assert cols["kind"]["data_type"] in {"character varying", "varchar"}
    assert cols["kind"]["char_max_length"] == 64
    assert cols["payload"]["data_type"] == "jsonb"
    assert cols["created_at"]["data_type"] == "timestamp with time zone"

    constraints = _constraints(NOTIFICATIONS)
    pk = [n for n, t in constraints.items() if t == "p"]
    fk = [n for n, t in constraints.items() if t == "f"]
    chk = [n for n, t in constraints.items() if t == "c"]
    assert len(pk) == 1
    assert "fk_notifications_user_id" in fk
    assert "fk_notifications_source_team_id" in fk
    assert "fk_notifications_source_project_id" in fk
    assert "ck_notifications_kind" in chk

    fks = _fk_actions(NOTIFICATIONS)
    assert fks["fk_notifications_user_id"] == "c", (
        f"user FK should ON DELETE CASCADE, got {fks}"
    )
    assert fks["fk_notifications_source_team_id"] == "n", (
        f"source_team FK should ON DELETE SET NULL, got {fks}"
    )
    assert fks["fk_notifications_source_project_id"] == "n", (
        f"source_project FK should ON DELETE SET NULL, got {fks}"
    )


def test_s07_upgrade_creates_indexes(alembic_cfg: Config) -> None:
    """Both indexes exist; the unread-count one is partial WHERE read_at IS NULL."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    idx = _indexes(NOTIFICATIONS)
    assert "ix_notifications_user_id_created_at" in idx, idx
    assert "ix_notifications_unread_count" in idx, idx

    chrono_def = idx["ix_notifications_user_id_created_at"]["indexdef"]
    assert "user_id" in chrono_def and "created_at" in chrono_def, chrono_def
    assert "DESC" in chrono_def, (
        f"chronological index must order created_at DESC, got: {chrono_def}"
    )

    unread_def = idx["ix_notifications_unread_count"]["indexdef"]
    assert "WHERE" in unread_def, (
        f"unread index must be partial WHERE clause, got: {unread_def}"
    )
    assert "read_at IS NULL" in unread_def, (
        f"unread index predicate must be 'read_at IS NULL', got: {unread_def}"
    )


def test_s07_upgrade_creates_preferences_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns(PREFERENCES)
    assert set(cols) == {
        "id",
        "user_id",
        "workflow_id",
        "event_type",
        "in_app",
        "push",
        "created_at",
        "updated_at",
    }, f"unexpected preferences column set: {sorted(cols)}"

    assert cols["id"]["is_nullable"] == "NO"
    assert cols["user_id"]["is_nullable"] == "NO"
    assert cols["workflow_id"]["is_nullable"] == "YES"
    assert cols["event_type"]["is_nullable"] == "NO"
    assert cols["in_app"]["is_nullable"] == "NO"
    assert cols["push"]["is_nullable"] == "NO"

    assert cols["event_type"]["char_max_length"] == 64
    assert cols["in_app"]["data_type"] == "boolean"
    assert cols["push"]["data_type"] == "boolean"

    constraints = _constraints(PREFERENCES)
    fk = [n for n, t in constraints.items() if t == "f"]
    chk = [n for n, t in constraints.items() if t == "c"]
    assert "fk_notification_preferences_user_id" in fk
    assert "ck_notification_preferences_event_type" in chk

    fks = _fk_actions(PREFERENCES)
    assert fks["fk_notification_preferences_user_id"] == "c"


def test_s07_preferences_unique_index_uses_coalesce(
    alembic_cfg: Config,
) -> None:
    """The PK-stand-in unique index is COALESCE-aware on workflow_id."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    idx = _indexes(PREFERENCES)
    assert "ix_notification_preferences_pk" in idx, idx
    pk_idx = idx["ix_notification_preferences_pk"]
    assert pk_idx["is_unique"] is True
    indexdef = pk_idx["indexdef"]
    assert "COALESCE" in indexdef.upper(), (
        f"preferences PK must wrap workflow_id in COALESCE, got: {indexdef}"
    )
    assert "00000000-0000-0000-0000-000000000000" in indexdef, (
        f"preferences PK must use zero-uuid as team-default sentinel, got: {indexdef}"
    )
    assert "user_id" in indexdef
    assert "event_type" in indexdef


def test_s07_team_default_collision_raises(alembic_cfg: Config) -> None:
    """Two team-default rows (workflow_id NULL) for same (user, event) collide."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="dup")
        session.commit()

        _make_preference(
            session,
            user_id=user_id,
            workflow_id=None,
            event_type="system",
        )
        session.commit()

        with pytest.raises(IntegrityError):
            _make_preference(
                session,
                user_id=user_id,
                workflow_id=None,
                event_type="system",
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s07_team_default_and_override_coexist(alembic_cfg: Config) -> None:
    """A NULL row and a UUID-override row for the same (user, event) coexist."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="override")
        session.commit()

        _make_preference(
            session,
            user_id=user_id,
            workflow_id=None,
            event_type="project_created",
        )
        _make_preference(
            session,
            user_id=user_id,
            workflow_id=uuid.uuid4(),
            event_type="project_created",
        )
        session.commit()

        observed = session.execute(
            text(
                "SELECT COUNT(*) FROM notification_preferences"
                " WHERE user_id = :u AND event_type = 'project_created'"
            ),
            {"u": user_id},
        ).scalar_one()
        assert observed == 2

    _truncate()


def test_s07_check_constraint_rejects_unknown_kind(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="badkind")
        session.commit()

        with pytest.raises(IntegrityError):
            _make_notification(session, user_id=user_id, kind="banana")
            session.commit()
        session.rollback()

    _truncate()


def test_s07_check_constraint_rejects_unknown_event_type(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="badevent")
        session.commit()

        with pytest.raises(IntegrityError):
            _make_preference(
                session,
                user_id=user_id,
                workflow_id=None,
                event_type="banana",
            )
            session.commit()
        session.rollback()

    _truncate()


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_s07_all_seven_kinds_accepted(
    alembic_cfg: Config, kind: str
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix=f"k-{kind}")
        session.commit()
        nid = _make_notification(session, user_id=user_id, kind=kind)
        session.commit()
        observed = session.execute(
            text("SELECT kind FROM notifications WHERE id = :i"),
            {"i": nid},
        ).scalar_one()
        assert observed == kind

    _truncate()


def test_s07_user_delete_cascades_notifications_and_preferences(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="cascade")
        session.commit()
        _make_notification(session, user_id=user_id)
        _make_preference(
            session,
            user_id=user_id,
            workflow_id=None,
            event_type="system",
        )
        session.commit()

        before_n = session.execute(
            text(
                f"SELECT COUNT(*) FROM {NOTIFICATIONS} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        before_p = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PREFERENCES} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        assert before_n == 1 and before_p == 1

        session.execute(
            text('DELETE FROM "user" WHERE id = :u'), {"u": user_id}
        )
        session.commit()

        after_n = session.execute(
            text(
                f"SELECT COUNT(*) FROM {NOTIFICATIONS} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        after_p = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PREFERENCES} WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
        assert after_n == 0
        assert after_p == 0

    _truncate()


def test_s07_team_delete_set_nulls_source_team_id(
    alembic_cfg: Config,
) -> None:
    """Deleting the source team must NULL source_team_id, not destroy the row."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        user_id = _make_user(session, suffix="setnull")
        team_id = _make_team(session, suffix="setnull")
        session.commit()
        nid = _make_notification(
            session, user_id=user_id, source_team_id=team_id
        )
        session.commit()

        session.execute(
            text("DELETE FROM team WHERE id = :t"), {"t": team_id}
        )
        session.commit()

        row = session.execute(
            text(
                "SELECT source_team_id FROM notifications WHERE id = :i"
            ),
            {"i": nid},
        ).scalar_one()
        assert row is None, (
            f"expected source_team_id to be NULL after team delete, got {row}"
        )

        # Notification itself survives.
        survived = session.execute(
            text("SELECT COUNT(*) FROM notifications WHERE id = :i"),
            {"i": nid},
        ).scalar_one()
        assert survived == 1

    _truncate()


def test_s07_downgrade_drops_both_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S06E_REV)

    cols_n = _columns(NOTIFICATIONS)
    cols_p = _columns(PREFERENCES)
    assert cols_n == {}, (
        f"{NOTIFICATIONS} columns should be empty after downgrade, got {cols_n}"
    )
    assert cols_p == {}, (
        f"{PREFERENCES} columns should be empty after downgrade, got {cols_p}"
    )
    # autouse fixture restores head.


def test_s07_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade leaves the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["notifications_columns"], (
        "precondition: notifications table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S06E_REV)
    command.upgrade(alembic_cfg, S07_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
