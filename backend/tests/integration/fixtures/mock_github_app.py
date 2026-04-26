"""Minimal GitHub App mock used by M004/S02/T04 e2e.

Mounted into a python:3.12-slim sibling container on `perpetuity_default`
so the ephemeral orchestrator under test can reach
`http://mock-github-<uuid>:8080` instead of the real `api.github.com`.

Routes mimic the two GitHub endpoints the orchestrator calls:

    POST /app/installations/{id}/access_tokens
        Verifies the inbound RS256 App JWT against `PUBLIC_KEY_PEM`
        with `iss=str(GITHUB_APP_ID)`. On success returns
        `{"token": FIXED_TOKEN, "expires_at": "<iso8601 +1h>"}`.

    GET  /app/installations/{id}
        Returns `{"id": id,
                   "account": {"login": "test-org", "type": "Organization"}}`.

The mock deliberately keeps no state — every request reverifies the JWT
against the env-provided public key so a misconfigured ephemeral
orchestrator (wrong key, wrong app id) fails closed with a 401 the test
can assert on. The fixed token is what the test compares against on the
mint+cache scenario; the mock-github container is the ONE place that
container-log redaction is NOT enforced (the issued token is part of the
canned response by design).

Env contract:

    PUBLIC_KEY_PEM   — RSA public key the test generates per-run
    FIXED_TOKEN      — the canned installation token (e.g. ``ghs_test...``)
    GITHUB_APP_ID    — int, must match what the test seeded into
                       `system_settings.github_app_id`
    REJECT_JWT       — optional, "1" to make /access_tokens return 401
                       (used by the optional scenario G to prove the
                       backend's install-callback wraps a 502 lookup
                       failure correctly)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import FastAPI, HTTPException, Request


def _env_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"mock_github_app: env {name!r} is required")
    return val


PUBLIC_KEY_PEM = _env_required("PUBLIC_KEY_PEM")
FIXED_TOKEN = _env_required("FIXED_TOKEN")
GITHUB_APP_ID = int(_env_required("GITHUB_APP_ID"))
REJECT_JWT = os.environ.get("REJECT_JWT", "0") == "1"


app = FastAPI(title="mock-github")


def _verify_app_jwt(request: Request) -> None:
    """Mirror what GitHub does: HS256-impossible — must be RS256 from our App.

    Raises HTTPException(401) on missing/invalid Bearer or signature mismatch.
    The error reason intentionally stays terse so test logs stay clean.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer")
    token = auth[len("Bearer "):]
    try:
        jwt.decode(
            token,
            PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            issuer=str(GITHUB_APP_ID),
            options={"require": ["iat", "exp", "iss"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=401, detail=f"invalid_jwt:{type(exc).__name__}"
        )


@app.post("/app/installations/{installation_id}/access_tokens")
async def access_tokens(installation_id: int, request: Request) -> dict:
    if REJECT_JWT:
        raise HTTPException(status_code=401, detail="rejected_for_test")
    _verify_app_jwt(request)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    return {"token": FIXED_TOKEN, "expires_at": expires_at}


@app.get("/app/installations/{installation_id}")
async def lookup(installation_id: int, request: Request) -> dict:
    _verify_app_jwt(request)
    return {
        "id": installation_id,
        "account": {"login": "test-org", "type": "Organization"},
    }


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
