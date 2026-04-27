"""Auto-push executor (M004/S04/T04).

The post-receive hook installed at clone-time on auto-rule projects fires
``POST /v1/projects/{project_id}/auto-push-callback`` after every successful
push from a user container into the team mirror. The callback handler in
``routes_projects`` delegates to ``run_auto_push`` here.

Runtime contract:

  1. Load (team_id, installation_id, github_repo_full_name) for project_id.
     Missing project → ``{result: 'project_not_found'}`` (the route layer
     already emits a 404; the executor returns rather than raising so the
     log carries the full story even when the route layer is bypassed in
     tests).
  2. Defensive re-check of ``project_push_rules.mode``. The hook may have
     been installed when mode=auto and never uninstalled if a subsequent
     PUT /push-rule's hook-uninstall hop failed. If mode is not 'auto' on
     the read, log INFO ``auto_push_skipped reason=rule_changed`` and
     return ``{result: 'skipped_rule_changed'}``.
  3. Mint a fresh installation token via ``get_installation_token`` (cache-
     first against Redis; mints on miss). Token-mint failure → log ERROR,
     return ``{result: 'token_mint_failed', status, reason}``.
  4. Discover the team's mirror container by labels (same shape as
     ``team_mirror._find_team_mirror_container``). Missing mirror →
     ``{result: 'mirror_unavailable'}`` and a WARNING log; the next user
     push will lazily re-spinup the mirror but the auto-push for THIS push
     is lost (this is the documented best-effort failure mode per D024).
  5. Docker-exec ``git push --all --prune <authed-url>`` followed by
     ``git push --tags <authed-url>`` inside the mirror container, with the
     installation token passed via the ``environment`` dict (NEVER in cmd).
     The shell expands ``$TOKEN`` from env at exec time (MEM274).
  6. Update ``projects.last_push_status``:
        - both calls 0 → 'ok', last_push_error=NULL
        - either non-zero → 'failed', last_push_error=<scrubbed stderr>
     Stderr is scrubbed of any ``gho_/ghs_/ghu_/ghr_/github_pat_`` substring
     before persisting (defense in depth — git's stderr can echo the URL
     back in some failure modes).

Logging discipline (slice observability contract):
  INFO  auto_push_started project_id=<uuid> rule_mode=auto
        trigger=post_receive token_prefix=<4>...
  INFO  auto_push_completed project_id=<uuid> result=<ok|failed>
        duration_ms=<n>
  INFO  auto_push_skipped project_id=<uuid> reason=<rule_changed|...>
  WARN  auto_push_rejected_by_remote project_id=<uuid> exit_code=<n>
        stderr_short=<scrubbed,first-200-chars>

Token plaintext NEVER appears in logs — only the first-4-char prefix via
``_token_prefix`` from github_tokens. Stderr is scrubbed unconditionally
before any persistence or logging.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

import aiodocker
import asyncpg
from aiodocker.exceptions import DockerError

from orchestrator.config import settings
from orchestrator.errors import DockerUnavailable
from orchestrator.github_tokens import (
    InstallationTokenMintFailed,
    _token_prefix,
    get_installation_token,
)
from orchestrator.team_mirror import _team_mirror_container_name

logger = logging.getLogger("orchestrator")


# Token-prefix scrubbing fingerprints. Same family as the leak-detection
# fingerprints in clone._LEAK_FINGERPRINTS but applied as a redactor (not a
# detector) — git's stderr can carry an authenticated URL on certain failure
# modes (e.g. "fatal: unable to access 'https://x-access-token:gho_AAA@...'")
# and we MUST NOT persist that into projects.last_push_error.
_SCRUB_PATTERNS = re.compile(
    r"(gho_|ghs_|ghu_|ghr_|github_pat_)[A-Za-z0-9_]+",
    re.IGNORECASE,
)


# Max stderr bytes we persist into projects.last_push_error. The DB column
# is TEXT (no constraint), but capping defends against a runaway error
# message from blowing up admin queries / FE renders.
_MAX_STDERR_PERSIST_CHARS = 500

# Max chars of stderr that appear in the WARNING log line. Shorter than the
# DB cap because logs are noisier — the DB is the durable record.
_MAX_STDERR_LOG_CHARS = 200


def _scrub_token_substrings(text: str) -> str:
    """Replace any GitHub-token-prefix substring with ``<redacted-token>``.

    Defense in depth: git's stderr can include the full clone URL on some
    failure modes. The token would otherwise land in the DB row and any log
    line constructed from it. We unconditionally scrub before either lands.
    """
    if not text:
        return text
    return _SCRUB_PATTERNS.sub("<redacted-token>", text)


async def _load_project_for_push(
    pool: asyncpg.Pool, project_id: str
) -> dict[str, Any] | None:
    """Return (team_id, installation_id, github_repo_full_name) for project_id.

    Returns None if the project row is missing or pg is unreachable. The
    caller maps None to ``{result: 'project_not_found'}`` rather than raising
    — auto-push is best-effort; a deleted project should not crash the hook.
    """
    sql = (
        "SELECT team_id, installation_id, github_repo_full_name "
        "FROM projects WHERE id = $1"
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, uuid.UUID(project_id))
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=load_project_for_push reason=%s project_id=%s",
            type(exc).__name__,
            project_id,
        )
        return None
    if row is None:
        return None
    return {
        "team_id": str(row["team_id"]),
        "installation_id": int(row["installation_id"]),
        "repo_full_name": str(row["github_repo_full_name"]),
    }


async def _read_push_rule_mode(
    pool: asyncpg.Pool, project_id: str
) -> str | None:
    """Re-check the project's push-rule mode (defensive, post-load).

    Mirrors clone._read_push_rule_mode but kept local so auto_push has no
    cross-import on clone. Returns None on missing row or pg trouble — the
    caller treats both as "skip with rule_changed" since neither path
    should fire an auto-push.
    """
    sql = "SELECT mode FROM project_push_rules WHERE project_id = $1"
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, uuid.UUID(project_id))
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=read_push_rule_mode_auto_push "
            "reason=%s project_id=%s",
            type(exc).__name__,
            project_id,
        )
        return None
    if row is None:
        return None
    mode = row["mode"]
    return str(mode) if mode is not None else None


async def _update_last_push_status(
    pool: asyncpg.Pool,
    project_id: str,
    *,
    status: str,
    error: str | None,
) -> None:
    """Persist auto-push outcome into projects.last_push_status / last_push_error.

    Failure to write is logged-and-swallowed: the executor's job is the push,
    not the bookkeeping; a stale row is acceptable (and the next push will
    overwrite it on success). We do NOT raise here.
    """
    sql = (
        "UPDATE projects SET last_push_status = $1, last_push_error = $2 "
        "WHERE id = $3"
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql, status, error, uuid.UUID(project_id))
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "pg_unreachable op=update_last_push_status "
            "reason=%s project_id=%s",
            type(exc).__name__,
            project_id,
        )


async def _find_team_mirror_container_id(
    docker: aiodocker.Docker, team_id: str
) -> str | None:
    """Find the running mirror container for ``team_id``, or None.

    Same label filter shape as team_mirror._find_team_mirror_container, but
    inlined here to avoid the cross-import (auto_push deliberately depends
    on team_mirror only for the container-name helper).
    """
    filters = json.dumps(
        {
            "label": [
                f"team_id={team_id}",
                "perpetuity.team_mirror=true",
                "perpetuity.managed=true",
            ]
        }
    )
    try:
        results = await docker.containers.list(all=True, filters=filters)
    except DockerError as exc:
        raise DockerUnavailable(
            f"docker_list_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc
    for container in results:
        state = container._container.get("State")
        if isinstance(state, str):
            running = state == "running"
        elif isinstance(state, dict):
            running = state.get("Running", False)
        else:
            running = False
        if running:
            return container.id
    return None


async def _exec_collect_stderr(
    docker: aiodocker.Docker,
    container_id: str,
    cmd: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> tuple[str, int]:
    """Run ``cmd`` inside ``container_id``; return (stdout+stderr-text, exit_code).

    Distinct from clone._exec_with_env in that it explicitly captures stderr
    into the same byte stream (aiodocker multiplexes both into the read_out()
    iterator already). The combined stream is the one we scrub + persist.
    """
    try:
        container = await docker.containers.get(container_id)
        exec_inst = await container.exec(
            cmd=cmd,
            stdout=True,
            stderr=True,
            environment=environment,
        )
        out_bytes = bytearray()
        async with exec_inst.start(detach=False) as stream:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                out_bytes.extend(msg.data)
        info = await exec_inst.inspect()
        exit_code = int(info.get("ExitCode") or 0)
        return out_bytes.decode("utf-8", errors="replace"), exit_code
    except DockerError as exc:
        raise DockerUnavailable(
            f"docker_exec_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc


async def run_auto_push(
    docker: aiodocker.Docker,
    pool: asyncpg.Pool,
    *,
    project_id: str,
    redis_client: Any | None = None,
) -> dict[str, Any]:
    """Push the team mirror's bare repo for ``project_id`` to its GitHub origin.

    Always returns a dict; never raises. The result shape carries enough
    detail for the route layer to log + return a 200 (auto-push is best-
    effort and the post-receive hook ignores the response code anyway).

    Possible result values:
      - 'ok'                   — both push commands succeeded
      - 'failed'               — at least one push command exited non-zero
      - 'project_not_found'    — project row missing or pg unreachable
      - 'skipped_rule_changed' — rule is no longer 'auto'
      - 'mirror_unavailable'   — no running mirror container for this team
      - 'token_mint_failed'    — get_installation_token raised
      - 'docker_unavailable'   — docker daemon trouble during exec
    """
    started = time.monotonic()

    # 1. Load project. Missing → no-op.
    project = await _load_project_for_push(pool, project_id)
    if project is None:
        logger.warning(
            "auto_push_project_not_found project_id=%s", project_id
        )
        return {"result": "project_not_found"}

    team_id = project["team_id"]
    installation_id = project["installation_id"]
    repo_full_name = project["repo_full_name"]

    # 2. Defensive rule re-check.
    mode = await _read_push_rule_mode(pool, project_id)
    if mode != "auto":
        logger.info(
            "auto_push_skipped project_id=%s reason=rule_changed",
            project_id,
        )
        return {"result": "skipped_rule_changed"}

    # 3. Mint a fresh installation token. We do not reuse a token across
    #    pushes — the cache layer in get_installation_token gives us reuse
    #    semantics for free (50min TTL) without per-call ceremony here.
    try:
        minted = await get_installation_token(
            installation_id, redis_client=redis_client, pg_pool=pool
        )
    except InstallationTokenMintFailed as exc:
        await _update_last_push_status(
            pool,
            project_id,
            status="failed",
            error=f"token_mint_failed_status_{exc.status}",
        )
        logger.error(
            "auto_push_token_mint_failed project_id=%s status=%s reason=%s",
            project_id,
            exc.status,
            exc.reason,
        )
        return {
            "result": "token_mint_failed",
            "status": exc.status,
            "reason": exc.reason,
        }
    token = str(minted["token"])

    # 4. Find the team mirror container.
    try:
        mirror_container_id = await _find_team_mirror_container_id(
            docker, team_id
        )
    except DockerUnavailable as exc:
        logger.error(
            "auto_push_docker_unavailable project_id=%s reason=%s",
            project_id,
            str(exc),
        )
        return {"result": "docker_unavailable"}

    if mirror_container_id is None:
        # The mirror was reaped between hook fire and callback — auto-push
        # for this user push is lost. Best-effort by D024.
        logger.warning(
            "auto_push_mirror_unavailable project_id=%s team_id=%s",
            project_id,
            team_id,
        )
        return {"result": "mirror_unavailable"}

    logger.info(
        "auto_push_started project_id=%s rule_mode=auto "
        "trigger=post_receive token_prefix=%s",
        project_id,
        _token_prefix(token),
    )

    # 5. Push refs. Two separate exec calls because `git push --all` does
    #    NOT include tags by GitHub's protocol convention — explicitly push
    #    tags after refs/heads. The token only ever appears in the env dict.
    # ``github_clone_base_url`` is a settings hook ONLY used by the M004/S04
    # e2e to swap the public host for a mock — production NEVER overrides
    # it. Same shape as clone.py's _git_clone_into_tmp.
    bare = f"/repos/{project_id}.git"
    base = settings.github_clone_base_url.rstrip("/")
    if base.startswith("https://"):
        host = base.split("://", 1)[1]
        push_url = (
            f"https://x-access-token:$TOKEN@{host}/{repo_full_name}.git"
        )
    else:
        # git:// (test mock) — credential-free.
        push_url = f"{base}/{repo_full_name}.git"
    push_all_cmd = [
        "sh",
        "-c",
        f"git --git-dir={bare} push --all --prune {push_url}",
    ]
    push_tags_cmd = [
        "sh",
        "-c",
        f"git --git-dir={bare} push --tags {push_url}",
    ]

    try:
        out_all, exit_all = await _exec_collect_stderr(
            docker,
            mirror_container_id,
            push_all_cmd,
            environment={"TOKEN": token},
        )
    except DockerUnavailable as exc:
        await _update_last_push_status(
            pool,
            project_id,
            status="failed",
            error=f"docker_unavailable:{str(exc)[:100]}",
        )
        logger.error(
            "auto_push_docker_unavailable project_id=%s phase=push_all reason=%s",
            project_id,
            str(exc),
        )
        return {"result": "docker_unavailable"}

    # If --all succeeded, follow with --tags. If --all failed, skip --tags
    # (no point pushing tags if the heads didn't land).
    out_tags = ""
    exit_tags = 0
    if exit_all == 0:
        try:
            out_tags, exit_tags = await _exec_collect_stderr(
                docker,
                mirror_container_id,
                push_tags_cmd,
                environment={"TOKEN": token},
            )
        except DockerUnavailable as exc:
            await _update_last_push_status(
                pool,
                project_id,
                status="failed",
                error=f"docker_unavailable_tags:{str(exc)[:100]}",
            )
            logger.error(
                "auto_push_docker_unavailable project_id=%s "
                "phase=push_tags reason=%s",
                project_id,
                str(exc),
            )
            return {"result": "docker_unavailable"}

    # 6. Persist outcome + emit completion log.
    duration_ms = int((time.monotonic() - started) * 1000)

    if exit_all == 0 and exit_tags == 0:
        await _update_last_push_status(
            pool, project_id, status="ok", error=None
        )
        logger.info(
            "auto_push_completed project_id=%s result=ok duration_ms=%d",
            project_id,
            duration_ms,
        )
        return {
            "result": "ok",
            "exit_code": 0,
            "duration_ms": duration_ms,
            "stderr_short": "",
        }

    # At least one push failed. Combine the failing-call stderr (or both,
    # if both failed) for persistence + logging. Scrub before either lands.
    combined = ""
    if exit_all != 0:
        combined = out_all
    elif exit_tags != 0:
        combined = out_tags
    scrubbed = _scrub_token_substrings(combined)
    persist_error = scrubbed[:_MAX_STDERR_PERSIST_CHARS]
    log_short = scrubbed[:_MAX_STDERR_LOG_CHARS].replace("\n", " ")

    await _update_last_push_status(
        pool, project_id, status="failed", error=persist_error
    )
    failing_exit = exit_all if exit_all != 0 else exit_tags
    logger.warning(
        "auto_push_rejected_by_remote project_id=%s exit_code=%d "
        "stderr_short=%s",
        project_id,
        failing_exit,
        log_short,
    )
    logger.info(
        "auto_push_completed project_id=%s result=failed duration_ms=%d",
        project_id,
        duration_ms,
    )
    return {
        "result": "failed",
        "exit_code": failing_exit,
        "duration_ms": duration_ms,
        "stderr_short": log_short,
    }


__all__ = [
    "run_auto_push",
    "_scrub_token_substrings",
    "_load_project_for_push",
    "_read_push_rule_mode",
    "_update_last_push_status",
    "_find_team_mirror_container_id",
    "_SCRUB_PATTERNS",
    "_MAX_STDERR_PERSIST_CHARS",
    "_MAX_STDERR_LOG_CHARS",
]
