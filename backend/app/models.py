import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import EmailStr
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    user = "user"
    system_admin = "system_admin"


class TeamRole(str, enum.Enum):
    member = "member"
    admin = "admin"


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    role: UserRole = Field(default=UserRole.user)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)
    team_memberships: list["TeamMember"] = Relationship(
        back_populates="user", cascade_delete=True
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Team: user or personal workspace owner. Personal teams (is_personal=True)
# are auto-created 1:1 with users and cannot be invited to (S02 invite stub).
class Team(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, unique=True, index=True)
    is_personal: bool = Field(default=False, nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    members: list["TeamMember"] = Relationship(
        back_populates="team", cascade_delete=True
    )


class TeamPublic(SQLModel):
    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool
    created_at: datetime | None = None


class TeamCreate(SQLModel):
    name: str = Field(min_length=1, max_length=255)


class TeamWithRole(SQLModel):
    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool
    created_at: datetime | None = None
    role: TeamRole


# Join table — user ↔ team with per-membership role
class TeamMember(SQLModel, table=True):
    __tablename__ = "team_member"
    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_team_member_user_team"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE", index=True
    )
    role: TeamRole = Field(default=TeamRole.member)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    user: User | None = Relationship(back_populates="team_memberships")
    team: Team | None = Relationship(back_populates="members")


# Invite: bearer-token invitation to join a team. Code is a urlsafe string
# (32 chars ≈ 190 bits entropy). One-shot: used_at + used_by are stamped on
# acceptance; a future agent can audit issuance/acceptance via this row.
class TeamInvite(SQLModel, table=True):
    __tablename__ = "team_invite"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(max_length=64, unique=True, index=True)
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE", index=True
    )
    created_by: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))  # type: ignore
    used_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), nullable=True  # type: ignore
    )
    used_by: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Workspace volume: per-(user, team) loopback-ext4 .img backing for the
# workspace container's bind-mount. One row per (user, team) — the
# uq_workspace_volume_user_team constraint is the D004/MEM004 invariant.
# size_gb is the effective per-volume cap (the kernel-enforced ext4 size of
# the .img). img_path is the absolute host-side path to the .img file
# (uuid-keyed by construction so it never embeds PII).
class WorkspaceVolume(SQLModel, table=True):
    __tablename__ = "workspace_volume"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "team_id", name="uq_workspace_volume_user_team"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE", index=True
    )
    size_gb: int = Field(nullable=False)
    img_path: str = Field(max_length=512, nullable=False, unique=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# System-wide admin-tunable settings (D015). Generic key/value store backing
# the admin settings API. The canonical first key is
# `workspace_volume_size_gb`, which the orchestrator looks up on each fresh
# `create_volume` call to pick the new-volume cap. JSONB so future keys can
# carry richer payloads; per-key validators in the API layer enforce shape.
class SystemSetting(SQLModel, table=True):
    __tablename__ = "system_settings"

    key: str = Field(max_length=255, primary_key=True)
    value: Any | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    value_encrypted: bytes | None = Field(default=None, nullable=True)
    sensitive: bool = Field(default=False, nullable=False)
    has_value: bool = Field(default=False, nullable=False)
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class SystemSettingPublic(SQLModel):
    key: str
    sensitive: bool
    has_value: bool
    value: Any | None = None
    updated_at: datetime | None = None


class SystemSettingPut(SQLModel):
    value: Any


class SystemSettingShrinkWarning(SQLModel):
    user_id: uuid.UUID
    team_id: uuid.UUID
    size_gb: int
    usage_bytes: int | None = None


class SystemSettingPutResponse(SQLModel):
    key: str
    value: Any
    updated_at: datetime | None = None
    warnings: list[SystemSettingShrinkWarning] = []


class SystemSettingGenerateResponse(SQLModel):
    key: str
    value: str
    has_value: bool = True
    generated: bool = True
    updated_at: datetime | None = None


# Per-team GitHub App installation. After a team admin walks through the
# GitHub App install handshake, the install-callback persists one row here
# scoped to the originating team. The orchestrator looks up by team_id when
# minting installation tokens. installation_id is BIGINT because GitHub
# installation ids are int64; UNIQUE because the same installation can only
# be claimed by one team at a time.
class GitHubAppInstallation(SQLModel, table=True):
    __tablename__ = "github_app_installations"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", name="uq_github_app_installations_installation_id"
        ),
        CheckConstraint(
            "account_type IN ('Organization', 'User')",
            name="ck_github_app_installations_account_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE"
    )
    installation_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    account_login: str = Field(max_length=255, nullable=False)
    account_type: str = Field(max_length=64, nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class GitHubAppInstallationPublic(SQLModel):
    id: uuid.UUID
    team_id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    created_at: datetime | None = None


# Per-team mirror container state-of-record. One row per team (UNIQUE on
# team_id). The row outlives any individual container — `container_id` goes
# NULL after a reap; `volume_path` stays put so the next ensure remounts
# the same /repos. `last_started_at` / `last_idle_at` drive the reaper:
# `always_on=true` suppresses reap entirely. `volume_path` is uuid-keyed
# by construction in the orchestrator (no PII).
class TeamMirrorVolume(SQLModel, table=True):
    __tablename__ = "team_mirror_volumes"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_team_mirror_volumes_team_id"),
        UniqueConstraint(
            "volume_path", name="uq_team_mirror_volumes_volume_path"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE"
    )
    volume_path: str = Field(max_length=512, nullable=False)
    container_id: str | None = Field(
        default=None, max_length=64, nullable=True
    )
    last_started_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), nullable=True  # type: ignore
    )
    last_idle_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True), nullable=True  # type: ignore
    )
    always_on: bool = Field(default=False, nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class TeamMirrorVolumePublic(SQLModel):
    id: uuid.UUID
    team_id: uuid.UUID
    volume_path: str
    container_id: str | None = None
    last_started_at: datetime | None = None
    last_idle_at: datetime | None = None
    always_on: bool
    created_at: datetime | None = None


class TeamMirrorPatch(SQLModel):
    """PATCH body for /api/v1/teams/{id}/mirror — only `always_on` toggles today."""

    always_on: bool


# Per-team GitHub-linked project (M004/S04). One row per (team, project name).
# `installation_id` references `github_app_installations.installation_id`
# (BIGINT) — RESTRICT so an admin cannot silently orphan projects by deleting
# the install row first. `last_push_status`/`last_push_error` are the
# auto-push outcome surface; populated by the orchestrator's auto-push
# executor in T04 — NULL between pushes / before the first push.
class Project(SQLModel, table=True):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_projects_team_id_name"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE"
    )
    installation_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey(
                "github_app_installations.installation_id",
                name="fk_projects_installation_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        )
    )
    github_repo_full_name: str = Field(max_length=512, nullable=False)
    name: str = Field(min_length=1, max_length=255, nullable=False)
    last_push_status: str | None = Field(
        default=None, max_length=32, nullable=True
    )
    last_push_error: str | None = Field(default=None, nullable=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class ProjectPublic(SQLModel):
    id: uuid.UUID
    team_id: uuid.UUID
    installation_id: int
    github_repo_full_name: str
    name: str
    last_push_status: str | None = None
    last_push_error: str | None = None
    created_at: datetime | None = None


class ProjectsPublic(SQLModel):
    data: list[ProjectPublic]
    count: int


class ProjectCreate(SQLModel):
    installation_id: int = Field(ge=1)
    github_repo_full_name: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=255)


class ProjectUpdate(SQLModel):
    """PATCH body for /api/v1/projects/{id} — name only today."""

    name: str = Field(min_length=1, max_length=255)


# Per-project push-back rule. 1:1 with `projects` (PK == FK). Three modes:
#   - auto             : every user push is auto-pushed to GitHub (T04 wires
#                        the executor + post-receive hook)
#   - rule             : selective push by branch_pattern (M005); the rule is
#                        stored but inert in M004
#   - manual_workflow  : user runs a GitHub Actions workflow by id (M005);
#                        also stored but inert in M004
# branch_pattern is required for mode=rule; workflow_id is required for
# mode=manual_workflow. Both are NULL for mode=auto. The CHECK constraint on
# `mode` is enforced at the DB layer; field-shape validation is enforced at
# the API layer (mode-specific 422 detail).
class ProjectPushRule(SQLModel, table=True):
    __tablename__ = "project_push_rules"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('auto', 'rule', 'manual_workflow')",
            name="ck_project_push_rules_mode",
        ),
    )

    project_id: uuid.UUID = Field(
        foreign_key="projects.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    mode: str = Field(max_length=32, nullable=False)
    branch_pattern: str | None = Field(
        default=None, max_length=255, nullable=True
    )
    workflow_id: str | None = Field(
        default=None, max_length=255, nullable=True
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class ProjectPushRulePublic(SQLModel):
    project_id: uuid.UUID
    mode: str
    branch_pattern: str | None = None
    workflow_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectPushRulePut(SQLModel):
    """PUT body for /api/v1/projects/{id}/push-rule.

    Mode-specific field validation runs in the route — the model-level shape
    only enforces the str-typing of the fields. The route returns 422 with a
    field-specific detail for mismatches (e.g. mode=rule without
    branch_pattern).
    """

    mode: str = Field(min_length=1, max_length=32)
    branch_pattern: str | None = Field(default=None, max_length=255)
    workflow_id: str | None = Field(default=None, max_length=255)


# Wire-shapes for the M004/S02 install handshake. Kept colocated with the
# row model so the API surface and persistence shape evolve together.
class InstallUrlResponse(SQLModel):
    install_url: str
    state: str
    expires_at: datetime


class InstallCallbackBody(SQLModel):
    installation_id: int = Field(ge=1)
    setup_action: str = Field(max_length=64)
    state: str = Field(min_length=1)


class InstallationsList(SQLModel):
    data: list[GitHubAppInstallationPublic]
    count: int


class TeamInvitePublic(SQLModel):
    id: uuid.UUID
    code: str
    team_id: uuid.UUID
    created_by: uuid.UUID
    expires_at: datetime
    used_at: datetime | None = None
    used_by: uuid.UUID | None = None
    created_at: datetime | None = None


class InviteIssued(SQLModel):
    code: str
    url: str
    expires_at: datetime


class MemberRoleUpdate(SQLModel):
    role: TeamRole


class TeamMemberPublic(SQLModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: TeamRole


class TeamMembersPublic(SQLModel):
    data: list[TeamMemberPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
