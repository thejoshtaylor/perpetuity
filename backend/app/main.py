import logging

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.api.team_secrets import (
    MissingTeamSecretError,
    TeamSecretDecryptError,
)
from app.core.config import settings
from app.core.encryption import SystemSettingDecryptError

logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.ENVIRONMENT != "local":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)


# Single fan-in for sensitive system_settings decrypt failures (M004/S01).
# Every decrypt site (admin GETs, future S02 JWT-sign callers, etc.) raises
# `SystemSettingDecryptError(key=...)` from the `decrypt_setting` helper —
# they never catch. This handler is the one place that translates the
# failure into an operator-visible 503 + ERROR log. The plaintext MUST NOT
# appear in either the response body or the log line; only the row key is
# named so triage can localize without leaking the secret.
@app.exception_handler(SystemSettingDecryptError)
async def _system_settings_decrypt_failed_handler(
    _request: Request, exc: SystemSettingDecryptError
) -> JSONResponse:
    logger.error(
        "system_settings_decrypt_failed key=%s",
        exc.key,
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": "system_settings_decrypt_failed",
            "key": exc.key,
        },
    )


# Mirrors the system-scoped handler above for team-scoped secrets (M005/S01).
# `get_team_secret` is the single decrypt site for `team_secrets` rows; on
# `cryptography.fernet.InvalidToken` it raises `TeamSecretDecryptError`. The
# handler translates that into a 503 with a stable detail key and emits an
# ERROR log naming team_id + key only. The plaintext, ciphertext, and any
# value prefix MUST NOT appear in either surface — the redaction sweep
# extension in S01 (`sk-`, `sk-ant-`) gates this.
@app.exception_handler(TeamSecretDecryptError)
async def _team_secret_decrypt_failed_handler(
    _request: Request, exc: TeamSecretDecryptError
) -> JSONResponse:
    logger.error(
        "team_secret_decrypt_failed team_id=%s key=%s",
        exc.team_id,
        exc.key,
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": "team_secret_decrypt_failed",
            "key": exc.key,
        },
    )


# Downstream callers (S02+) catch `MissingTeamSecretError` directly to
# surface step-level "missing_team_secret" errors. The HTTP fan-in here is
# for the rarer case where the helper bubbles up through a request handler
# unguarded — translates to 404 with the same shape the GET-single route
# returns when a row is absent so clients see one consistent error key.
@app.exception_handler(MissingTeamSecretError)
async def _team_secret_not_set_handler(
    _request: Request, exc: MissingTeamSecretError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "detail": "team_secret_not_set",
            "key": exc.key,
        },
    )


# Set all CORS enabled origins
if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix=settings.API_V1_STR)
