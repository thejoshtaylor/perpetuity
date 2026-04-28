"""S12 seed: backfill _direct_claude + _direct_codex for every team

Revision ID: s12_seed_direct_workflows
Revises: s11_workflow_runs
Create Date: 2026-04-28 16:00:00.000000

Data-only migration. Walks every row in ``team`` and for each team inserts:

  - workflows row ``_direct_claude`` (system_owned=TRUE, scope='user') +
    workflow_steps row at index 0 with action='claude' and config
    ``{"prompt_template": "{prompt}"}``.
  - workflows row ``_direct_codex`` (same shape, action='codex').

ON CONFLICT (team_id, name) DO NOTHING makes the migration idempotent — re-
running on a database that already has the rows leaves the schema and data
unchanged. The runtime team-create code path
(``crud.create_team_with_admin`` and ``create_user_with_personal_team``) calls
the same helper for newly-created teams; this migration is the one-time
backfill for teams that pre-date M005/S02.

Downgrade deletes only the system-owned rows that match these two names —
user-owned rows happen to share the workflows table but are out of scope
for this migration's reverse direction.
"""
from __future__ import annotations

import json
import logging
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s12_seed_direct_workflows"
down_revision = "s11_workflow_runs"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s12")


# Mirrors backend/app/api/workflows_seed.SYSTEM_WORKFLOWS. Kept inline
# because alembic migrations should not depend on the application package
# import surface (the migration runner runs with a different working
# directory and partial PYTHONPATH); duplicating the small payload is
# cheaper than wiring up the import.
_SYSTEM_WORKFLOWS = [
    {
        "name": "_direct_claude",
        "description": "Direct Claude prompt — dashboard button (D028).",
        "scope": "user",
        "step": {
            "step_index": 0,
            "action": "claude",
            "config": {"prompt_template": "{prompt}"},
        },
    },
    {
        "name": "_direct_codex",
        "description": "Direct Codex prompt — dashboard button (D028).",
        "scope": "user",
        "step": {
            "step_index": 0,
            "action": "codex",
            "config": {"prompt_template": "{prompt}"},
        },
    },
]


def upgrade():
    bind = op.get_bind()
    teams = bind.execute(sa.text("SELECT id FROM team")).all()
    inserted = 0
    for (team_id,) in teams:
        for wf in _SYSTEM_WORKFLOWS:
            new_wf_id = uuid.uuid4()
            row = bind.execute(
                sa.text(
                    """
                    INSERT INTO workflows (
                        id, team_id, name, description, scope, system_owned
                    )
                    VALUES (
                        :id, :team_id, :name, :description, :scope, TRUE
                    )
                    ON CONFLICT (team_id, name) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": new_wf_id,
                    "team_id": team_id,
                    "name": wf["name"],
                    "description": wf["description"],
                    "scope": wf["scope"],
                },
            ).first()
            if row is None:
                # Already seeded for this team — skip the step insert too.
                # The (team_id, name) row's step row was written by the
                # prior seed pass.
                continue
            inserted_wf_id = row[0]
            step = wf["step"]
            bind.execute(
                sa.text(
                    """
                    INSERT INTO workflow_steps (
                        id, workflow_id, step_index, action, config
                    )
                    VALUES (
                        :id, :workflow_id, :step_index, :action,
                        CAST(:config AS JSONB)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "workflow_id": inserted_wf_id,
                    "step_index": step["step_index"],
                    "action": step["action"],
                    "config": json.dumps(step["config"], separators=(",", ":")),
                },
            )
            inserted += 1
    logger.info(
        "s12_seed_direct_workflows upgrade: backfilled %d system workflow rows "
        "across %d teams (idempotent — existing rows preserved)",
        inserted,
        len(teams),
    )


def downgrade():
    bind = op.get_bind()
    # Workflow_steps cascade-delete with their workflow row. We only delete
    # rows that were system-owned and match one of the two names — user-owned
    # rows that happen to share a name (impossible because system_owned
    # discriminates) are left alone.
    result = bind.execute(
        sa.text(
            """
            DELETE FROM workflows
            WHERE system_owned = TRUE
              AND name IN ('_direct_claude', '_direct_codex')
            """
        )
    )
    logger.info(
        "s12_seed_direct_workflows downgrade: removed %d system workflow rows",
        result.rowcount or 0,
    )
