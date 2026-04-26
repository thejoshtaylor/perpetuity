"""Integration tests for the S06d projects + project_push_rules migration.

Exercises ``s06c_team_mirror_volumes`` ⇄ ``s06d_projects_and_push_rules``
on the real Postgres test DB:

  1. After upgrade (head), assert ``projects`` and ``project_push_rules``
     exist with the expected columns/types/nullability, the PKs land where
     expected, the UNIQUE on ``(team_id, name)`` is in place, the FK on
     ``team_id`` cascades on parent delete, the FK on ``installation_id``
     restricts on delete, and the CHECK constraint pins ``mode`` to
     {auto, rule, manual_workflow}.
  2. Inserting two ``projects`` rows with the same ``(team_id, name)`` MUST
     raise ``IntegrityError`` (UNIQUE).
  3. Inserting ``mode='banana'`` into ``project_push_rules`` MUST raise
     ``IntegrityError`` (CheckViolation).
  4. Deleting the parent team MUST cascade-delete its projects and (via the
     project→push_rule FK CASCADE) their rule rows.
  5. Deleting a project MUST cascade-delete its push_rule row.
  6. Deleting a github_app_installations row that still has projects bound
     to it MUST raise ``IntegrityError`` (FK RESTRICT).
  7. After downgrade to ``s06c_team_mirror_volumes``, both tables are gone.
  8. Downgrade then re-upgrade must leave the schema byte-identical.

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

S06C_REV = "s06c_team_mirror_volumes"
S06D_REV = "s06d_projects_and_push_rules"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend

PROJECTS = "projects"
PUSH_RULES = "project_push_rules"


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
            f"Could not restore DB to head after S06d migration test: {restore_err}"
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
        "projects_columns": _columns(PROJECTS),
        "projects_constraints": _constraints(PROJECTS),
        "projects_fks": _fk_actions(PROJECTS),
        "push_rules_columns": _columns(PUSH_RULES),
        "push_rules_constraints": _constraints(PUSH_RULES),
        "push_rules_fks": _fk_actions(PUSH_RULES),
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


def _make_project(
    session: Session,
    *,
    team_id: uuid.UUID,
    installation_id: int,
    name: str,
    repo: str = "acme/widgets",
) -> uuid.UUID:
    pid = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO projects
                (id, team_id, installation_id, github_repo_full_name, name,
                 created_at)
            VALUES
                (:id, :team, :inst, :repo, :name, NOW())
            """
        ),
        {
            "id": pid,
            "team": team_id,
            "inst": installation_id,
            "repo": repo,
            "name": name,
        },
    )
    return pid


def _make_push_rule(
    session: Session, *, project_id: uuid.UUID, mode: str = "manual_workflow"
) -> None:
    session.execute(
        text(
            """
            INSERT INTO project_push_rules
                (project_id, mode, branch_pattern, workflow_id, created_at,
                 updated_at)
            VALUES (:pid, :mode, NULL, NULL, NOW(), NOW())
            """
        ),
        {"pid": project_id, "mode": mode},
    )


def _truncate() -> None:
    with Session(engine) as session:
        # FK order: push_rules → projects → installations.
        session.execute(text(f"DELETE FROM {PUSH_RULES}"))
        session.execute(text(f"DELETE FROM {PROJECTS}"))
        session.execute(text("DELETE FROM github_app_installations"))
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_s06d_upgrade_creates_projects_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns(PROJECTS)
    assert set(cols) == {
        "id",
        "team_id",
        "installation_id",
        "github_repo_full_name",
        "name",
        "last_push_status",
        "last_push_error",
        "created_at",
    }, f"unexpected projects column set: {sorted(cols)}"

    assert cols["id"]["is_nullable"] == "NO"
    assert cols["team_id"]["is_nullable"] == "NO"
    assert cols["installation_id"]["is_nullable"] == "NO"
    assert cols["github_repo_full_name"]["is_nullable"] == "NO"
    assert cols["name"]["is_nullable"] == "NO"
    assert cols["last_push_status"]["is_nullable"] == "YES"
    assert cols["last_push_error"]["is_nullable"] == "YES"
    assert cols["created_at"]["is_nullable"] == "NO"

    assert cols["id"]["data_type"] == "uuid"
    assert cols["team_id"]["data_type"] == "uuid"
    assert cols["installation_id"]["data_type"] == "bigint"
    assert cols["github_repo_full_name"]["data_type"] in {
        "character varying",
        "varchar",
    }
    assert cols["github_repo_full_name"]["char_max_length"] == 512
    assert cols["name"]["data_type"] in {"character varying", "varchar"}
    assert cols["name"]["char_max_length"] == 255
    assert cols["last_push_status"]["char_max_length"] == 32
    assert cols["last_push_error"]["data_type"] == "text"
    assert cols["created_at"]["data_type"] == "timestamp with time zone"

    constraints = _constraints(PROJECTS)
    pk = [n for n, t in constraints.items() if t == "p"]
    uq = [n for n, t in constraints.items() if t == "u"]
    fk = [n for n, t in constraints.items() if t == "f"]
    assert len(pk) == 1, f"expected one PK on projects, got {pk}"
    assert "uq_projects_team_id_name" in uq, uq
    assert "fk_projects_team_id" in fk, fk
    assert "fk_projects_installation_id" in fk, fk

    fks = _fk_actions(PROJECTS)
    assert fks["fk_projects_team_id"] == "c", (
        f"team FK should ON DELETE CASCADE, got {fks}"
    )
    assert fks["fk_projects_installation_id"] == "r", (
        f"installation FK should ON DELETE RESTRICT, got {fks}"
    )


def test_s06d_upgrade_creates_push_rules_table(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    cols = _columns(PUSH_RULES)
    assert set(cols) == {
        "project_id",
        "mode",
        "branch_pattern",
        "workflow_id",
        "created_at",
        "updated_at",
    }, f"unexpected push_rules column set: {sorted(cols)}"

    assert cols["project_id"]["is_nullable"] == "NO"
    assert cols["mode"]["is_nullable"] == "NO"
    assert cols["branch_pattern"]["is_nullable"] == "YES"
    assert cols["workflow_id"]["is_nullable"] == "YES"
    assert cols["created_at"]["is_nullable"] == "NO"
    assert cols["updated_at"]["is_nullable"] == "NO"

    assert cols["project_id"]["data_type"] == "uuid"
    assert cols["mode"]["char_max_length"] == 32
    assert cols["branch_pattern"]["char_max_length"] == 255
    assert cols["workflow_id"]["char_max_length"] == 255

    constraints = _constraints(PUSH_RULES)
    pk = [n for n, t in constraints.items() if t == "p"]
    fk = [n for n, t in constraints.items() if t == "f"]
    chk = [n for n, t in constraints.items() if t == "c"]
    assert len(pk) == 1, f"expected one PK on push_rules, got {pk}"
    assert "fk_project_push_rules_project_id" in fk
    assert "ck_project_push_rules_mode" in chk

    fks = _fk_actions(PUSH_RULES)
    assert fks["fk_project_push_rules_project_id"] == "c", (
        f"project FK should ON DELETE CASCADE, got {fks}"
    )


def test_s06d_unique_team_id_name(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="dup")
        _make_install(session, team_id=team_id, installation_id=12345)
        session.commit()

        _make_project(
            session, team_id=team_id, installation_id=12345, name="widgets"
        )
        session.commit()

        with pytest.raises(IntegrityError):
            _make_project(
                session,
                team_id=team_id,
                installation_id=12345,
                name="widgets",
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06d_check_constraint_rejects_unknown_mode(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="check")
        _make_install(session, team_id=team_id, installation_id=99)
        session.commit()
        pid = _make_project(
            session,
            team_id=team_id,
            installation_id=99,
            name="checkme",
        )
        session.commit()

        with pytest.raises(IntegrityError):
            _make_push_rule(session, project_id=pid, mode="banana")
            session.commit()
        session.rollback()

    _truncate()


@pytest.mark.parametrize("mode", ["auto", "rule", "manual_workflow"])
def test_s06d_check_constraint_accepts_valid_modes(
    alembic_cfg: Config, mode: str
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix=f"ok-{mode}")
        _make_install(session, team_id=team_id, installation_id=200)
        session.commit()
        pid = _make_project(
            session,
            team_id=team_id,
            installation_id=200,
            name=f"p-{mode}",
        )
        session.commit()
        _make_push_rule(session, project_id=pid, mode=mode)
        session.commit()

        observed = session.execute(
            text(
                "SELECT mode FROM project_push_rules"
                " WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar_one()
        assert observed == mode

    _truncate()


def test_s06d_team_delete_cascades_projects_and_push_rules(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="cascade")
        _make_install(session, team_id=team_id, installation_id=42)
        session.commit()

        pid = _make_project(
            session,
            team_id=team_id,
            installation_id=42,
            name="cascade-me",
        )
        session.commit()
        _make_push_rule(session, project_id=pid)
        session.commit()

        before_proj = session.execute(
            text(f"SELECT COUNT(*) FROM {PROJECTS} WHERE id = :id"),
            {"id": pid},
        ).scalar_one()
        before_rule = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_RULES}"
                " WHERE project_id = :id"
            ),
            {"id": pid},
        ).scalar_one()
        assert before_proj == 1
        assert before_rule == 1

        session.execute(
            text("DELETE FROM team WHERE id = :id"), {"id": team_id}
        )
        session.commit()

        after_proj = session.execute(
            text(f"SELECT COUNT(*) FROM {PROJECTS} WHERE id = :id"),
            {"id": pid},
        ).scalar_one()
        after_rule = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_RULES}"
                " WHERE project_id = :id"
            ),
            {"id": pid},
        ).scalar_one()
        assert after_proj == 0, "project should cascade-delete with team"
        assert after_rule == 0, (
            "push_rule should cascade-delete with project (FK CASCADE chain)"
        )

    _truncate()


def test_s06d_project_delete_cascades_push_rule(
    alembic_cfg: Config,
) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="proj-cascade")
        _make_install(session, team_id=team_id, installation_id=314)
        session.commit()
        pid = _make_project(
            session,
            team_id=team_id,
            installation_id=314,
            name="proj-cascade",
        )
        session.commit()
        _make_push_rule(session, project_id=pid)
        session.commit()

        session.execute(
            text(f"DELETE FROM {PROJECTS} WHERE id = :id"), {"id": pid}
        )
        session.commit()

        after_rule = session.execute(
            text(
                f"SELECT COUNT(*) FROM {PUSH_RULES}"
                " WHERE project_id = :id"
            ),
            {"id": pid},
        ).scalar_one()
        assert after_rule == 0

    _truncate()


def test_s06d_installation_delete_with_projects_is_restricted(
    alembic_cfg: Config,
) -> None:
    """Deleting an installation with bound projects must raise IntegrityError."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    with Session(engine) as session:
        team_id = _make_team(session, slug_suffix="restrict")
        _make_install(session, team_id=team_id, installation_id=8001)
        session.commit()
        _make_project(
            session,
            team_id=team_id,
            installation_id=8001,
            name="restricted",
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "DELETE FROM github_app_installations"
                    " WHERE installation_id = :i"
                ),
                {"i": 8001},
            )
            session.commit()
        session.rollback()

    _truncate()


def test_s06d_downgrade_drops_both_tables(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    _truncate()

    command.downgrade(alembic_cfg, S06C_REV)

    cols_p = _columns(PROJECTS)
    cols_r = _columns(PUSH_RULES)
    assert cols_p == {}, (
        f"{PROJECTS} columns should be empty after downgrade, got {cols_p}"
    )
    assert cols_r == {}, (
        f"{PUSH_RULES} columns should be empty after downgrade, got {cols_r}"
    )
    # autouse fixture restores head.


def test_s06d_round_trip_schema_identical(alembic_cfg: Config) -> None:
    """Downgrade + re-upgrade must leave the schema byte-identical."""
    command.upgrade(alembic_cfg, "head")
    _truncate()

    before = _schema_snapshot()
    assert before["projects_columns"], (
        "precondition: projects table should exist before round-trip"
    )

    command.downgrade(alembic_cfg, S06C_REV)
    command.upgrade(alembic_cfg, S06D_REV)

    after = _schema_snapshot()
    assert after == before, (
        "schema diverged after downgrade+re-upgrade:\n"
        f"before={before}\nafter={after}"
    )
