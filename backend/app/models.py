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


class VoiceTranscribeResponse(SQLModel):
    text: str


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


# Per-team Fernet-encrypted credentials (M005/S01). One row per
# (team_id, key) registered in `team_secrets_registry`. Ciphertext lives
# in `value_encrypted` and is decrypted only at the call site via
# `get_team_secret(...)` — never serialized back to the UI. Row absence
# is the canonical "not set" state; `has_value` is here for parity with
# `system_settings` so the public status DTO can render without peeking
# at the ciphertext column. FK CASCADE on team delete drops every
# secret with the team — orphan ciphertext is unrecoverable anyway.
class TeamSecret(SQLModel, table=True):
    __tablename__ = "team_secrets"

    team_id: uuid.UUID = Field(
        foreign_key="team.id",
        primary_key=True,
        nullable=False,
        ondelete="CASCADE",
    )
    key: str = Field(max_length=64, primary_key=True, nullable=False)
    value_encrypted: bytes = Field(nullable=False)
    has_value: bool = Field(default=True, nullable=False)
    sensitive: bool = Field(default=True, nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Public DTO for a single team_secret row. Intentionally excludes
# `value_encrypted` — the field is not present on this model at all so
# `TeamSecretPublic.model_validate(row)` cannot accidentally serialize
# the ciphertext.
class TeamSecretPublic(SQLModel):
    team_id: uuid.UUID
    key: str
    has_value: bool
    sensitive: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Status DTO for GET responses. Same shape the registry uses to render
# both "set" and "not set" entries — `updated_at` is None when the row
# does not exist yet. Never carries the value or its prefix.
class TeamSecretStatus(SQLModel):
    key: str
    has_value: bool
    sensitive: bool
    updated_at: datetime | None = None


# PUT body for /api/v1/teams/{team_id}/secrets/{key} — single field so
# the API surface stays narrow and the validator registry owns shape
# checks.
class TeamSecretPut(SQLModel):
    value: str


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


# Verified GitHub webhook deliveries (M004/S05). The route HMAC-verifies the
# request, then INSERT ... ON CONFLICT (delivery_id) DO NOTHING — the UNIQUE
# constraint on `delivery_id` is the storage-layer enforcement of GitHub's
# 24h retry idempotency contract (D025 / MEM229). `installation_id` is
# nullable + FK SET NULL: losing an installation must not destroy the audit
# trail of webhooks already received. `payload` is persisted in full but
# intentionally NOT logged — only `event_type` + `delivery_id` are.
class GitHubWebhookEvent(SQLModel, table=True):
    __tablename__ = "github_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", name="uq_github_webhook_events_delivery_id"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    installation_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey(
                "github_app_installations.installation_id",
                name="fk_github_webhook_events_installation_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    event_type: str = Field(max_length=64, nullable=False)
    delivery_id: str = Field(max_length=64, nullable=False)
    payload: dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False)
    )
    received_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    dispatch_status: str = Field(
        default="noop", max_length=32, nullable=False
    )
    dispatch_error: str | None = Field(default=None, nullable=True)


# Audit trail for rejected webhook deliveries (signature missing or invalid).
# Body is intentionally NOT persisted here — only the metadata needed to
# investigate abuse or misconfiguration. `delivery_id` is nullable because
# GitHub's `X-GitHub-Delivery` header may be absent on a malformed request.
class WebhookRejection(SQLModel, table=True):
    __tablename__ = "webhook_rejections"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    delivery_id: str | None = Field(
        default=None, max_length=64, nullable=True
    )
    signature_present: bool = Field(nullable=False)
    signature_valid: bool = Field(nullable=False)
    source_ip: str = Field(max_length=64, nullable=False)
    received_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Admin-side projection of GitHubWebhookEvent — never include `payload`,
# the request body is sensitive and must not surface in admin UIs.
class GitHubWebhookEventPublic(SQLModel):
    id: uuid.UUID
    installation_id: int | None = None
    event_type: str
    delivery_id: str
    received_at: datetime | None = None
    dispatch_status: str
    dispatch_error: str | None = None


class WebhookRejectionPublic(SQLModel):
    id: uuid.UUID
    delivery_id: str | None = None
    signature_present: bool
    signature_valid: bool
    source_ip: str
    received_at: datetime | None = None


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


# In-app notification substrate (M005/S02). The seven kinds drive both the
# storage CHECK constraints (see migration s07_notifications) and the
# preference-matching key on `notification_preferences.event_type`. Kept as a
# str-Enum so values land as plain strings in JSON / DB and route handlers
# can pass them through `app.core.notify(user_id, kind=...)` without manual
# `.value` conversion. If you add a kind here, add it to the migration's
# CHECK list and to any UI tab on the settings page.
class NotificationKind(str, enum.Enum):
    workflow_run_started = "workflow_run_started"
    workflow_run_succeeded = "workflow_run_succeeded"
    workflow_run_failed = "workflow_run_failed"
    workflow_step_completed = "workflow_step_completed"
    team_invite_accepted = "team_invite_accepted"
    project_created = "project_created"
    system = "system"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('workflow_run_started', 'workflow_run_succeeded', "
            "'workflow_run_failed', 'workflow_step_completed', "
            "'team_invite_accepted', 'project_created', 'system')",
            name="ck_notifications_kind",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    kind: str = Field(max_length=64, nullable=False)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="'{}'::jsonb"),
    )
    read_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    source_team_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="team.id",
        nullable=True,
        ondelete="SET NULL",
    )
    source_project_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="projects.id",
        nullable=True,
        ondelete="SET NULL",
    )
    # NOTE: source_workflow_run_id has NO FK — the workflow_run table does
    # not exist yet. The FK-add is deferred to whichever future slice ships
    # the workflow engine.
    source_workflow_run_id: uuid.UUID | None = Field(
        default=None, nullable=True
    )


class NotificationPublic(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    # Typed as the enum so OpenAPI emits the seven literal values — the
    # frontend client picks them up as a TS string-literal union.
    kind: NotificationKind
    payload: dict[str, Any]
    read_at: datetime | None = None
    created_at: datetime | None = None
    source_team_id: uuid.UUID | None = None
    source_project_id: uuid.UUID | None = None
    source_workflow_run_id: uuid.UUID | None = None


class NotificationsPublic(SQLModel):
    data: list[NotificationPublic]
    count: int


class NotificationPreference(SQLModel, table=True):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('workflow_run_started', "
            "'workflow_run_succeeded', 'workflow_run_failed', "
            "'workflow_step_completed', 'team_invite_accepted', "
            "'project_created', 'system')",
            name="ck_notification_preferences_event_type",
        ),
    )

    # Synthetic ORM PK. Business uniqueness is the COALESCE UNIQUE INDEX in
    # migration s07 on (user_id, COALESCE(workflow_id, zero-uuid),
    # event_type) — Postgres PRIMARY KEY can't wrap a COALESCE expression,
    # so we keep an `id` column for ORM identity and rely on the index for
    # the team-default-vs-override collision contract. Route upserts SELECT
    # by (user_id, workflow_id, event_type) and UPDATE-or-INSERT.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        nullable=False,
        ondelete="CASCADE",
    )
    workflow_id: uuid.UUID | None = Field(default=None, nullable=True)
    event_type: str = Field(max_length=64, nullable=False)
    in_app: bool = Field(default=True, nullable=False)
    push: bool = Field(default=False, nullable=False)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class NotificationPreferencePublic(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    workflow_id: uuid.UUID | None = None
    # Typed as the enum so OpenAPI emits the seven literal values.
    event_type: NotificationKind
    in_app: bool
    push: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationPreferencePut(SQLModel):
    """PUT body for /api/v1/notifications/preferences/{event_type}.

    The ``event_type`` is taken from the URL path; the body only carries
    the channel toggles. The route always upserts the team-default row
    (workflow_id IS NULL) — per-workflow overrides ship in a future slice
    when the workflow detail page lands.
    """

    in_app: bool = True
    push: bool = False


class NotificationUnreadCount(SQLModel):
    count: int


class NotificationReadAllResponse(SQLModel):
    affected: int


class NotificationTestTrigger(SQLModel):
    """POST /api/v1/notifications/test body — system-admin seed trigger.

    Inserts a notification row for the recipient (defaults to the calling
    admin when ``user_id`` is omitted). Used to prove the bell wiring without
    depending on a real invite/project flow. ``kind`` defaults to ``system``;
    M005/S02/T05 widened it to optionally accept any NotificationKind so the
    preferences contract spec can fire a `team_invite_accepted` and assert
    that a preference toggle actually suppresses the in_app insert.
    """

    user_id: uuid.UUID | None = None
    message: str = "System test notification"
    kind: NotificationKind = NotificationKind.system


# Web Push device registration (M005/S03). One row per (user, browser/device)
# — phone + laptop = two rows. ``endpoint`` is the Mozilla / FCM / APNs Web
# URL handed to us by the browser at subscribe time and is treated as opaque
# secret on log surfaces (only sha256[:8] is ever emitted).
# ``keys.{p256dh,auth}`` is the browser-issued ECDH keypair material that
# pywebpush feeds into its message-encryption pipeline. The dispatcher (T04)
# bumps ``last_seen_at`` on each successful delivery, records
# ``last_status_code``, and prunes the row when the upstream returns 410 or
# ``consecutive_failures`` reaches 5.
class PushSubscription(SQLModel, table=True):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "endpoint",
            name="uq_push_subscriptions_user_id_endpoint",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    # ``endpoint`` is stored as TEXT (no max length) — Mozilla Push Service
    # URLs comfortably fit in 255 chars today, but FCM/APNs are free to grow
    # and we never want a DB-level truncation on a subscribe attempt.
    endpoint: str = Field(nullable=False)
    keys: dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    user_agent: str | None = Field(
        default=None, max_length=500, nullable=True
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    last_seen_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    last_status_code: int | None = Field(default=None, nullable=True)
    consecutive_failures: int = Field(default=0, nullable=False)


class PushSubscriptionKeys(SQLModel):
    """Browser-issued ECDH key material for Web Push message encryption.

    Shape mirrors ``PushSubscription.toJSON().keys`` from the W3C Push API.
    Both halves are url-safe-base64 strings; we don't validate base64 at the
    API boundary — pywebpush surfaces a structured error at encrypt time if
    the bytes are malformed.
    """

    p256dh: str = Field(min_length=1, max_length=512)
    auth: str = Field(min_length=1, max_length=512)


class PushSubscriptionCreate(SQLModel):
    """POST /api/v1/push/subscribe body — full T03 wiring lands here.

    Shape matches ``PushSubscription.toJSON()`` from the browser. T01 only
    declares the model so the migration test imports are coherent; the
    subscribe route ships in T03.
    """

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushSubscriptionKeys


class PushSubscriptionDelete(SQLModel):
    """DELETE /api/v1/push/subscribe body — endpoint-only.

    The browser's ``PushSubscription.unsubscribe()`` does not return the
    ``keys`` material, so the unsubscribe path takes only the endpoint URL
    and uses (user_id, endpoint) as the deletion key.
    """

    endpoint: str = Field(min_length=1, max_length=2048)


class PushSubscriptionPublic(SQLModel):
    """Redaction-safe projection of a PushSubscription row.

    NEVER include the raw ``endpoint`` — only ``endpoint_hash`` (the leading
    8 chars of sha256(endpoint)). The hash is enough for the operator UI to
    distinguish two devices and for log-cross-correlation, without leaking
    the push URL itself (which is treated as a bearer-style secret).
    """

    id: uuid.UUID
    endpoint_hash: str
    user_agent: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None


class PushSubscriptionsList(SQLModel):
    data: list[PushSubscriptionPublic]
    count: int


class VapidKeysGenerateResponse(SQLModel):
    """Response for POST /admin/settings/vapid_keys/generate.

    Returns BOTH the public and private VAPID keys exactly once. Subsequent
    admin GETs on either row return the redacted shape (public is plain JSONB
    and remains visible; private has ``value=null, has_value=true``).
    Re-calling the endpoint is intentionally destructive (D025) — every
    existing push subscription becomes unverifiable until devices re-subscribe.
    """

    public_key: str
    private_key: str
    overwrote_existing: bool


class VapidPublicKeyResponse(SQLModel):
    """Response for GET /api/v1/push/vapid_public_key (no auth)."""

    public_key: str


# Workflow registry (M005/S02). One row per (team, name) — composite
# uniqueness is enforced at the storage layer so re-running the system-
# workflow auto-seed is safe and so S03's CRUD cannot silently shadow a
# system workflow. ``system_owned=TRUE`` flags the auto-seeded rows
# (`_direct_claude`, `_direct_codex`) so S03's CRUD UI can filter them
# out of the listing (D028: those workflows are surfaced as dashboard
# buttons, not as editable rows). Scope dictates dispatch shape — S02
# only writes 'user'; 'team' / 'round_robin' land in S03's dispatcher.
class WorkflowScope(str, enum.Enum):
    user = "user"
    team = "team"
    round_robin = "round_robin"


class WorkflowAction(str, enum.Enum):
    claude = "claude"
    codex = "codex"
    shell = "shell"
    git = "git"


# S03: per-step container target. 'team_mirror' is reserved for S04 but the
# column lands in s13 so S04 does not need an ALTER.
class WorkflowStepTargetContainer(str, enum.Enum):
    user_workspace = "user_workspace"
    team_mirror = "team_mirror"


# S03: form-field shape within workflows.form_schema. kind drives the
# input type rendered on the dashboard trigger form.
class WorkflowFormFieldKind(str, enum.Enum):
    string = "string"
    text = "text"
    number = "number"


class WorkflowFormField(SQLModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: WorkflowFormFieldKind = WorkflowFormFieldKind.string
    required: bool = False


class WorkflowFormSchema(SQLModel):
    fields: list[WorkflowFormField] = Field(default_factory=list)


class Workflow(SQLModel, table=True):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "name", name="uq_workflows_team_id_name"
        ),
        CheckConstraint(
            "scope IN ('user', 'team', 'round_robin')",
            name="ck_workflows_scope",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(min_length=1, max_length=255, nullable=False)
    description: str | None = Field(default=None, nullable=True)
    scope: str = Field(default="user", max_length=32, nullable=False)
    system_owned: bool = Field(default=False, nullable=False)
    # S03 additions (s13 migration)
    form_schema: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="'{}'::jsonb"),
    )
    target_user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
    )
    round_robin_cursor: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    # S05 additions (s15 migration) — operational caps enforced at dispatch time
    max_concurrent_runs: int | None = Field(default=None, nullable=True)
    max_runs_per_hour: int | None = Field(default=None, nullable=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Per-workflow step row. step_index is dense within the parent (0..N-1),
# enforced by UNIQUE (workflow_id, step_index). action is the executor-
# dispatch discriminator; CHECK installs the closed set so S03's seed for
# 'shell' / 'git' executors lands without an ALTER. config is the JSONB
# tail — for S02's ``_direct_*`` workflows it is
# ``{"prompt_template": "{prompt}"}``.
class WorkflowStep(SQLModel, table=True):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "step_index",
            name="uq_workflow_steps_workflow_id_step_index",
        ),
        CheckConstraint(
            "action IN ('claude', 'codex', 'shell', 'git')",
            name="ck_workflow_steps_action",
        ),
        CheckConstraint(
            "target_container IN ('user_workspace', 'team_mirror')",
            name="ck_workflow_steps_target_container",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workflow_id: uuid.UUID = Field(
        foreign_key="workflows.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    step_index: int = Field(nullable=False)
    action: str = Field(max_length=64, nullable=False)
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="'{}'::jsonb"),
    )
    # S03 addition (s13 migration)
    target_container: str = Field(
        default="user_workspace", max_length=32, nullable=False
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class WorkflowStepPublic(SQLModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    step_index: int
    # Typed as the enum so OpenAPI emits the four literal values — the
    # frontend client picks them up as a TS string-literal union.
    action: WorkflowAction
    config: dict[str, Any]
    # Typed as the enum so OpenAPI emits the two container literal values.
    target_container: WorkflowStepTargetContainer = WorkflowStepTargetContainer.user_workspace
    created_at: datetime | None = None
    updated_at: datetime | None = None


# S03 CRUD input DTOs. system_owned is always False on CRUD-created rows —
# only the seed helper (workflows_seed.py / s12 migration) writes True.
class WorkflowStepCreate(SQLModel):
    step_index: int = Field(ge=0)
    action: WorkflowAction
    config: dict[str, Any] = Field(default_factory=dict)
    target_container: WorkflowStepTargetContainer = WorkflowStepTargetContainer.user_workspace


class WorkflowCreate(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scope: WorkflowScope = WorkflowScope.user
    target_user_id: uuid.UUID | None = None
    form_schema: WorkflowFormSchema = Field(default_factory=WorkflowFormSchema)
    steps: list[WorkflowStepCreate] = Field(default_factory=list)
    max_concurrent_runs: int | None = None
    max_runs_per_hour: int | None = None


class WorkflowUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    scope: WorkflowScope | None = None
    target_user_id: uuid.UUID | None = None
    form_schema: WorkflowFormSchema | None = None
    steps: list[WorkflowStepCreate] | None = None
    max_concurrent_runs: int | None = None
    max_runs_per_hour: int | None = None


class WorkflowPublic(SQLModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    description: str | None = None
    # Typed as the enum so OpenAPI emits the three literal scope values.
    scope: WorkflowScope
    system_owned: bool
    form_schema: dict[str, Any] = Field(default_factory=dict)
    target_user_id: uuid.UUID | None = None
    round_robin_cursor: int = 0
    max_concurrent_runs: int | None = None
    max_runs_per_hour: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowsPublic(SQLModel):
    data: list[WorkflowPublic]
    count: int


# DTO for the dashboard listing — combines the workflow row with its
# ordered step rows so the frontend can render the action label without a
# second roundtrip. S02 only reads this surface; the multi-step CRUD
# write surface lands in S03.
class WorkflowWithStepsPublic(SQLModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    description: str | None = None
    scope: WorkflowScope
    system_owned: bool
    form_schema: dict[str, Any] = Field(default_factory=dict)
    target_user_id: uuid.UUID | None = None
    round_robin_cursor: int = 0
    max_concurrent_runs: int | None = None
    max_runs_per_hour: int | None = None
    steps: list[WorkflowStepPublic]
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Per-run history. ``status`` lifecycle is pending → running →
# (succeeded | failed | cancelled). ``error_class`` is the failure
# discriminator — kept as VARCHAR rather than an enum because S03/S04/S05
# layer in additional discriminators ('webhook_validation_failed', etc.);
# the application layer is the source of truth for the closed set.
# ``trigger_payload`` is JSONB and free-form — for ``_direct_*`` workflows
# it is ``{"prompt": "<user text>"}``. ``last_heartbeat_at`` is reserved
# for S05's worker-crash recovery; S02 sets it on transition into running.
class WorkflowRunTriggerType(str, enum.Enum):
    button = "button"
    webhook = "webhook"
    schedule = "schedule"
    manual = "manual"
    admin_manual = "admin_manual"


class WorkflowRunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class StepRunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class WorkflowRun(SQLModel, table=True):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('button', 'webhook', 'schedule', 'manual', "
            "'admin_manual')",
            name="ck_workflow_runs_trigger_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'cancelled')",
            name="ck_workflow_runs_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workflow_id: uuid.UUID = Field(
        foreign_key="workflows.id", nullable=False, ondelete="CASCADE"
    )
    team_id: uuid.UUID = Field(
        foreign_key="team.id", nullable=False, ondelete="CASCADE"
    )
    trigger_type: str = Field(max_length=32, nullable=False)
    triggered_by_user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
    )
    target_user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
    )
    trigger_payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="'{}'::jsonb"),
    )
    status: str = Field(default="pending", max_length=32, nullable=False)
    error_class: str | None = Field(
        default=None, max_length=64, nullable=True
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    duration_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    last_heartbeat_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    # S03 additions (s13 migration) — cancellation audit
    cancelled_by_user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
    )
    cancelled_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    # S04 addition (s14 migration) — webhook idempotency. Set only when
    # trigger_type='webhook'; NULL for all other trigger types. The UNIQUE
    # constraint on this column prevents a duplicate delivery_id from
    # creating a second WorkflowRun row.
    webhook_delivery_id: str | None = Field(
        default=None, max_length=64, nullable=True, unique=True
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Per-step record. ``snapshot`` JSONB freezes the WorkflowStep row at
# dispatch time — the contract is forever-frozen so editing the parent
# WorkflowStep after dispatch must not change the historical record.
# ``stdout`` / ``stderr`` ARE persisted (R018: forever-debuggable history);
# the rest of the system never logs them. ``error_class`` is propagated up
# to the parent run on failure.
class StepRun(SQLModel, table=True):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "step_index",
            name="uq_step_runs_workflow_run_id_step_index",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'skipped')",
            name="ck_step_runs_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workflow_run_id: uuid.UUID = Field(
        foreign_key="workflow_runs.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    step_index: int = Field(nullable=False)
    snapshot: dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    status: str = Field(default="pending", max_length=32, nullable=False)
    stdout: str = Field(default="", nullable=False)
    stderr: str = Field(default="", nullable=False)
    exit_code: int | None = Field(default=None, nullable=True)
    error_class: str | None = Field(
        default=None, max_length=64, nullable=True
    )
    duration_ms: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class StepRunPublic(SQLModel):
    id: uuid.UUID
    workflow_run_id: uuid.UUID
    step_index: int
    snapshot: dict[str, Any]
    # Typed as the enum so OpenAPI emits the five status literals.
    status: StepRunStatus
    stdout: str
    stderr: str
    exit_code: int | None = None
    error_class: str | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class WorkflowRunPublic(SQLModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    team_id: uuid.UUID
    # Typed as the enum so OpenAPI emits the five trigger_type literals.
    trigger_type: WorkflowRunTriggerType
    triggered_by_user_id: uuid.UUID | None = None
    target_user_id: uuid.UUID | None = None
    trigger_payload: dict[str, Any]
    # Typed as the enum so OpenAPI emits the five status literals.
    status: WorkflowRunStatus
    error_class: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    last_heartbeat_at: datetime | None = None
    # S03 additions — cancellation audit
    cancelled_by_user_id: uuid.UUID | None = None
    cancelled_at: datetime | None = None
    created_at: datetime | None = None
    step_runs: list[StepRunPublic] = Field(default_factory=list)


# Dispatch DTO for POST /api/v1/workflows/{id}/run. ``trigger_payload`` is
# free-form JSONB; for ``_direct_*`` workflows the route validates the
# presence of a ``prompt`` key but lets the rest pass through.
class WorkflowRunCreate(SQLModel):
    trigger_payload: dict[str, Any] = Field(default_factory=dict)


# Response DTO for POST /api/v1/workflows/{id}/run. Just the run id +
# initial status — the client polls GET /workflow_runs/{id} for the
# transitions.
class WorkflowRunDispatched(SQLModel):
    run_id: uuid.UUID
    status: WorkflowRunStatus


# Paginated list DTO for GET /api/v1/teams/{team_id}/runs.
class WorkflowRunSummaryPublic(SQLModel):
    """Lightweight run row for the history list — no step_runs embedded."""

    id: uuid.UUID
    workflow_id: uuid.UUID
    team_id: uuid.UUID
    trigger_type: WorkflowRunTriggerType
    triggered_by_user_id: uuid.UUID | None = None
    target_user_id: uuid.UUID | None = None
    status: WorkflowRunStatus
    error_class: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None


class WorkflowRunsPublic(SQLModel):
    data: list[WorkflowRunSummaryPublic]
    count: int


# Request body for POST /api/v1/admin/workflows/{id}/trigger.
class AdminWorkflowTriggerBody(SQLModel):
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
