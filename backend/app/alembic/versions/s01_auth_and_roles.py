"""S01 auth and roles: UserRole enum, TeamRole enum, Team stub, TeamMember, drop is_superuser

Revision ID: s01_auth_and_roles
Revises: fe56fa70289e
Create Date: 2026-04-24 14:57:00.000000

- Creates userrole enum ('user', 'system_admin') and teamrole enum ('member', 'admin').
- Adds user.role column (nullable first), data-migrates is_superuser=True -> 'system_admin'
  and is_superuser=False -> 'user', then makes role NOT NULL and drops is_superuser.
- Creates minimal `team` stub (id UUID PK + created_at); S02 will extend with real columns.
- Creates `team_member` (id, user_id, team_id, role, created_at) with unique (user_id, team_id).

Downgrade is fully reversible: drops team_member + team, re-adds is_superuser and maps
role='system_admin' -> True else False, drops the role column, drops both enum types.
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "s01_auth_and_roles"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s01")


USER_ROLE_VALUES = ("user", "system_admin")
TEAM_ROLE_VALUES = ("member", "admin")


def upgrade():
    bind = op.get_bind()

    # 1. Create enum types.
    user_role = postgresql.ENUM(*USER_ROLE_VALUES, name="userrole")
    team_role = postgresql.ENUM(*TEAM_ROLE_VALUES, name="teamrole")
    user_role.create(bind, checkfirst=False)
    team_role.create(bind, checkfirst=False)

    # 2. Add user.role column as nullable so existing rows can be back-filled.
    op.add_column(
        "user",
        sa.Column(
            "role",
            postgresql.ENUM(*USER_ROLE_VALUES, name="userrole", create_type=False),
            nullable=True,
        ),
    )

    # 3. Data migration: is_superuser=True -> 'system_admin', else 'user'.
    admin_count = bind.execute(
        sa.text(
            "UPDATE \"user\" SET role = 'system_admin'::userrole WHERE is_superuser = TRUE"
        )
    ).rowcount
    user_count = bind.execute(
        sa.text(
            "UPDATE \"user\" SET role = 'user'::userrole WHERE is_superuser = FALSE"
        )
    ).rowcount
    logger.info(
        "S01 migration: mapped %d is_superuser=True rows -> system_admin, "
        "%d is_superuser=False rows -> user",
        admin_count,
        user_count,
    )

    # 4. Tighten role to NOT NULL now that every row has a value.
    op.alter_column("user", "role", nullable=False)

    # 5. Drop is_superuser now that role carries the information.
    op.drop_column("user", "is_superuser")

    # 6. Create minimal `team` stub. S02 extends with (name, slug, is_personal, ...).
    op.create_table(
        "team",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. Create `team_member` join table.
    op.create_table(
        "team_member",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(*TEAM_ROLE_VALUES, name="teamrole", create_type=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "team_id", name="uq_team_member_user_team"),
    )
    op.create_index(
        op.f("ix_team_member_user_id"), "team_member", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_member_team_id"), "team_member", ["team_id"], unique=False
    )


def downgrade():
    bind = op.get_bind()

    # Reverse order of creation.
    op.drop_index(op.f("ix_team_member_team_id"), table_name="team_member")
    op.drop_index(op.f("ix_team_member_user_id"), table_name="team_member")
    op.drop_table("team_member")
    op.drop_table("team")

    # Re-add is_superuser as nullable first so we can back-fill from role.
    op.add_column(
        "user",
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
    )
    admin_count = bind.execute(
        sa.text(
            "UPDATE \"user\" SET is_superuser = TRUE WHERE role = 'system_admin'::userrole"
        )
    ).rowcount
    other_count = bind.execute(
        sa.text(
            "UPDATE \"user\" SET is_superuser = FALSE WHERE role <> 'system_admin'::userrole"
        )
    ).rowcount
    logger.info(
        "S01 downgrade: restored is_superuser for %d admin rows and %d non-admin rows",
        admin_count,
        other_count,
    )
    op.alter_column("user", "is_superuser", nullable=False)

    # Drop role column then enum types.
    op.drop_column("user", "role")

    postgresql.ENUM(*TEAM_ROLE_VALUES, name="teamrole").drop(bind, checkfirst=False)
    postgresql.ENUM(*USER_ROLE_VALUES, name="userrole").drop(bind, checkfirst=False)
