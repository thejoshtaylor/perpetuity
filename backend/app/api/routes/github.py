"""GitHub App install handshake (M004 / S02 / T02).

Endpoints:

  - `GET    /api/v1/teams/{team_id}/github/install-url`
        Team-admin gated. Returns a signed-state HS256 JWT plus the GitHub App
        install URL the team admin should visit. State payload carries
        `team_id` so the public callback can attribute the install to the
        right team without trusting GitHub-supplied query params alone.

  - `GET    /api/v1/github/install-callback`
        PUBLIC. GitHub redirects the operator's browser here (GET) after the
        install handshake. When the GitHub App has OAuth enabled, configure this
        URL as the "Callback URL" — GitHub sends code+state (no installation_id).
        The handler exchanges the code for a user token, then resolves the
        installation_id via GET /user/installations. When OAuth is disabled,
        configure as the "Setup URL" — GitHub sends installation_id+state directly.
        On success, redirects to the frontend teams page.

  - `POST   /api/v1/github/install-callback`
        PUBLIC. Same logic as the GET callback but accepts params as a JSON
        request body — kept for API clients and existing tests.

  - `GET    /api/v1/teams/{team_id}/github/installations`
        Team-admin gated. Lists installations bound to the team, ordered by
        `created_at` DESC.

  - `DELETE /api/v1/teams/{team_id}/github/installations/{id}`
        Team-admin gated. Removes the local row only — does NOT call GitHub
        (the App install on GitHub is operator-managed; revocation lives at
        github.com). 404 covers both missing rows and rows belonging to a
        different team so existence is not enumerable across teams.

Logging discipline (slice observability contract):
  INFO  github_install_url_issued team_id=<uuid> actor_id=<uuid> state_jti=<8>
  INFO  github_install_callback_accepted team_id=<uuid> installation_id=<id>
        account_login=<login> account_type=<type> state_jti=<8>
  WARN  github_install_callback_state_invalid reason=<...> presented_jti=<8|NA>
  WARN  github_install_callback_team_reassigned old_team_id=<uuid>
        new_team_id=<uuid> installation_id=<id>
  INFO  github_installation_deleted actor_id=<uuid> team_id=<uuid>
        installation_id=<id>

The full state JWT is NEVER logged — only the 8-char `jti` prefix. The
orchestrator lookup is reached through `httpx.AsyncClient`; tests
monkeypatch the module-level `httpx` import (MEM172/MEM184) to script
responses without booting the orchestrator.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.admin import (
    GITHUB_APP_CLIENT_ID_KEY,
    GITHUB_APP_CLIENT_SECRET_KEY,
    GITHUB_APP_SLUG_KEY,
)
from app.api.team_access import assert_caller_is_team_admin
from app.core.config import settings
from app.core.encryption import SystemSettingDecryptError, decrypt_setting
from app.models import (
    GitHubAppInstallation,
    GitHubAppInstallationPublic,
    InstallCallbackBody,
    InstallUrlResponse,
    SystemSetting,
    Team,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github"])


# State-JWT contract. Locked here so the e2e test (T04) can re-derive the
# expected payload without importing route internals.
_STATE_ALGO = "HS256"
_STATE_ISS = "perpetuity-install"
_STATE_AUD = "github-install"
_STATE_TTL_SECONDS = 600  # 10 minutes — covers a slow operator round-trip.

# Orchestrator HTTP timeout. 10s is generous for a single GitHub-side
# /app/installations/{id} GET; aligns with the slice plan's failure-mode
# table.
_ORCH_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


# ---------------------------------------------------------------------------
# State-JWT helpers
# ---------------------------------------------------------------------------


def _jti_prefix(jti: str | None) -> str:
    """Return the 8-char log-safe prefix of a jti (or 'NA' on missing)."""
    if not jti or not isinstance(jti, str):
        return "NA"
    return jti[:8]


def _mint_install_state(team_id: uuid.UUID) -> tuple[str, datetime, str]:
    """Mint a signed install-state JWT bound to `team_id`.

    Returns (token, exp_dt, jti). The jti is `secrets.token_urlsafe(16)` —
    enough entropy to make state tokens single-use in practice without
    introducing a server-side replay store (the 10-min expiry is the
    primary defense; the jti exists so logs can correlate issuance to the
    callback that consumes it).
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=_STATE_TTL_SECONDS)
    jti = secrets.token_urlsafe(16)
    payload = {
        "team_id": str(team_id),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": _STATE_ISS,
        "aud": _STATE_AUD,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=_STATE_ALGO)
    return token, exp, jti


def _decode_install_state(state_token: str) -> dict[str, Any]:
    """Decode and validate the install-state JWT.

    Raises HTTPException 400 with a stable detail string on every failure
    mode so the FE can branch on `detail` rather than parsing free-form
    error text. The presented jti (if recoverable) is logged on every
    rejection so an operator can grep for the correlated issuance line.
    """
    if not state_token:
        logger.warning(
            "github_install_callback_state_invalid reason=empty presented_jti=NA"
        )
        raise HTTPException(status_code=400, detail="install_state_invalid")

    # Best-effort jti extraction for the error log line. We deliberately do
    # NOT skip signature verification here — we only peek at the payload if
    # the actual decode raises, and that peek tolerates anything (including
    # a 'state_token' that is not a JWT at all).
    presented_jti = "NA"
    try:
        payload = jwt.decode(
            state_token,
            settings.SECRET_KEY,
            algorithms=[_STATE_ALGO],
            audience=_STATE_AUD,
            issuer=_STATE_ISS,
        )
    except jwt.ExpiredSignatureError:
        # Pull the jti out of the (verified-modulo-expiry) payload for log
        # correlation. Decoding with verify_exp=False is safe — we have
        # already proven the signature is good (ExpiredSignatureError is
        # only raised after signature verification succeeds).
        try:
            stale = jwt.decode(
                state_token,
                settings.SECRET_KEY,
                algorithms=[_STATE_ALGO],
                audience=_STATE_AUD,
                issuer=_STATE_ISS,
                options={"verify_exp": False},
            )
            presented_jti = _jti_prefix(stale.get("jti"))
        except Exception:  # noqa: BLE001 — log-only fallback
            presented_jti = "NA"
        logger.warning(
            "github_install_callback_state_invalid reason=expired presented_jti=%s",
            presented_jti,
        )
        raise HTTPException(status_code=400, detail="install_state_expired")
    except jwt.InvalidTokenError:
        logger.warning(
            "github_install_callback_state_invalid reason=bad_signature presented_jti=NA"
        )
        raise HTTPException(status_code=400, detail="install_state_invalid")

    return payload


# ---------------------------------------------------------------------------
# Orchestrator lookup helper
# ---------------------------------------------------------------------------


async def _orch_lookup_installation(installation_id: int) -> dict[str, Any]:
    """Ask the orchestrator for {account_login, account_type} for an install.

    The orchestrator owns the GitHub App private key and is the only side
    that can authenticate against GitHub's /app/installations/{id} endpoint
    — this proxy keeps the credential boundary intact. Failure modes are
    shaped per the slice plan's failure-mode table: every error path
    surfaces as HTTPException 502 with `detail='github_lookup_failed'` and
    a structured `reason` so the install-callback caller can distinguish
    transient orchestrator outage from a malformed response.
    """
    base = settings.ORCHESTRATOR_BASE_URL.rstrip("/")
    url = f"{base}/v1/installations/{installation_id}/lookup"
    headers = {"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            r = await c.get(url, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        logger.warning(
            "github_lookup_failed installation_id=%s reason=timeout",
            installation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"detail": "github_lookup_failed", "reason": "timeout"},
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "github_lookup_failed installation_id=%s reason=transport err=%s",
            installation_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"detail": "github_lookup_failed", "reason": "transport"},
        )

    if r.status_code != 200:
        logger.warning(
            "github_lookup_failed installation_id=%s reason=%s",
            installation_id,
            r.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_lookup_failed",
                "reason": str(r.status_code),
            },
        )

    try:
        body = r.json()
    except ValueError:
        logger.warning(
            "github_lookup_failed installation_id=%s reason=malformed_lookup_response",
            installation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_lookup_failed",
                "reason": "malformed_lookup_response",
            },
        )
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("account_login"), str)
        or not isinstance(body.get("account_type"), str)
    ):
        logger.warning(
            "github_lookup_failed installation_id=%s reason=malformed_lookup_response",
            installation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_lookup_failed",
                "reason": "malformed_lookup_response",
            },
        )
    return body


# ---------------------------------------------------------------------------
# OAuth code-exchange helper
# ---------------------------------------------------------------------------


async def _resolve_installation_id_from_oauth_code(
    session: Any, code: str
) -> int:
    """Exchange a GitHub OAuth `code` for a user token, then resolve the installation_id.

    Called when GitHub's OAuth Callback URL flow sends `code` + `state`
    instead of `installation_id` + `state`. Steps:
      1. Read client_id (non-sensitive JSONB) and client_secret (Fernet-encrypted)
         from system_settings.
      2. POST to github.com/login/oauth/access_token to exchange the code.
      3. GET /user/installations with the resulting token to find the installation
         that was just granted. Returns the most-recently-granted installation_id.

    Raises HTTPException 502 on any GitHub API error, 503 if credentials are
    missing or unreadable.
    """
    # Read client_id
    client_id_row = session.get(SystemSetting, GITHUB_APP_CLIENT_ID_KEY)
    if (
        client_id_row is None
        or not client_id_row.has_value
        or not isinstance(client_id_row.value, str)
        or not client_id_row.value
    ):
        logger.warning(
            "github_oauth_exchange_failed reason=client_id_not_configured"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_not_configured",
        )
    client_id: str = client_id_row.value

    # Read client_secret (encrypted)
    secret_row = session.get(SystemSetting, GITHUB_APP_CLIENT_SECRET_KEY)
    if secret_row is None or not secret_row.has_value or not secret_row.value_encrypted:
        logger.warning(
            "github_oauth_exchange_failed reason=client_secret_not_configured"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_not_configured",
        )
    try:
        client_secret = decrypt_setting(bytes(secret_row.value_encrypted))
    except SystemSettingDecryptError:
        logger.warning(
            "github_oauth_exchange_failed reason=client_secret_decrypt_error"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_credential_error",
        )

    # Exchange code for access token
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            token_resp = await c.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "github_oauth_exchange_failed reason=token_request_error err=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    if token_resp.status_code != 200:
        logger.warning(
            "github_oauth_exchange_failed reason=token_request_status status=%s",
            token_resp.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    try:
        token_body = token_resp.json()
    except ValueError:
        logger.warning(
            "github_oauth_exchange_failed reason=token_response_malformed"
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    access_token = token_body.get("access_token")
    if not access_token or not isinstance(access_token, str):
        error = token_body.get("error", "unknown")
        logger.warning(
            "github_oauth_exchange_failed reason=no_access_token error=%s",
            error,
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    # Fetch installations accessible to this user token
    try:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            installs_resp = await c.get(
                "https://api.github.com/user/installations",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "github_oauth_exchange_failed reason=installations_request_error err=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    if installs_resp.status_code != 200:
        logger.warning(
            "github_oauth_exchange_failed reason=installations_request_status status=%s",
            installs_resp.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    try:
        installs_body = installs_resp.json()
        installations = installs_body.get("installations", [])
    except (ValueError, AttributeError):
        logger.warning(
            "github_oauth_exchange_failed reason=installations_response_malformed"
        )
        raise HTTPException(
            status_code=502,
            detail="github_oauth_exchange_failed",
        )

    if not installations:
        logger.warning(
            "github_oauth_exchange_failed reason=no_installations_found"
        )
        raise HTTPException(
            status_code=400,
            detail="github_no_installation_found",
        )

    # Return the installation with the highest (most recent) id.
    # GitHub returns installations in descending creation order but we sort
    # defensively to handle any ordering the API may return.
    installation_id: int = max(
        inst["id"] for inst in installations if isinstance(inst.get("id"), int)
    )
    logger.info(
        "github_oauth_code_exchanged installation_id=%s total_installations=%s",
        installation_id,
        len(installations),
    )
    return installation_id


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/github/install-url", response_model=InstallUrlResponse
)
def get_github_install_url(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """Mint an install-state JWT and return the GitHub App install URL.

    Requires the caller to be a team admin. Reads `github_app_slug` from
    `system_settings` to build the install URL — the slug is the app's
    short name used in github.com/apps/{slug}/installations/new (distinct
    from the numeric App ID and the OAuth Client ID). When the operator has
    not yet seeded the slug, returns 404 `github_app_not_configured` so the
    FE can prompt the system admin to fill in the missing setting.
    """
    assert_caller_is_team_admin(session, team_id, current_user.id)

    slug_row = session.get(SystemSetting, GITHUB_APP_SLUG_KEY)
    if (
        slug_row is None
        or not slug_row.has_value
        or not isinstance(slug_row.value, str)
        or not slug_row.value
    ):
        raise HTTPException(
            status_code=404, detail="github_app_not_configured"
        )
    app_slug: str = slug_row.value

    state, exp, jti = _mint_install_state(team_id)
    install_url = (
        f"{settings.GITHUB_APP_INSTALL_URL_BASE.rstrip('/')}"
        f"/apps/{app_slug}/installations/new?state={state}"
    )

    logger.info(
        "github_install_url_issued team_id=%s actor_id=%s state_jti=%s",
        team_id,
        current_user.id,
        _jti_prefix(jti),
    )
    return InstallUrlResponse(
        install_url=install_url,
        state=state,
        expires_at=exp,
    )


async def _process_install_callback(
    session: Any,
    installation_id: int,
    state: str,
) -> GitHubAppInstallationPublic:
    """Shared core for both GET and POST install-callback handlers.

    Validates the state JWT, confirms the team exists, looks up the
    installation via the orchestrator, and UPSERTs the row. Returns the
    persisted installation record. Raises HTTPException on any validation or
    upstream error — callers decide how to surface that to the browser.
    """
    payload = _decode_install_state(state)

    presented_jti = _jti_prefix(payload.get("jti"))
    raw_team_id = payload.get("team_id")
    try:
        state_team_id = uuid.UUID(str(raw_team_id))
    except (TypeError, ValueError):
        logger.warning(
            "github_install_callback_state_invalid reason=team_unknown presented_jti=%s",
            presented_jti,
        )
        raise HTTPException(status_code=400, detail="install_state_team_unknown")

    team = session.get(Team, state_team_id)
    if team is None:
        logger.warning(
            "github_install_callback_state_invalid reason=team_unknown presented_jti=%s",
            presented_jti,
        )
        raise HTTPException(status_code=400, detail="install_state_team_unknown")

    # Orchestrator hop. Errors raise 502 from inside the helper.
    lookup = await _orch_lookup_installation(installation_id)
    account_login: str = lookup["account_login"]
    account_type: str = lookup["account_type"]

    existing = session.exec(
        select(GitHubAppInstallation).where(
            GitHubAppInstallation.installation_id == installation_id
        )
    ).first()
    if existing is not None and existing.team_id != state_team_id:
        logger.warning(
            "github_install_callback_team_reassigned old_team_id=%s"
            " new_team_id=%s installation_id=%s",
            existing.team_id,
            state_team_id,
            installation_id,
        )

    upsert = text(
        """
        INSERT INTO github_app_installations
            (id, team_id, installation_id, account_login, account_type, created_at)
        VALUES
            (:id, :team_id, :installation_id, :account_login, :account_type, NOW())
        ON CONFLICT (installation_id) DO UPDATE
        SET team_id = EXCLUDED.team_id,
            account_login = EXCLUDED.account_login,
            account_type = EXCLUDED.account_type
        RETURNING id, team_id, installation_id, account_login, account_type, created_at
        """
    )
    result = session.execute(
        upsert,
        {
            "id": uuid.uuid4(),
            "team_id": state_team_id,
            "installation_id": installation_id,
            "account_login": account_login,
            "account_type": account_type,
        },
    )
    row = result.one()
    session.commit()

    logger.info(
        "github_install_callback_accepted team_id=%s installation_id=%s"
        " account_login=%s account_type=%s state_jti=%s",
        state_team_id,
        installation_id,
        account_login,
        account_type,
        presented_jti,
    )

    return GitHubAppInstallationPublic(
        id=row.id,
        team_id=row.team_id,
        installation_id=row.installation_id,
        account_login=row.account_login,
        account_type=row.account_type,
        created_at=row.created_at,
    )


@router.get("/github/install-callback")
async def github_install_callback_get(
    *,
    session: SessionDep,
    installation_id: int | None = Query(default=None, ge=1),
    setup_action: str | None = Query(default=None, max_length=64),
    state: str | None = Query(default=None, min_length=1),
    code: str | None = Query(default=None),
) -> RedirectResponse:
    """Browser-facing GET callback — supports both Setup URL and OAuth Callback URL flows.

    OAuth flow (GitHub App has "Identifying and authorizing users" enabled):
      GitHub sends code+state. This handler exchanges the code for a user
      access token, then calls GET /user/installations to resolve the
      installation_id. Requires github_app_client_id and github_app_client_secret
      to be configured in system_settings.

    Setup URL flow (OAuth disabled):
      GitHub sends installation_id+setup_action+state directly. No code exchange.

    All params are optional at the transport layer so FastAPI never 422-rejects
    a GitHub redirect. The handler redirects to the frontend with a
    github_install_error param on any failure so the UI can surface a toast.
    """
    frontend_teams = f"{settings.FRONTEND_HOST.rstrip('/')}/teams"

    if not state:
        logger.warning(
            "github_install_callback_missing_params missing=state"
        )
        return RedirectResponse(
            url=f"{frontend_teams}?github_install_error=missing_params",
            status_code=302,
        )

    # When GitHub's OAuth Callback URL flow is used, GitHub sends code+state
    # but NOT installation_id. Exchange the code for a user token and resolve
    # the installation_id via /user/installations.
    resolved_installation_id = installation_id
    if resolved_installation_id is None and code:
        try:
            resolved_installation_id = await _resolve_installation_id_from_oauth_code(
                session, code
            )
        except HTTPException as exc:
            detail = exc.detail
            reason = detail.get("detail", "unknown") if isinstance(detail, dict) else str(detail)
            logger.warning(
                "github_install_callback_oauth_exchange_failed reason=%s", reason
            )
            return RedirectResponse(
                url=f"{frontend_teams}?github_install_error={reason}",
                status_code=302,
            )

    if not resolved_installation_id:
        logger.warning(
            "github_install_callback_missing_params missing=installation_id"
        )
        return RedirectResponse(
            url=f"{frontend_teams}?github_install_error=missing_params",
            status_code=302,
        )

    try:
        await _process_install_callback(session, resolved_installation_id, state)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            reason = detail.get("detail", "unknown")
        else:
            reason = str(detail)
        return RedirectResponse(
            url=f"{frontend_teams}?github_install_error={reason}",
            status_code=302,
        )
    return RedirectResponse(url=frontend_teams, status_code=302)


@router.post(
    "/github/install-callback", response_model=GitHubAppInstallationPublic
)
async def github_install_callback(
    *,
    session: SessionDep,
    body: InstallCallbackBody,
) -> Any:
    """API-client callback — same logic as GET but accepts a JSON body.

    Kept for API clients and backward compatibility. Browser-initiated
    installs should use the GET endpoint (GitHub's Setup URL redirect).
    """
    return await _process_install_callback(
        session, body.installation_id, body.state
    )


@router.get("/teams/{team_id}/github/installations")
def list_github_installations(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> dict[str, Any]:
    """List installations bound to a team, newest first.

    Team-admin gated — same shape as every other team-mutation surface so
    the auth boundary is uniform across the slice. Returns the
    `{data, count}` envelope used by the FE listing components.
    """
    assert_caller_is_team_admin(session, team_id, current_user.id)

    rows = session.exec(
        select(GitHubAppInstallation)
        .where(GitHubAppInstallation.team_id == team_id)
        .order_by(col(GitHubAppInstallation.created_at).desc())
    ).all()
    data = [
        GitHubAppInstallationPublic(
            id=row.id,
            team_id=row.team_id,
            installation_id=row.installation_id,
            account_login=row.account_login,
            account_type=row.account_type,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return {"data": data, "count": len(data)}


@router.delete(
    "/teams/{team_id}/github/installations/{installation_row_id}",
    status_code=status.HTTP_200_OK,
)
def delete_github_installation(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
    installation_row_id: uuid.UUID,
) -> dict[str, Any]:
    """Remove a local installation record. Does NOT call GitHub.

    The GitHub-side install is operator-managed and revoked at github.com;
    deleting the local row simply forgets the team↔installation binding.
    Team-admin gated. 404 covers both missing rows and rows owned by a
    different team — keeps cross-team existence non-enumerable.
    """
    assert_caller_is_team_admin(session, team_id, current_user.id)

    row = session.get(GitHubAppInstallation, installation_row_id)
    if row is None or row.team_id != team_id:
        raise HTTPException(status_code=404, detail="installation_not_found")

    installation_id = row.installation_id
    session.delete(row)
    session.commit()

    logger.info(
        "github_installation_deleted actor_id=%s team_id=%s installation_id=%s",
        current_user.id,
        team_id,
        installation_id,
    )
    return {"id": str(installation_row_id), "deleted": True}
