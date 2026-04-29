"""Integration tests for the S13 workflow CRUD extensions migration.

Exercises ``s12_seed_direct_workflows`` ⇄ ``s13_workflow_crud_extensions``
against the real Postgres test DB.

Coverage:
  1. Column shape + defaults — all six new columns present with correct
     Postgres types and server-side defaults.
  2. FK cascade — deleting the referenced user sets target_user_id and
     cancelled_by_user_id to NULL (SET NULL, not CASCADE or RESTRICT).
  3. CHECK rejection — inserting an unknown target_container value raises
     IntegrityError; valid values are accepted.
  4. round_robin_cursor is BIGINT and defaults to 0.
  5. Downgrade removes all six columns and the associated constraints,
     restoring the prior schema exactly.

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
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine

S12_REV = "s12_seed_direct_workflows"
S13_REV = "s13_workflow_crud_extensions"
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
            f"Could not restore DB to head after S13 migration test: {restore_err}"
        )
    finally:
        engine.dispose()


# ---- helpers ----------------------------------------------------------------


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
            "name": f"s13-{slug_suffix}",
            "slug": f"s13-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    session.commit()
    return team_id


def _make_user(session: Session) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO "user" (id, email, hashed_password, is_active, role, created_at)
            VALUES (:id, :email, 'x', TRUE, 'user', NOW())
            """
        ),
        {"id": user_id, "email": f"{uuid.uuid4().hex[:8]}@s13.test"},
    )
    session.commit()
    return user_id


def _make_workflow(
    session: Session,
    *,
    team_id: uuid.UUID,
    name: str = "test-workflow",
    scope: str = "user",
) -> uuid.UUID:
    wf_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name, scope, system_owned)
            VALUES (:id, :team_id, :name, :scope, FALSE)
            """
        ),
        {"id": wf_id, "team_id": team_id, "name": name, "scope": scope},
    )
    session.commit()
    return wf_id


def _make_workflow_step(
    session: Session,
    *,
    workflow_id: uuid.UUID,
    step_index: int = 0,
    action: str = "shell",
) -> uuid.UUID:
    step_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO workflow_steps (id, workflow_id, step_index, action, config)
            VALUES (:id, :wf, :si, :action, '{}'::jsonb)
            """
        ),
        {"id": step_id, "wf": workflow_id, "si": step_index, "action": action},
    )
    session.commit()
    return step_id


def _make_workflow_run(
    session: Session,
    *,
    workflow_id: uuid.UUID,
    team_id: uuid.UUID,
    triggered_by_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO workflow_runs (
                id, workflow_id, team_id, trigger_type,
                triggered_by_user_id, trigger_payload, status
            )
            VALUES (
                :id, :wf, :team, 'button',
                :user_id, '{}'::jsonb, 'pending'
            )
            """
        ),
        {
            "id": run_id,
            "wf": workflow_id,
            "team": team_id,
            "user_id": triggered_by_user_id,
        },
    )
    session.commit()
    return run_id


# ---- tests ------------------------------------------------------------------


def test_s13_column_shape_and_defaults(alembic_cfg: Config) -> None:
    """After s13 upgrade, all new columns exist with correct defaults."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="shape")

        # workflows: form_schema defaults to {}, round_robin_cursor to 0,
        # target_user_id defaults to NULL.
        wf_id = _make_workflow(session, team_id=team_id, name="shape-wf")
        row = session.execute(
            text(
                """
                SELECT form_schema, target_user_id, round_robin_cursor
                FROM workflows WHERE id = :id
                """
            ),
            {"id": wf_id},
        ).one()
        assert row.form_schema == {}, f"form_schema default wrong: {row.form_schema}"
        assert row.target_user_id is None
        assert row.round_robin_cursor == 0

        # workflow_steps: target_container defaults to 'user_workspace'.
        step_id = _make_workflow_step(session, workflow_id=wf_id)
        step_row = session.execute(
            text("SELECT target_container FROM workflow_steps WHERE id = :id"),
            {"id": step_id},
        ).one()
        assert step_row.target_container == "user_workspace"

        # workflow_runs: cancelled_by_user_id defaults to NULL,
        # cancelled_at defaults to NULL.
        run_id = _make_workflow_run(
            session, workflow_id=wf_id, team_id=team_id
        )
        run_row = session.execute(
            text(
                """
                SELECT cancelled_by_user_id, cancelled_at
                FROM workflow_runs WHERE id = :id
                """
            ),
            {"id": run_id},
        ).one()
        assert run_row.cancelled_by_user_id is None
        assert run_row.cancelled_at is None


def test_s13_form_schema_stores_jsonb(alembic_cfg: Config) -> None:
    """form_schema accepts and returns structured JSONB payloads."""
    command.upgrade(alembic_cfg, S13_REV)

    schema_payload = (
        '{"fields": [{"name": "branch", "label": "Branch", '
        '"kind": "string", "required": true}]}'
    )

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="jsonb")
        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (
                    id, team_id, name, scope, system_owned, form_schema
                )
                VALUES (
                    :id, :team_id, 'jsonb-wf', 'user', FALSE,
                    CAST(:fs AS JSONB)
                )
                """
            ),
            {"id": wf_id, "team_id": team_id, "fs": schema_payload},
        )
        session.commit()

        row = session.execute(
            text("SELECT form_schema FROM workflows WHERE id = :id"),
            {"id": wf_id},
        ).one()
        assert row.form_schema == {
            "fields": [
                {"name": "branch", "label": "Branch", "kind": "string", "required": True}
            ]
        }


def test_s13_target_user_id_fk_set_null_on_user_delete(
    alembic_cfg: Config,
) -> None:
    """Deleting the referenced user sets workflows.target_user_id to NULL
    (SET NULL — the workflow row survives)."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="fk-tu")
        user_id = _make_user(session)

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (
                    id, team_id, name, scope, system_owned, target_user_id
                )
                VALUES (:id, :team_id, 'fk-wf', 'user', FALSE, :uid)
                """
            ),
            {"id": wf_id, "team_id": team_id, "uid": user_id},
        )
        session.commit()

    # Delete the user — should cascade FK to SET NULL, not delete the workflow.
    with Session(engine) as session:
        session.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
        session.commit()

    with Session(engine) as session:
        row = session.execute(
            text("SELECT target_user_id FROM workflows WHERE id = :id"),
            {"id": wf_id},
        ).one()
        assert row.target_user_id is None, (
            "target_user_id should be NULL after user deletion (SET NULL), "
            f"got {row.target_user_id}"
        )


def test_s13_cancelled_by_fk_set_null_on_user_delete(
    alembic_cfg: Config,
) -> None:
    """Deleting the cancelling user sets workflow_runs.cancelled_by_user_id
    to NULL — the run audit row survives."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="fk-cb")
        user_id = _make_user(session)
        wf_id = _make_workflow(session, team_id=team_id, name="cancel-fk-wf")

        run_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflow_runs (
                    id, workflow_id, team_id, trigger_type,
                    trigger_payload, status,
                    cancelled_by_user_id, cancelled_at
                )
                VALUES (
                    :id, :wf, :team, 'button',
                    '{}'::jsonb, 'cancelled',
                    :uid, NOW()
                )
                """
            ),
            {"id": run_id, "wf": wf_id, "team": team_id, "uid": user_id},
        )
        session.commit()

    with Session(engine) as session:
        session.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
        session.commit()

    with Session(engine) as session:
        row = session.execute(
            text(
                "SELECT cancelled_by_user_id FROM workflow_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).one()
        assert row.cancelled_by_user_id is None, (
            "cancelled_by_user_id should be NULL after user deletion, "
            f"got {row.cancelled_by_user_id}"
        )


def test_s13_target_container_check_rejects_unknown_value(
    alembic_cfg: Config,
) -> None:
    """INSERT with an unknown target_container value must raise IntegrityError."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="ck-bad")
        wf_id = _make_workflow(session, team_id=team_id, name="ck-bad-wf")

        with pytest.raises(sa_exc.IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_steps (
                        id, workflow_id, step_index, action, config, target_container
                    )
                    VALUES (
                        :id, :wf, 0, 'shell', '{}'::jsonb, 'bad_container'
                    )
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id},
            )
            session.commit()


def test_s13_target_container_check_accepts_valid_values(
    alembic_cfg: Config,
) -> None:
    """Both 'user_workspace' and 'team_mirror' pass the CHECK constraint."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="ck-ok")
        wf_id = _make_workflow(session, team_id=team_id, name="ck-ok-wf")

        for idx, container in enumerate(["user_workspace", "team_mirror"]):
            session.execute(
                text(
                    """
                    INSERT INTO workflow_steps (
                        id, workflow_id, step_index, action, config, target_container
                    )
                    VALUES (
                        :id, :wf, :si, 'shell', '{}'::jsonb, :tc
                    )
                    """
                ),
                {"id": uuid.uuid4(), "wf": wf_id, "si": idx, "tc": container},
            )
        session.commit()

        rows = session.execute(
            text(
                """
                SELECT target_container FROM workflow_steps
                WHERE workflow_id = :wf ORDER BY step_index
                """
            ),
            {"wf": wf_id},
        ).all()
        assert [r[0] for r in rows] == ["user_workspace", "team_mirror"]


def test_s13_round_robin_cursor_is_bigint_and_updatable(
    alembic_cfg: Config,
) -> None:
    """round_robin_cursor is BIGINT: it accepts large monotonic values."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="rrc")
        wf_id = _make_workflow(session, team_id=team_id, name="rrc-wf")

        large_cursor = 2**32 + 7  # > INT max, within BIGINT range
        session.execute(
            text(
                "UPDATE workflows SET round_robin_cursor = :c WHERE id = :id"
            ),
            {"c": large_cursor, "id": wf_id},
        )
        session.commit()

        row = session.execute(
            text("SELECT round_robin_cursor FROM workflows WHERE id = :id"),
            {"id": wf_id},
        ).one()
        assert row.round_robin_cursor == large_cursor


def test_s13_workflow_not_deleted_when_target_user_deleted(
    alembic_cfg: Config,
) -> None:
    """User deletion must NOT cascade-delete the workflow row (SET NULL, not CASCADE)."""
    command.upgrade(alembic_cfg, S13_REV)

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="nd")
        user_id = _make_user(session)

        wf_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO workflows (
                    id, team_id, name, scope, system_owned, target_user_id
                )
                VALUES (:id, :team_id, 'nd-wf', 'user', FALSE, :uid)
                """
            ),
            {"id": wf_id, "team_id": team_id, "uid": user_id},
        )
        session.commit()

    with Session(engine) as session:
        session.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
        session.commit()

    with Session(engine) as session:
        count = session.execute(
            text("SELECT COUNT(*) FROM workflows WHERE id = :id"), {"id": wf_id}
        ).scalar()
        assert count == 1, (
            "workflow row must survive user deletion (FK is SET NULL, not CASCADE)"
        )


def test_s13_downgrade_removes_all_new_columns(alembic_cfg: Config) -> None:
    """Downgrading to s12 removes all six new columns and their constraints."""
    command.upgrade(alembic_cfg, S13_REV)
    command.downgrade(alembic_cfg, S12_REV)

    with Session(engine) as session:
        # workflows must NOT have the three new columns.
        wf_cols = [
            r[0]
            for r in session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'workflows'
                    """
                )
            ).all()
        ]
        for col in ("form_schema", "target_user_id", "round_robin_cursor"):
            assert col not in wf_cols, (
                f"column '{col}' should not exist in workflows after downgrade"
            )

        # workflow_steps must NOT have target_container.
        step_cols = [
            r[0]
            for r in session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'workflow_steps'
                    """
                )
            ).all()
        ]
        assert "target_container" not in step_cols

        # workflow_runs must NOT have the two cancellation columns.
        run_cols = [
            r[0]
            for r in session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'workflow_runs'
                    """
                )
            ).all()
        ]
        for col in ("cancelled_by_user_id", "cancelled_at"):
            assert col not in run_cols, (
                f"column '{col}' should not exist in workflow_runs after downgrade"
            )

        # The target_container CHECK constraint must also be gone.
        ck_rows = session.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = 'workflow_steps'
                  AND constraint_type = 'CHECK'
                  AND constraint_name = 'ck_workflow_steps_target_container'
                """
            )
        ).all()
        assert len(ck_rows) == 0, (
            "ck_workflow_steps_target_container CHECK should be removed after downgrade"
        )
