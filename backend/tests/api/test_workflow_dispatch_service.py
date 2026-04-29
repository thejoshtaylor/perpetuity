"""Unit tests for `app.services.workflow_dispatch.resolve_target_user`.

Coverage:
  * scope='user' → always returns triggering user, None fallback reason
  * scope='team' (team_specific) happy path → returns workflow.target_user_id
  * scope='team' with NULL target_user_id → raises TargetUserNoMembershipError
  * scope='team' with non-member target → raises TargetUserNoMembershipError
  * scope='round_robin' picks cursor-indexed member + increments cursor
  * scope='round_robin' wraps cursor at len(members)
  * scope='round_robin' all members offline → falls back to triggering user
  * scope='round_robin' some members offline → skips offline, picks online
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import Team, TeamMember, WorkflowScope
from app.services.workflow_dispatch import (
    TargetUserNoMembershipError,
    resolve_target_user,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_tables(db: Session) -> Generator[None, None, None]:
    db.rollback()
    db.execute(text("DELETE FROM workspace_volume WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM team_member WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM workflows WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM team WHERE name LIKE 'dispatch-test-%'"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("DELETE FROM workspace_volume WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM team_member WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM workflows WHERE team_id IN (SELECT id FROM team WHERE name LIKE 'dispatch-test-%')"))
    db.execute(text("DELETE FROM team WHERE name LIKE 'dispatch-test-%'"))
    db.commit()


def _make_team(db: Session) -> Team:
    suffix = uuid.uuid4().hex[:8]
    team = Team(name=f"dispatch-test-{suffix}", slug=f"dispatch-test-{suffix}")
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _add_member(db: Session, team_id: uuid.UUID) -> uuid.UUID:
    from app.models import User
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"dispatch-{suffix}@test.example",
        hashed_password="x",
        full_name=f"Test {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    tm = TeamMember(user_id=user.id, team_id=team_id)
    db.add(tm)
    db.commit()
    return user.id


def _provision_workspace(db: Session, user_id: uuid.UUID, team_id: uuid.UUID) -> None:
    img = f"/data/{uuid.uuid4()}.img"
    db.execute(
        text(
            "INSERT INTO workspace_volume (id, user_id, team_id, size_gb, img_path, created_at) "
            "VALUES (:id, :uid, :tid, 10, :img, now())"
        ).bindparams(id=uuid.uuid4(), uid=user_id, tid=team_id, img=img)
    )
    db.commit()


class _FakeWorkflow:
    """Stand-in for app.models.Workflow — avoids DB insert for simple tests."""

    def __init__(
        self,
        team_id: uuid.UUID,
        scope: str,
        target_user_id: uuid.UUID | None = None,
        round_robin_cursor: int = 0,
        workflow_id: uuid.UUID | None = None,
    ) -> None:
        self.id = workflow_id or uuid.uuid4()
        self.team_id = team_id
        self.scope = scope
        self.target_user_id = target_user_id
        self.round_robin_cursor = round_robin_cursor


def _insert_workflow(db: Session, team_id: uuid.UUID, cursor: int = 0) -> uuid.UUID:
    wid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflows "
            "(id, team_id, name, scope, system_owned, form_schema, round_robin_cursor, created_at, updated_at) "
            "VALUES (:id, :tid, :name, 'round_robin', false, '{}'::jsonb, :cursor, now(), now())"
        ).bindparams(id=wid, tid=team_id, name=f"wf-{wid.hex[:6]}", cursor=cursor)
    )
    db.commit()
    return wid


# ─── scope='user' ─────────────────────────────────────────────────────────────


def test_user_scope_returns_triggering_user(db: Session) -> None:
    team = _make_team(db)
    triggering = uuid.uuid4()
    wf = _FakeWorkflow(team_id=team.id, scope="user")
    result_uid, reason = resolve_target_user(db, wf, triggering)
    assert result_uid == triggering
    assert reason is None


def test_user_scope_enum_returns_triggering_user(db: Session) -> None:
    team = _make_team(db)
    triggering = uuid.uuid4()
    wf = _FakeWorkflow(team_id=team.id, scope=WorkflowScope.user)
    result_uid, reason = resolve_target_user(db, wf, triggering)
    assert result_uid == triggering
    assert reason is None


# ─── scope='team' (team_specific) ────────────────────────────────────────────


def test_team_specific_happy_path(db: Session) -> None:
    team = _make_team(db)
    target = _add_member(db, team.id)
    triggering = _add_member(db, team.id)
    wf = _FakeWorkflow(team_id=team.id, scope="team", target_user_id=target)
    result_uid, reason = resolve_target_user(db, wf, triggering)
    assert result_uid == target
    assert reason is None


def test_team_specific_null_target_raises(db: Session) -> None:
    team = _make_team(db)
    triggering = uuid.uuid4()
    wf = _FakeWorkflow(team_id=team.id, scope="team", target_user_id=None)
    with pytest.raises(TargetUserNoMembershipError) as exc_info:
        resolve_target_user(db, wf, triggering)
    assert exc_info.value.workflow_id == wf.id
    assert exc_info.value.target_user_id is None


def test_team_specific_non_member_target_raises(db: Session) -> None:
    team = _make_team(db)
    # Create a user but do NOT add them to the team
    from app.models import User
    suffix = uuid.uuid4().hex[:8]
    non_member = User(
        email=f"nonmember-{suffix}@test.example",
        hashed_password="x",
        full_name="Non Member",
    )
    db.add(non_member)
    db.commit()
    db.refresh(non_member)

    triggering = uuid.uuid4()
    wf = _FakeWorkflow(team_id=team.id, scope="team", target_user_id=non_member.id)
    with pytest.raises(TargetUserNoMembershipError) as exc_info:
        resolve_target_user(db, wf, triggering)
    assert exc_info.value.target_user_id == non_member.id


def test_team_specific_enum_scope(db: Session) -> None:
    team = _make_team(db)
    target = _add_member(db, team.id)
    triggering = _add_member(db, team.id)
    wf = _FakeWorkflow(team_id=team.id, scope=WorkflowScope.team, target_user_id=target)
    result_uid, reason = resolve_target_user(db, wf, triggering)
    assert result_uid == target


# ─── scope='round_robin' ─────────────────────────────────────────────────────


def test_round_robin_picks_cursor_member_and_increments(db: Session) -> None:
    team = _make_team(db)
    m1 = _add_member(db, team.id)
    m2 = _add_member(db, team.id)
    _provision_workspace(db, m1, team.id)
    _provision_workspace(db, m2, team.id)
    wid = _insert_workflow(db, team.id, cursor=0)

    # Use real workflow object from DB so atomic UPDATE works
    from app.models import Workflow
    wf = db.get(Workflow, wid)
    assert wf is not None
    triggering = uuid.uuid4()
    result_uid, reason = resolve_target_user(db, wf, triggering)

    # cursor was 0 → picks members[0]
    members = db.exec(
        __import__("sqlmodel").select(TeamMember.user_id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    ).all()
    assert result_uid == members[0]
    assert reason is None

    # cursor should have been incremented
    db.refresh(wf)
    assert wf.round_robin_cursor == 1


def test_round_robin_wraps_at_member_count(db: Session) -> None:
    team = _make_team(db)
    m1 = _add_member(db, team.id)
    m2 = _add_member(db, team.id)
    _provision_workspace(db, m1, team.id)
    _provision_workspace(db, m2, team.id)
    # Start cursor at 2 (== len(members)) → should wrap to index 0
    wid = _insert_workflow(db, team.id, cursor=2)

    from app.models import Workflow
    wf = db.get(Workflow, wid)
    assert wf is not None
    triggering = uuid.uuid4()
    result_uid, reason = resolve_target_user(db, wf, triggering)

    members = db.exec(
        __import__("sqlmodel").select(TeamMember.user_id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    ).all()
    # cursor=2, n=2 → 2 % 2 = 0 → picks members[0]
    assert result_uid == members[0]
    assert reason is None


def test_round_robin_all_offline_falls_back_to_triggering(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    team = _make_team(db)
    _add_member(db, team.id)
    _add_member(db, team.id)
    # No workspace_volume rows → all "offline"
    wid = _insert_workflow(db, team.id, cursor=0)

    from app.models import Workflow
    wf = db.get(Workflow, wid)
    assert wf is not None
    triggering = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="app.services.workflow_dispatch"):
        result_uid, reason = resolve_target_user(db, wf, triggering, run_id=uuid.uuid4())

    assert result_uid == triggering
    assert reason == "no_live_workspace"
    assert "workflow_dispatch_fallback" in caplog.text
    assert "no_live_workspace" in caplog.text


def test_round_robin_some_offline_skips_them(db: Session) -> None:
    team = _make_team(db)
    m1 = _add_member(db, team.id)
    m2 = _add_member(db, team.id)
    # Only m2 has a live workspace
    _provision_workspace(db, m2, team.id)
    wid = _insert_workflow(db, team.id, cursor=0)

    from app.models import Workflow
    wf = db.get(Workflow, wid)
    assert wf is not None
    triggering = uuid.uuid4()
    result_uid, reason = resolve_target_user(db, wf, triggering)

    # cursor=0 → tries m1 (no workspace), then m2 (has workspace)
    members = db.exec(
        __import__("sqlmodel").select(TeamMember.user_id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    ).all()
    assert result_uid == members[1]  # m2 is index 1
    assert reason is None


def test_round_robin_no_members_falls_back(db: Session) -> None:
    team = _make_team(db)
    wid = _insert_workflow(db, team.id, cursor=0)

    from app.models import Workflow
    wf = db.get(Workflow, wid)
    assert wf is not None
    triggering = uuid.uuid4()
    result_uid, reason = resolve_target_user(db, wf, triggering)
    assert result_uid == triggering
    assert reason == "no_live_workspace"
