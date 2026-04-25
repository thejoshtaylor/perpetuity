import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import EmailStr
from sqlalchemy import Column, DateTime, UniqueConstraint
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
    value: Any = Field(sa_column=Column(JSONB, nullable=False))
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class SystemSettingPublic(SQLModel):
    key: str
    value: Any
    updated_at: datetime | None = None


class SystemSettingPut(SQLModel):
    value: Any


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
