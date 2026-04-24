"""S02 team columns: name, slug, is_personal on team

Revision ID: s02_team_columns
Revises: s01_auth_and_roles
Create Date: 2026-04-24 16:00:00.000000

Adds the real Team columns on top of the S01 stub:
- name VARCHAR(255) NOT NULL
- slug VARCHAR(64) NOT NULL UNIQUE (ix_team_slug)
- is_personal BOOLEAN NOT NULL DEFAULT FALSE

Follows the nullable -> backfill -> NOT NULL pattern so existing rows (if any
have been seeded manually) don't break the migration. Backfill uses the team's
own UUID as a deterministic name/slug stem so every row gets a unique slug
without collisions.

Downgrade is fully reversible: drops the unique index, then drops the three
columns — no data preservation (S02 introduces these; downgrade returns to
S01 shape).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s02_team_columns"
down_revision = "s01_auth_and_roles"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s02")


def upgrade():
    bind = op.get_bind()

    # 1. Add columns nullable so existing rows survive.
    op.add_column(
        "team",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "team",
        sa.Column("slug", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "team",
        sa.Column(
            "is_personal",
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )

    # 2. Backfill any pre-existing rows with deterministic, unique values.
    # Fresh DBs have zero rows in `team` (S01 migration created the table empty),
    # but a manually seeded row would otherwise fail the NOT NULL flip below.
    backfill_count = bind.execute(
        sa.text(
            """
            UPDATE "team"
            SET name = 'Legacy Team ' || substr(id::text, 1, 8),
                slug = 'legacy-' || substr(id::text, 1, 8),
                is_personal = FALSE
            WHERE name IS NULL
            """
        )
    ).rowcount
    logger.info(
        "S02 migration: backfilled %d team rows with legacy names",
        backfill_count,
    )

    # 3. Tighten to NOT NULL now that every row has values.
    op.alter_column("team", "name", nullable=False)
    op.alter_column("team", "slug", nullable=False)
    op.alter_column(
        "team",
        "is_personal",
        nullable=False,
        server_default=sa.false(),
    )

    # 4. Unique index on slug (separate from inline unique=True so we control the name).
    op.create_index("ix_team_slug", "team", ["slug"], unique=True)


def downgrade():
    bind = op.get_bind()

    # Rowcount for observability before we drop the columns.
    drop_count = bind.execute(sa.text('SELECT COUNT(*) FROM "team"')).scalar() or 0

    op.drop_index("ix_team_slug", table_name="team")
    op.drop_column("team", "is_personal")
    op.drop_column("team", "slug")
    op.drop_column("team", "name")

    logger.info(
        "S02 downgrade: dropped name/slug/is_personal from %d team rows",
        drop_count,
    )
