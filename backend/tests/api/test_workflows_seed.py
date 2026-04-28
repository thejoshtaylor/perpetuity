"""Unit tests for `app.api.workflows_seed.seed_system_workflows` (M005/S02/T02).

Covers the helper that auto-seeds ``_direct_claude`` + ``_direct_codex``
system workflows for every newly-created team (D028). Both runtime
team-create code paths (``crud.create_team_with_admin`` for non-personal
teams, ``crud.create_user_with_personal_team`` for the signup flow) call
this helper before the final commit; the s12 migration calls the same
shape against existing teams.

Test isolation:
  * autouse `_clean_workflows` deletes every workflow_steps + workflows
    row at setup and teardown. The session-scoped `db` fixture is shared
    across tests, so without this each test would inherit prior writes.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app import crud
from app.api.workflows_seed import SYSTEM_WORKFLOWS, seed_system_workflows
from app.models import Team, UserCreate


@pytest.fixture(autouse=True)
def _clean_workflows(db: Session) -> Generator[None, None, None]:
    """Wipe workflow rows before AND after each test.

    workflow_steps cascades on workflow delete (FK ON DELETE CASCADE) so
    a single DELETE on workflows is enough.
    """
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()
    yield
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()


def _make_team(db: Session, *, slug_suffix: str) -> uuid.UUID:
    team_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO team (id, name, slug, is_personal, created_at)
            VALUES (:id, :name, :slug, FALSE, NOW())
            """
        ),
        {
            "id": team_id,
            "name": f"seed-{slug_suffix}",
            "slug": f"seed-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        },
    )
    db.commit()
    return team_id


def test_seed_inserts_two_system_workflows(db: Session) -> None:
    """Fresh team → 2 workflows (claude + codex), each with 1 step at index 0,
    each system_owned=TRUE, scope='user', ``prompt_template={prompt}``."""
    team_id = _make_team(db, slug_suffix="fresh")

    inserted = seed_system_workflows(db, team_id)
    db.commit()
    assert inserted == 2, f"expected 2 fresh inserts, got {inserted}"

    rows = db.execute(
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
    assert [r[0] for r in rows] == ["_direct_claude", "_direct_codex"]
    for _, scope, system_owned in rows:
        assert scope == "user"
        assert system_owned is True

    # Each workflow has exactly one step at index 0 with the right action.
    steps = db.execute(
        text(
            """
            SELECT w.name, ws.step_index, ws.action, ws.config
            FROM workflows w
            JOIN workflow_steps ws ON ws.workflow_id = w.id
            WHERE w.team_id = :t
            ORDER BY w.name, ws.step_index
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


def test_seed_is_idempotent(db: Session) -> None:
    """Re-seeding an already-seeded team is a no-op (returns 0, no duplicate
    rows, no UNIQUE violation).
    """
    team_id = _make_team(db, slug_suffix="idem")
    inserted_first = seed_system_workflows(db, team_id)
    db.commit()
    assert inserted_first == 2

    inserted_second = seed_system_workflows(db, team_id)
    db.commit()
    assert inserted_second == 0, (
        "re-seed must be a no-op once both names exist (ON CONFLICT DO NOTHING)"
    )

    # Still exactly two workflow rows + two step rows for this team.
    wf_count = db.execute(
        text("SELECT COUNT(*) FROM workflows WHERE team_id = :t"),
        {"t": team_id},
    ).scalar_one()
    step_count = db.execute(
        text(
            """
            SELECT COUNT(*) FROM workflow_steps ws
            JOIN workflows w ON w.id = ws.workflow_id
            WHERE w.team_id = :t
            """
        ),
        {"t": team_id},
    ).scalar_one()
    assert wf_count == 2
    assert step_count == 2


def test_seed_partial_recovery(db: Session) -> None:
    """If a team already has _direct_claude but not _direct_codex (e.g. a
    prior partial-seed crash), re-seeding adds only the missing row."""
    team_id = _make_team(db, slug_suffix="partial")

    # Pre-seed only _direct_claude using the same path the helper would.
    claude_only = [w for w in SYSTEM_WORKFLOWS if w["name"] == "_direct_claude"]
    wf_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name, description, scope, system_owned)
            VALUES (:id, :team_id, :name, :description, :scope, TRUE)
            """
        ),
        {
            "id": wf_id,
            "team_id": team_id,
            "name": claude_only[0]["name"],
            "description": claude_only[0]["description"],
            "scope": claude_only[0]["scope"],
        },
    )
    db.execute(
        text(
            """
            INSERT INTO workflow_steps (id, workflow_id, step_index, action, config)
            VALUES (:id, :wf, 0, 'claude', CAST(:config AS JSONB))
            """
        ),
        {
            "id": uuid.uuid4(),
            "wf": wf_id,
            "config": '{"prompt_template":"{prompt}"}',
        },
    )
    db.commit()

    inserted = seed_system_workflows(db, team_id)
    db.commit()
    assert inserted == 1, "only _direct_codex should be missing"

    names = {
        row[0]
        for row in db.execute(
            text("SELECT name FROM workflows WHERE team_id = :t"),
            {"t": team_id},
        ).all()
    }
    assert names == {"_direct_claude", "_direct_codex"}


def test_seed_isolated_per_team(db: Session) -> None:
    """Seeding team A does NOT touch team B's workflow rows."""
    team_a = _make_team(db, slug_suffix="iso-a")
    team_b = _make_team(db, slug_suffix="iso-b")

    seed_system_workflows(db, team_a)
    db.commit()

    a_count = db.execute(
        text("SELECT COUNT(*) FROM workflows WHERE team_id = :t"),
        {"t": team_a},
    ).scalar_one()
    b_count = db.execute(
        text("SELECT COUNT(*) FROM workflows WHERE team_id = :t"),
        {"t": team_b},
    ).scalar_one()
    assert a_count == 2
    assert b_count == 0, "team B should be untouched until seeded explicitly"


def test_create_team_with_admin_seeds_workflows(db: Session) -> None:
    """The runtime team-create path runs the seed inside the same
    transaction as the team + admin membership (so a seed failure rolls
    everything back). After ``crud.create_team_with_admin`` returns, the
    new team must have both _direct_* workflows ready to fire.
    """
    # Need a real user as the admin. Reuse the personal-team helper to
    # avoid hand-rolling the User shape.
    user_create = UserCreate(
        email=f"wf-seed-{uuid.uuid4().hex[:8]}@test.local",
        password="password-not-checked-here",
        full_name="Workflow Seed Test",
    )
    user, _personal = crud.create_user_with_personal_team(
        session=db, user_create=user_create, raise_http_on_duplicate=False
    )

    # Personal team also got seeded — verify and then exercise the
    # non-personal path too.
    personal_count = db.execute(
        text(
            """
            SELECT COUNT(*) FROM workflows
            WHERE team_id = (
                SELECT team_id FROM team_member WHERE user_id = :u LIMIT 1
            )
            """
        ),
        {"u": user.id},
    ).scalar_one()
    assert personal_count == 2, (
        "create_user_with_personal_team must seed system workflows"
    )

    team = crud.create_team_with_admin(
        session=db, name="Workflow Seed Test", creator_id=user.id
    )

    rows = db.execute(
        text(
            """
            SELECT name, system_owned FROM workflows
            WHERE team_id = :t ORDER BY name
            """
        ),
        {"t": team.id},
    ).all()
    assert [r[0] for r in rows] == ["_direct_claude", "_direct_codex"]
    assert all(r[1] is True for r in rows)


def test_seed_returns_int_count(db: Session) -> None:
    """Helper return value matches the public contract — number of newly
    inserted workflow rows (not steps, not totals)."""
    team_id = _make_team(db, slug_suffix="count")
    n = seed_system_workflows(db, team_id)
    db.commit()
    assert isinstance(n, int)
    assert n == len(SYSTEM_WORKFLOWS)
