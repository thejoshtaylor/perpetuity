import logging

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
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
