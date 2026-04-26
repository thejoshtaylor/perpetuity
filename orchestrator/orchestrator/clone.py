"""GitHub→mirror clone with strict credential discipline (M004/S04/T02).

The first hop of the two-hop clone path (MEM228). One function:

    clone_to_mirror(docker, pool, *, team_id, project_id,
                    repo_full_name, installation_id) -> {result, duration_ms}

Steps inside the team's mirror container:

  1. ensure_team_mirror(...) — spin up / reuse the mirror (S03).
  2. Idempotency short-circuit: if /repos/<project_id>.git/HEAD already
     exists, return {result:'reused', duration_ms:0} without re-cloning.
     The mirror's bare repo is the durable state; a re-materialize on a
     project that already cloned once is a no-op.
  3. get_installation_token(installation_id, ...) — cache-first via Redis,
     mints from GitHub on miss (S02).
  4. docker-exec `git clone --bare https://x-access-token:${TOKEN}@github.com/<repo>.git
     /repos/.tmp/<project_id>.git` inside the mirror container with the
     token passed via the `environment` dict on the exec invocation,
     NEVER as part of the persisted command line. The shell expands
     ${TOKEN} from the env dict at exec time.
  5. Sanitize: `git --git-dir=/repos/.tmp/<project_id>.git remote set-url
     origin https://github.com/<repo>.git` rewrites the token out of the
     bare repo's config.
  6. Verify: `cat /repos/.tmp/<project_id>.git/config` MUST NOT contain
     `x-access-token` or any `gho_/ghs_/ghu_/ghr_/github_pat_` prefix.
     If it does, rm -rf the half-clone and raise
     CloneCredentialLeakDetected (mapped to 500 — never reached in prod).
  7. Atomic rename: `mv /repos/.tmp/<project_id>.git /repos/<project_id>.git`.

Failure mapping (route layer):
  InstallationTokenMintFailed → 502 github_clone_failed
  DockerUnavailable           → 503 docker_unavailable (existing handler)
  Generic non-zero git-clone  → 502 with reason=git_clone_exit_<code>
  CloneCredentialLeakDetected → 500 clone_credential_leak

Logging discipline (MEM134, MEM262): UUIDs only; container ids
truncated to 12; tokens NEVER appear except via _token_prefix(token).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiodocker
import asyncpg
from aiodocker.exceptions import DockerError

from orchestrator.errors import (
    CloneCredentialLeakDetected,
    DockerUnavailable,
)
from orchestrator.github_tokens import (
    InstallationTokenMintFailed,
    _token_prefix,
    get_installation_token,
)
from orchestrator.team_mirror import (
    _team_mirror_container_name,
    ensure_team_mirror,
)

logger = logging.getLogger("orchestrator")


# Token-leak fingerprints. Any of these substrings in /repos/<id>.git/config
# after the sanitize step is a structural failure. `x-access-token` is the
# username placeholder GitHub recommends for installation-token clones; the
# others are GitHub's documented token-prefix families (MEM262).
_LEAK_FINGERPRINTS = (
    "x-access-token",
    "gho_",
    "ghs_",
    "ghu_",
    "ghr_",
    "github_pat_",
)


def _bare_repo_path(project_id: str) -> str:
    return f"/repos/{project_id}.git"


def _tmp_repo_path(project_id: str) -> str:
    return f"/repos/.tmp/{project_id}.git"


async def _exec_with_env(
    docker: aiodocker.Docker,
    container_id: str,
    cmd: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> tuple[str, int]:
    """Run ``cmd`` inside ``container_id`` with optional env overrides.

    Mirrors ``sessions._exec_collect`` but threads the ``environment`` dict
    through to ``container.exec``. The token MUST be passed this way — the
    cmd list is what gets persisted in docker's exec inspect record, and
    putting the token there would leak it into any audit log that scrapes
    docker events.

    Returns ``(stdout-as-text, exit_code)``. Raises DockerUnavailable on
    daemon trouble, matching the rest of the orchestrator's contract.
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


class _CloneExecFailed(Exception):
    """Internal — non-zero exit from a git-clone-class exec inside the mirror.

    Carries the exit code so the route layer can map to
    `git_clone_exit_<code>`. Never carries stderr verbatim — token plaintext
    could appear there if the URL was malformed.
    """

    def __init__(self, exit_code: int, op: str) -> None:
        self.exit_code = exit_code
        self.op = op
        super().__init__(f"{op}_exit_{exit_code}")


async def _bare_repo_exists(
    docker: aiodocker.Docker, container_id: str, project_id: str
) -> bool:
    """True if /repos/<project_id>.git/HEAD is present in the mirror.

    HEAD is the canonical "this bare repo is initialized" sentinel; cloning
    creates it last. We test with `test -f` rather than `cat` so an absent
    file returns clean exit code 1 (not 2 / not stderr noise).
    """
    head = f"{_bare_repo_path(project_id)}/HEAD"
    out, code = await _exec_with_env(
        docker,
        container_id,
        ["test", "-f", head],
    )
    return code == 0


async def _git_clone_into_tmp(
    docker: aiodocker.Docker,
    container_id: str,
    *,
    project_id: str,
    repo_full_name: str,
    token: str,
) -> None:
    """git clone --bare https://x-access-token:${TOKEN}@github.com/...

    Token is referenced in the URL via shell variable expansion; the
    expansion happens inside `sh -c`, so the actual token string never
    sits in the cmd list passed to docker exec. The env dict carries it.
    """
    # Make sure /repos/.tmp exists (idempotent).
    out, code = await _exec_with_env(
        docker,
        container_id,
        ["mkdir", "-p", "/repos/.tmp"],
    )
    if code != 0:
        raise _CloneExecFailed(code, "mkdir_tmp")

    # If /repos/.tmp/<project_id>.git exists from a prior failed attempt,
    # nuke it so the clone target is clean.
    tmp = _tmp_repo_path(project_id)
    out, code = await _exec_with_env(
        docker,
        container_id,
        ["rm", "-rf", tmp],
    )
    if code != 0:
        raise _CloneExecFailed(code, "rm_tmp_precheck")

    # The URL substitution happens in-shell; the token only ever lives in
    # the env dict. The repo_full_name is hard-quoted into the URL by
    # construction (caller validates shape). $TOKEN is the env binding.
    url = f"https://x-access-token:$TOKEN@github.com/{repo_full_name}.git"
    cmd = [
        "sh",
        "-c",
        f"git clone --bare {url} {tmp}",
    ]
    out, code = await _exec_with_env(
        docker,
        container_id,
        cmd,
        environment={"TOKEN": token},
    )
    if code != 0:
        # Best-effort cleanup; ignore exit code on the rm.
        await _exec_with_env(
            docker,
            container_id,
            ["rm", "-rf", tmp],
        )
        raise _CloneExecFailed(code, "git_clone")


async def _sanitize_remote_url(
    docker: aiodocker.Docker,
    container_id: str,
    *,
    project_id: str,
    repo_full_name: str,
) -> None:
    """Rewrite origin to the bare https URL — strips the token from .git/config."""
    tmp = _tmp_repo_path(project_id)
    bare_url = f"https://github.com/{repo_full_name}.git"
    out, code = await _exec_with_env(
        docker,
        container_id,
        [
            "git",
            f"--git-dir={tmp}",
            "remote",
            "set-url",
            "origin",
            bare_url,
        ],
    )
    if code != 0:
        raise _CloneExecFailed(code, "remote_set_url")


async def _verify_no_credentials(
    docker: aiodocker.Docker,
    container_id: str,
    *,
    project_id: str,
) -> None:
    """Fail closed if any leak fingerprint is in /repos/.tmp/<id>.git/config.

    On detection: rm -rf the half-clone and raise
    CloneCredentialLeakDetected. The half-clone removal keeps a future
    re-materialize from finding stale credentials inside the mirror.
    """
    tmp = _tmp_repo_path(project_id)
    out, code = await _exec_with_env(
        docker,
        container_id,
        ["cat", f"{tmp}/config"],
    )
    if code != 0:
        # Couldn't even read the config we just wrote — treat as a leak
        # detection pessimistically, but the rm will likely also fail.
        # Cleanest contract: raise an exec-level failure so the caller
        # surfaces 502 git_clone_exit_<code> rather than a structural 500.
        raise _CloneExecFailed(code, "verify_cat_config")
    haystack = out.lower()
    for needle in _LEAK_FINGERPRINTS:
        if needle.lower() in haystack:
            logger.error(
                "clone_credential_leak_detected project_id=%s",
                project_id,
            )
            # Best-effort cleanup; ignore exit code.
            await _exec_with_env(
                docker,
                container_id,
                ["rm", "-rf", tmp],
            )
            raise CloneCredentialLeakDetected(project_id)


async def _atomic_rename(
    docker: aiodocker.Docker,
    container_id: str,
    *,
    project_id: str,
) -> None:
    """mv /repos/.tmp/<id>.git /repos/<id>.git — atomic on the same FS."""
    tmp = _tmp_repo_path(project_id)
    final = _bare_repo_path(project_id)
    out, code = await _exec_with_env(
        docker,
        container_id,
        ["mv", tmp, final],
    )
    if code != 0:
        raise _CloneExecFailed(code, "rename")


async def clone_to_mirror(
    docker: aiodocker.Docker,
    pool: asyncpg.Pool,
    *,
    team_id: str,
    project_id: str,
    repo_full_name: str,
    installation_id: int,
    redis_client: Any | None = None,
) -> dict[str, Any]:
    """Materialize ``repo_full_name`` into the team's mirror as a bare repo.

    Idempotent: if /repos/<project_id>.git/HEAD already exists, returns
    ``{result:'reused', duration_ms:0}`` without minting a token or doing
    any GitHub I/O.

    On a fresh clone returns ``{result:'created', duration_ms:<int>}``
    where duration_ms is the wall-clock from clone start to atomic rename
    completion (excluding the ensure_team_mirror cost — the mirror was
    likely already up).

    Failure surfaces (raised, not returned):
      InstallationTokenMintFailed     — token mint trouble (route → 502)
      DockerUnavailable               — docker daemon trouble (route → 503)
      _CloneExecFailed (re-raised as
        a generic Exception)          — git clone non-zero (route → 502
                                        with reason=git_clone_exit_<code>)
      CloneCredentialLeakDetected     — sanitize-verify failed (route → 500)
    """
    # 1. Ensure the mirror is up.
    ensure_result = await ensure_team_mirror(
        pool, docker, team_id, trigger="clone"
    )
    container_id = str(ensure_result["container_id"])
    container_name = _team_mirror_container_name(team_id)

    # 2. Idempotency short-circuit. If the bare repo already exists we
    # never mint a token and never touch GitHub.
    if await _bare_repo_exists(docker, container_id, project_id):
        logger.info(
            "team_mirror_clone_completed team_id=%s project_id=%s "
            "result=%s duration_ms=%d",
            team_id,
            project_id,
            "reused",
            0,
        )
        return {"result": "reused", "duration_ms": 0}

    # 3. Mint / cache-fetch the installation token.
    minted = await get_installation_token(
        installation_id, redis_client=redis_client, pg_pool=pool
    )
    token = str(minted["token"])

    started = time.monotonic()
    logger.info(
        "team_mirror_clone_started team_id=%s project_id=%s "
        "repo=%s token_prefix=%s",
        team_id,
        project_id,
        repo_full_name,
        _token_prefix(token),
    )

    try:
        # 4. The clone proper — token only in env, never in cmd.
        await _git_clone_into_tmp(
            docker,
            container_id,
            project_id=project_id,
            repo_full_name=repo_full_name,
            token=token,
        )
        # 5. Strip the token from the bare repo's config.
        await _sanitize_remote_url(
            docker,
            container_id,
            project_id=project_id,
            repo_full_name=repo_full_name,
        )
        # 6. Structural verify — fail closed if the sanitize missed.
        await _verify_no_credentials(
            docker,
            container_id,
            project_id=project_id,
        )
        # 7. Atomic rename into the canonical bare-repo path.
        await _atomic_rename(
            docker,
            container_id,
            project_id=project_id,
        )
    except _CloneExecFailed as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "team_mirror_clone_failed team_id=%s project_id=%s "
            "reason=%s duration_ms=%d",
            team_id,
            project_id,
            f"{exc.op}_exit_{exc.exit_code}",
            duration_ms,
        )
        raise
    except CloneCredentialLeakDetected:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "team_mirror_clone_failed team_id=%s project_id=%s "
            "reason=%s duration_ms=%d",
            team_id,
            project_id,
            "credential_leak_detected",
            duration_ms,
        )
        raise
    except InstallationTokenMintFailed as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "team_mirror_clone_failed team_id=%s project_id=%s "
            "reason=%s duration_ms=%d",
            team_id,
            project_id,
            f"token_mint_failed_{exc.status}",
            duration_ms,
        )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "team_mirror_clone_completed team_id=%s project_id=%s "
        "result=%s duration_ms=%d container=%s",
        team_id,
        project_id,
        "created",
        duration_ms,
        container_name,
    )
    return {"result": "created", "duration_ms": duration_ms}


__all__ = [
    "clone_to_mirror",
    "_CloneExecFailed",
    "_LEAK_FINGERPRINTS",
    "_bare_repo_path",
    "_tmp_repo_path",
    "_exec_with_env",
]
