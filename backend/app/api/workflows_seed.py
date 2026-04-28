"""System-workflow auto-seed helper for M005/S02 (D028).

Every team gets two system-owned workflows ready to fire:

  - ``_direct_claude`` — single Claude step with prompt template ``{prompt}``.
    The dashboard 'Run Claude' button submits a prompt form whose value
    becomes the ``prompt`` key in the trigger payload; the executor
    substitutes it into the step config at dispatch time.
  - ``_direct_codex`` — same shape, with the Codex CLI.

These rows are flagged ``system_owned=TRUE`` so S03's CRUD UI filters them
out — the dashboard surfaces them as buttons, not as editable workflow rows.
The leading-underscore name is the namespace convention; the
``UNIQUE (team_id, name)`` constraint backs the upsert.

This module is the single source of truth for the seed payload. Both the
runtime team-create code path (called from ``routes/teams.py`` and from
``crud.create_user_with_personal_team``) and the s12 backfill migration
construct their rows from the same dict.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

logger = logging.getLogger(__name__)


# Public seed registry. Order is significant only for readability — the
# unique constraint is on (team_id, name) so insert order doesn't matter.
# Each entry produces exactly one workflow + one step row.
SYSTEM_WORKFLOWS: list[dict[str, Any]] = [
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


def seed_system_workflows(session: Session, team_id: uuid.UUID) -> int:
    """Idempotently insert the system workflows for ``team_id``.

    Returns the number of workflow rows actually inserted (0 if all rows
    already existed). Steps for newly-inserted workflows are written too.

    The function uses raw ``INSERT ... ON CONFLICT (team_id, name) DO
    NOTHING RETURNING id`` so re-running is safe — replaying the seed for a
    team that already has both rows is a no-op. Critically: this means the
    s12 backfill migration AND the runtime team-create call can both invoke
    the same helper without coordinating.

    Does NOT commit. The caller controls transaction boundaries:
      - ``crud.create_team_with_admin`` and ``create_user_with_personal_team``
        commit after the team + admin membership land; we slot in just
        before that final commit so a seed failure rolls the whole
        team-create back rather than leaving an orphan team without
        workflows.
      - The s12 migration commits each batch in its own transaction.

    Errors:
      - SQL errors propagate so the caller can roll back the parent
        transaction. We log + re-raise (no swallowing).
      - This function does NOT raise on "team already had these rows" —
        that's the happy path of the upsert.
    """
    inserted = 0
    try:
        for wf in SYSTEM_WORKFLOWS:
            new_wf_id = uuid.uuid4()
            row = session.execute(
                text(
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
                # Already seeded — skip the step insert too. The (team_id,
                # name) row already has its step from the prior seed, and
                # writing a duplicate step would violate UNIQUE on
                # (workflow_id, step_index).
                continue
            inserted_wf_id = row[0]
            step = wf["step"]
            session.execute(
                text(
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
                    # Use JSON.dumps via sqlalchemy's text — passing the
                    # raw dict triggers asyncpg-style "list of params"
                    # binding that doesn't apply to JSONB on this
                    # synchronous SQLAlchemy session. Stringify, cast on
                    # the SQL side.
                    "config": _json_dumps(step["config"]),
                },
            )
            inserted += 1
    except SQLAlchemyError:
        logger.warning(
            "system_workflow_seed_failed team_id=%s",
            team_id,
        )
        raise

    if inserted:
        logger.info(
            "system_workflows_seeded team_id=%s inserted=%d",
            team_id,
            inserted,
        )
    return inserted


def _json_dumps(value: dict[str, Any]) -> str:
    """JSON-encode without importing json at module top — keeps the import
    surface tight and matches the rest of the api package's style of only
    importing what's needed."""
    import json

    return json.dumps(value, separators=(",", ":"))
