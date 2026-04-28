"""Schema-level integration tests for the S06e webhook receiver migration.

Exercises ``s06d_projects_and_push_rules`` ⇄ ``s06e_github_webhook_events``
on the real Postgres test DB. The slice's idempotency-correctness invariant
lives in the DB, not in the route — these tests pin the storage layer so
the route can rely on ``INSERT ... ON CONFLICT (delivery_id) DO NOTHING``:

  1. Duplicate ``delivery_id`` insert raises ``IntegrityError``
     (UNIQUE on ``github_webhook_events.delivery_id``) — the slice's
     idempotency invariant under GitHub's 24h retry contract.
  2. Deleting a parent ``github_app_installations`` row sets the child's
     ``installation_id`` to NULL (FK ``ON DELETE SET NULL``) — the
     audit-trail-preservation invariant.
  3. Alembic round-trip (upgrade → downgrade → upgrade) leaves the schema
     byte-identical, catching divergence between the SQLModel and the
     migration.

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

S06D_REV = "s06d_projects_and_push_rules"
S06E_REV = "s06e_github_webhook_events"
BACKEND_ROOT = Path(__file__).resolve().parents[3]  # <repo>/backend

EVENTS = "github_webhook_events"
REJECTIONS = "webhook_rejections"


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
            f"Could not restore DB to head after S06e migration test: "
            f"{restore_err}"
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
        "events_columns": _columns(EVENTS),
        "events_constraints": _constraints(EVENTS),
        "events_fks": _fk_actions(EVENTS),
        "rejections_columns": _columns(REJECTIONS),
        "rejections_constraints": _constraints(REJECTIONS),
        "rejections_fks": _fk_actions(REJECTIONS),
    }


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _make_team(session: Session, *, slug_suffix: str) -> uuid.UUID:
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


def _make_install(
    session: Session, *, team_id: uuid.UUID, installation_id: int
) -> None:
    session.execute(
        text(
            """
            INSERT INTO github_app_installations
                (id, team_id, installation_id, account_login, account_type,
                 created_at)
            VALUES
                (:id, :team, :inst, :login, 'Organization', NOW())
            """
        ),
        {
            "id": uuid.uuid4(),
            "team": team_id,
            "inst": installation_id,
            "login": f"org-{installation_id}",
        },
    )


def _make_event(
    session: Session,
    *,
    delivery_id: str,
    installation_id: int | None,
    event_type: str = "ping",
    payload: str = '{"zen": "Speak like a human."}',
) -> uuid.UUID:
    eid = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO github_webhook_events
                (id, installation_id, event_type, delivery_id, payload,
                 received_at, dispatch_status)
            VALUES
                (:id, :inst, :etype, :did, CAST(:payload AS jsonb),
                 NOW(), 'noop')
            """
        ),
        {
            "id": eid,
            "inst": installation_id,
            "etype": event_type,
            "did": delivery_id,
            "payload": payload,
        },
    )
    return eid


def _truncate() -> None:
    with Session(engine) as session:
        # FK order: events (FK→installations), rejections, then installations.
        session.execute(text(f"DELETE FROM {EVENTS}"))
        session.execute(text(f"DELETE FROM {REJECTIONS}"))
        session.execute(text("DELETE FROM github_app_installations"))
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s06e_unique_delivery_id_raises_integrity_error(
    alembic_cfg: Config,
) -> None:
    """The slice's idempotency invariant: a duplicate delivery_id MUST raise.

    The receiver route relies on the DB-level UNIQUE — without it the
    INSERT ... ON CONFLICT path silently double-dispatches under GitHub's
    24h retry policy (D025 / MEM229).
    """
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup-delivery")
        _make_install(session, team_id=team_id, installation_id=7001)
        session.commit()

        _make_event(
            session,
            delivery_id="dlv-dup-001",
            installation_id=7001,
            event_type="push",
        )
        session.commit()

        with pytest.raises(IntegrityError):
            _make_event(
                session,
                delivery_id="dlv-dup-001",
                installation_id=7001,
                event_type="push",
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06e_installation_delete_sets_null_on_event(
    alembic_cfg: Config,
) -> None:
    """Audit-trail-preservation invariant: deleting an installation must
    NOT cascade-delete the events GitHub already sent — the FK is
    ON DELETE SET NULL, so the row stays but `installation_id` goes NULL.
    """
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="set-null")
        _make_install(session, team_id=team_id, installation_id=7100)
        session.commit()

        eid = _make_event(
            session,
            delivery_id="dlv-setnull-001",
            installation_id=7100,
            event_type="pull_request",
        )
        session.commit()

        # Sanity: the FK is set before we delete.
        before = session.execute(
            text(
                f"SELECT installation_id FROM {EVENTS} WHERE id = :id"
            ),
            {"id": eid},
        ).scalar_one()
        assert before == 7100, (
            f"precondition failed: expected installation_id=7100, got {before}"
        )

        session.execute(
            text(
                "DELETE FROM github_app_installations"
                " WHERE installation_id = :i"
            ),
            {"i": 7100},
        )
        session.commit()

        after = session.execute(
            text(
                f"SELECT installation_id FROM {EVENTS} WHERE id = :id"
            ),
            {"id": eid},
        ).scalar_one()
        assert after is None, (
            "FK should be ON DELETE SET NULL — child row must survive"
            f" with installation_id=NULL, got {after}"
        )

    _truncate()


def test_s06e_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical.

    Catches drift between the SQLModel classes and the migration script —
    if the next agent edits `models.py` without writing a migration, the
    snapshot diverges and this test fails.
    """
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["events_columns"], (
        "precondition: github_webhook_events should exist before round-trip"
    )
    assert before["rejections_columns"], (
        "precondition: webhook_rejections should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S06D_REV)

    cols_e = _columns(EVENTS)
    cols_r = _columns(REJECTIONS)
    assert cols_e == {}, (
        f"{EVENTS} columns should be empty after downgrade, got {cols_e}"
    )
    assert cols_r == {}, (
        f"{REJECTIONS} columns should be empty after downgrade, got {cols_r}"
    )

    command.upgrade(alembic_cfg, S06E_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
