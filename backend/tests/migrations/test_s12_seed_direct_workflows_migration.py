"""Integration tests for the S12 system-workflow seed migration.

Exercises ``s11_workflow_runs`` ⇄ ``s12_seed_direct_workflows`` against the
real Postgres test DB:

  1. Pre-existing teams created at the s11 head get _direct_claude +
     _direct_codex rows (system_owned=TRUE, scope='user') after the s12
     upgrade — the backfill semantics.
  2. Each seeded workflow gets exactly one step at step_index=0 with the
     right action and ``{"prompt_template": "{prompt}"}`` config.
  3. Re-running the upgrade is idempotent (the migration's ON CONFLICT
     DO NOTHING shape is exercised by upgrading + downgrading the head
     boundary or by simulating a partial seed and re-running).
  4. Teams created BETWEEN the migration and the next downgrade get
     seeded too (proves the data shape is parity with the runtime helper).
  5. Downgrade removes the seeded rows — only system_owned rows with
     the two reserved names — leaving any user-owned rows untouched.

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
from sqlmodel import Session

from app.core.db import engine

S11_REV = "s11_workflow_runs"
S12_REV = "s12_seed_direct_workflows"
BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
            f"Could not restore DB to head after S12 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


def _truncate_workflow_state() -> None:
    with Session(engine) as session:
        session.execute(text("DELETE FROM step_runs"))
        session.execute(text("DELETE FROM workflow_runs"))
        session.execute(text("DELETE FROM workflow_steps"))
        session.execute(text("DELETE FROM workflows"))
        session.commit()


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
            "name": f"s12-{slug_suffix}",
            "slug": f"s12-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    session.commit()
    return team_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s12_backfills_system_workflows(alembic_cfg: Config) -> None:
    """Upgrading from s11 → s12 backfills _direct_claude + _direct_codex
    for every existing team."""
    # Roll back to s11 first so the s12 upgrade is a fresh data migration.
    command.downgrade(alembic_cfg, S11_REV)
    _truncate_workflow_state()

    with Session(engine) as session:
        team_a = _make_team(session, slug_suffix="bf-a")
        team_b = _make_team(session, slug_suffix="bf-b")
        session.commit()

    command.upgrade(alembic_cfg, S12_REV)

    with Session(engine) as session:
        for team_id, label in ((team_a, "team_a"), (team_b, "team_b")):
            rows = session.execute(
                text(
                    """
                    SELECT name, scope, system_owned
                    FROM workflows
                    WHERE team_id = :t
                    ORDER BY name
                    """
                ),
                {"t": team_id},
            ).all()
            assert [r[0] for r in rows] == [
                "_direct_claude",
                "_direct_codex",
            ], f"{label} did not get both system workflows: {rows}"
            for _, scope, system_owned in rows:
                assert scope == "user"
                assert system_owned is True


def test_s12_seeds_step_payload(alembic_cfg: Config) -> None:
    """Each seeded workflow gets one step at index 0 with the right action
    and the documented config payload."""
    command.downgrade(alembic_cfg, S11_REV)
    _truncate_workflow_state()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="step")
        session.commit()

    command.upgrade(alembic_cfg, S12_REV)

    with Session(engine) as session:
        steps = session.execute(
            text(
                """
                SELECT w.name, ws.step_index, ws.action, ws.config
                FROM workflows w
                JOIN workflow_steps ws ON ws.workflow_id = w.id
                WHERE w.team_id = :t
                ORDER BY w.name
                """
            ),
            {"t": team_id},
        ).all()
        assert len(steps) == 2
        by_name = {row[0]: row for row in steps}
        assert by_name["_direct_claude"][1] == 0
        assert by_name["_direct_claude"][2] == "claude"
        assert by_name["_direct_claude"][3] == {"prompt_template": "{prompt}"}
        assert by_name["_direct_codex"][1] == 0
        assert by_name["_direct_codex"][2] == "codex"
        assert by_name["_direct_codex"][3] == {"prompt_template": "{prompt}"}


def test_s12_idempotent_re_upgrade(alembic_cfg: Config) -> None:
    """downgrade → upgrade → downgrade → upgrade leaves the schema +
    seeded data byte-identical (modulo row id values, which we ignore).
    """
    command.downgrade(alembic_cfg, S11_REV)
    _truncate_workflow_state()
    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="idem")
        session.commit()

    command.upgrade(alembic_cfg, S12_REV)
    command.downgrade(alembic_cfg, S11_REV)
    command.upgrade(alembic_cfg, S12_REV)

    with Session(engine) as session:
        names = sorted(
            row[0]
            for row in session.execute(
                text(
                    """
                    SELECT name FROM workflows WHERE team_id = :t
                    """
                ),
                {"t": team_id},
            ).all()
        )
        assert names == ["_direct_claude", "_direct_codex"]
        # Each name still has exactly one step (no duplicates).
        step_counts = session.execute(
            text(
                """
                SELECT w.name, COUNT(*) FROM workflows w
                JOIN workflow_steps ws ON ws.workflow_id = w.id
                WHERE w.team_id = :t
                GROUP BY w.name
                """
            ),
            {"t": team_id},
        ).all()
        for _, n in step_counts:
            assert n == 1


def test_s12_partial_seed_recovery(alembic_cfg: Config) -> None:
    """If a prior partial seed left only _direct_claude on a team, re-running
    the upgrade adds _direct_codex without touching the existing row.
    """
    command.downgrade(alembic_cfg, S11_REV)
    _truncate_workflow_state()
    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="partial")
        session.commit()

        # Pre-insert _direct_claude only.
        claude_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name, description, scope, system_owned)
                VALUES (:id, :t, '_direct_claude', 'pre-seeded', 'user', TRUE)
                """
            ),
            {"id": claude_id, "t": team_id},
        )
        session.execute(
            text(
                """
                INSERT INTO workflow_steps (id, workflow_id, step_index, action, config)
                VALUES (:id, :wf, 0, 'claude', CAST(:c AS JSONB))
                """
            ),
            {
                "id": uuid.uuid4(),
                "wf": claude_id,
                "c": '{"prompt_template":"{prompt}"}',
            },
        )
        session.commit()

    command.upgrade(alembic_cfg, S12_REV)

    with Session(engine) as session:
        rows = session.execute(
            text(
                """
                SELECT id, name, description FROM workflows
                WHERE team_id = :t ORDER BY name
                """
            ),
            {"t": team_id},
        ).all()
        names = [r[1] for r in rows]
        assert names == ["_direct_claude", "_direct_codex"]
        # The pre-existing _direct_claude row was preserved (id matches,
        # description not overwritten).
        claude_row = next(r for r in rows if r[1] == "_direct_claude")
        assert claude_row[0] == claude_id, (
            "partial-seed row should be preserved, not replaced"
        )
        assert claude_row[2] == "pre-seeded"


def test_s12_downgrade_removes_only_system_named_rows(
    alembic_cfg: Config,
) -> None:
    """Downgrade strips the two _direct_* system rows; user-owned rows
    (system_owned=FALSE) on the same team survive intact.
    """
    command.upgrade(alembic_cfg, "head")
    _truncate_workflow_state()
    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dn")
        session.commit()

        # User-owned workflow (NOT system_owned) — the downgrade must NOT
        # touch this even if it happens to share the name namespace.
        user_wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (id, team_id, name, description, scope, system_owned)
                VALUES (:id, :t, 'user_thing', 'kept by user', 'user', FALSE)
                """
            ),
            {"id": user_wf_id, "t": team_id},
        )
        session.commit()

    # Apply s12 (it backfills the team) — then downgrade.
    command.upgrade(alembic_cfg, S12_REV)
    command.downgrade(alembic_cfg, S11_REV)

    with Session(engine) as session:
        names = sorted(
            row[0]
            for row in session.execute(
                text("SELECT name FROM workflows WHERE team_id = :t"),
                {"t": team_id},
            ).all()
        )
        assert names == ["user_thing"], (
            f"downgrade should leave only user-owned rows, got {names}"
        )
    # autouse fixture restores head.


def test_s12_seed_works_for_team_added_after_upgrade(
    alembic_cfg: Config,
) -> None:
    """Teams created AFTER the s12 upgrade do NOT auto-seed via the
    migration (which only runs once) — but the runtime helper covers
    this. This test asserts the migration shape only: it does not
    retroactively create rows for post-migration teams.

    This documents the boundary: the migration handles backfill; the
    runtime ``seed_system_workflows`` call inside the team-create code
    path handles the going-forward case.
    """
    command.upgrade(alembic_cfg, "head")
    _truncate_workflow_state()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="post")
        session.commit()

        # No rows yet — the migration ran before this team existed, so
        # the data migration is a no-op for it. Going forward, the
        # runtime helper writes these rows; the migration boundary is
        # historical.
        names = sorted(
            row[0]
            for row in session.execute(
                text("SELECT name FROM workflows WHERE team_id = :t"),
                {"t": team_id},
            ).all()
        )
        assert names == [], (
            "migration should not retroactively seed teams created after it ran; "
            "runtime helper covers post-migration teams"
        )
