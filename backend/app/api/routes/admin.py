"""System admin endpoints — bypass per-team membership.

Every route in this module is gated by `get_current_active_superuser`, which
already enforces `current_user.role == UserRole.system_admin` in deps.py.
Per-team membership checks (`_assert_caller_is_team_member` /
`_assert_caller_is_team_admin` from teams.py) are deliberately NOT reused —
system admins can inspect any team's roster and promote any user.

Out of scope for this slice (S05): demote-from-system-admin. The promote
endpoint is one-directional; demotion is future work.

Logs are UUID-only (matches S03 redaction posture) — no email or team name.
"""
import base64
import json
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlmodel import Session, col, func, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.core.encryption import encrypt_setting
from app.models import (
    SystemSetting,
    SystemSettingGenerateResponse,
    SystemSettingPublic,
    SystemSettingPut,
    SystemSettingPutResponse,
    SystemSettingShrinkWarning,
    Team,
    TeamMember,
    TeamMemberPublic,
    TeamMembersPublic,
    TeamPublic,
    User,
    UserPublic,
    UserRole,
    VapidKeysGenerateResponse,
    WorkspaceVolume,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_active_superuser)],
)


@router.get("/teams")
def read_all_teams(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List every team in the system, paginated, ordered by created_at DESC.

    Returns `{data: [TeamPublic, ...], count: int}` where `count` is the
    unfiltered total (so the FE can render Prev/Next correctly even with
    skip/limit applied). Mirrors the count+skip/limit pattern in
    `users.py::read_users`.
    """
    count_statement = select(func.count()).select_from(Team)
    count = session.exec(count_statement).one()

    statement = (
        select(Team)
        .order_by(col(Team.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    teams = session.exec(statement).all()
    data = [TeamPublic.model_validate(team, from_attributes=True) for team in teams]

    logger.info(
        "admin_teams_listed actor_id=%s skip=%s limit=%s count=%s",
        current_user.id,
        skip,
        limit,
        len(data),
    )
    return {"data": data, "count": count}


@router.get(
    "/teams/{team_id}/members", response_model=TeamMembersPublic
)
def read_admin_team_members(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """Return the roster of any team — does NOT require caller membership.

    - 404 if team missing.
    - 200 `{data: [{user_id, email, full_name, role}, ...], count: int}`.

    Note: deliberately does not call `_assert_caller_is_team_member` from
    teams.py — system admin must be able to inspect teams they aren't on.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    statement = (
        select(User, TeamMember.role)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(col(User.email))
    )
    rows = session.exec(statement).all()
    data = [
        TeamMemberPublic(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=role,
        )
        for user, role in rows
    ]
    logger.info(
        "admin_team_members_listed actor_id=%s team_id=%s count=%s",
        current_user.id,
        team_id,
        len(data),
    )
    return TeamMembersPublic(data=data, count=len(data))


@router.post(
    "/users/{user_id}/promote-system-admin", response_model=UserPublic
)
def promote_system_admin(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    user_id: uuid.UUID,
) -> Any:
    """Promote a user to UserRole.system_admin. Idempotent.

    - 404 if target user does not exist.
    - 200 with the (possibly unchanged) UserPublic on success.
    - If the target is already system_admin, no DB write is performed and
      the log line records `already_admin=true`.

    Demotion (system_admin → user) is intentionally not exposed — out of
    scope for S05. A future slice can add it with last-admin guards.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    already_admin = target.role == UserRole.system_admin
    if not already_admin:
        target.role = UserRole.system_admin
        try:
            session.add(target)
            session.commit()
            session.refresh(target)
        except Exception:
            session.rollback()
            logger.warning(
                "system_admin_promote_tx_rollback actor_id=%s target_user_id=%s",
                current_user.id,
                user_id,
            )
            raise

    logger.info(
        "system_admin_promoted actor_id=%s target_user_id=%s already_admin=%s",
        current_user.id,
        user_id,
        str(already_admin).lower(),
    )
    return target


# ---------------------------------------------------------------------------
# System settings — generic key/value store backing admin-tunable globals.
#
# Reject-by-default: PUTs to keys not in `_VALIDATORS` return 422. This closes
# the foot-gun where a typo in the key would silently add a row that nothing
# reads. New keys must be registered here alongside their validator.
#
# Logging discipline: never log the raw value — sensitive settings (PEMs,
# webhook secrets) MUST never appear in logs or HTTPException details. We log
# presence/absence, the key name, the actor, and the `sensitive` flag only.
# ---------------------------------------------------------------------------


WORKSPACE_VOLUME_SIZE_GB_KEY = "workspace_volume_size_gb"
IDLE_TIMEOUT_SECONDS_KEY = "idle_timeout_seconds"
MIRROR_IDLE_TIMEOUT_SECONDS_KEY = "mirror_idle_timeout_seconds"
GROK_STT_API_KEY = "grok_stt_api_key"
MAX_VOICE_TRANSCRIBES_PER_HOUR_GLOBAL = "max_voice_transcribes_per_hour_global"

GITHUB_APP_ID_KEY = "github_app_id"
GITHUB_APP_CLIENT_ID_KEY = "github_app_client_id"
GITHUB_APP_CLIENT_SECRET_KEY = "github_app_client_secret"
GITHUB_APP_SLUG_KEY = "github_app_slug"
GITHUB_APP_PRIVATE_KEY_KEY = "github_app_private_key"
GITHUB_APP_WEBHOOK_SECRET_KEY = "github_app_webhook_secret"

# M005/S03 — VAPID keypair for Web Push. Public is non-sensitive JSONB
# (browsers fetch it unauthenticated); private is Fernet-encrypted in
# value_encrypted. Both are server-generated by the one-shot
# /admin/settings/vapid_keys/generate endpoint.
VAPID_PUBLIC_KEY_KEY = "vapid_public_key"
VAPID_PRIVATE_KEY_KEY = "vapid_private_key"

# Per-key opt-out for the "generator-implies-sensitive" module-load assertion.
# vapid_public_key is the one principled exception: it's server-generated as
# part of the keypair (we don't want operators pasting half a keypair) but
# its value is intentionally world-readable — every browser fetches it from
# /api/v1/push/vapid_public_key. The assertion is widened, not disabled —
# only keys present in this set are permitted to declare a generator while
# remaining sensitive=False.
_NON_SENSITIVE_GENERATOR_OK: frozenset[str] = frozenset(
    {VAPID_PUBLIC_KEY_KEY}
)

# Url-safe-base64 max length for VAPID public key. RFC 8292 §3.2 specifies
# the uncompressed P-256 point (65 bytes) which encodes to 88 chars
# url-safe-base64 with padding stripped. We bound at 256 to leave headroom
# for any operator-pasted variant without admitting arbitrarily long blobs.
_VAPID_PUBLIC_KEY_MAX_LEN = 256

# Bound the PEM body so a misconfigured paste can't push an arbitrarily large
# blob through the API and into the DB. 16384 chars covers a 4096-bit RSA key
# in PEM form with comfortable headroom for armor and metadata; 64 is the
# floor that a structurally valid `-----BEGIN ... ----- ... -----END ... -----`
# can fit into.
_PEM_MIN_LEN = 64
_PEM_MAX_LEN = 16384


@dataclass(frozen=True)
class _SettingSpec:
    """Per-key registry entry.

    `validator` is None for sensitive keys whose only writer is the server
    (generator output is trusted by construction). `sensitive=True` flips the
    storage path from JSONB `value` to BYTEA `value_encrypted` and redacts the
    value from every read surface. `generator`, when present, is the
    server-side seed function for `POST /admin/settings/{key}/generate`.
    """

    validator: Callable[[Any], None] | None
    sensitive: bool
    generator: Callable[[], str] | None


def _validate_workspace_volume_size_gb(value: Any) -> None:
    """Mirror the orchestrator's volume_store range (1..256 GiB).

    bool is a subclass of int in Python — reject it explicitly so a JSON
    `true` doesn't silently coerce to 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": WORKSPACE_VOLUME_SIZE_GB_KEY,
                "reason": "must be int in 1..256",
            },
        )
    if not (1 <= value <= 256):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": WORKSPACE_VOLUME_SIZE_GB_KEY,
                "reason": "must be int in 1..256",
            },
        )


def _validate_idle_timeout_seconds(value: Any) -> None:
    """Mirror the orchestrator's reaper resolver range (1..86400 seconds).

    Same shape as the volume size validator — bool is rejected explicitly
    so JSON `true` doesn't coerce to 1. The new value just biases the
    next reaper tick; no partial-apply warnings are emitted because there
    is no per-row state to reconcile (unlike workspace_volume_size_gb).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 1..86400",
            },
        )
    if not (1 <= value <= 86400):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 1..86400",
            },
        )


def _validate_mirror_idle_timeout_seconds(value: Any) -> None:
    """Per-team mirror reaper window — int seconds in 60..86400.

    Floor of 60s keeps the reaper from being weaponized into a DoS on the
    mirror container (a low timeout would tear down on every tick). Cap of
    86400 (24h) matches the user-session reaper. Default applied at the
    orchestrator side is 1800s (30m). bool rejected explicitly so JSON
    `true` doesn't silently coerce to 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": MIRROR_IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 60..86400",
            },
        )
    if not (60 <= value <= 86400):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": MIRROR_IDLE_TIMEOUT_SECONDS_KEY,
                "reason": "must be int in 60..86400",
            },
        )


def _validate_max_voice_transcribes_per_hour_global(value: Any) -> None:
    """Global per-hour voice transcription budget — int in 1..1_000_000."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": MAX_VOICE_TRANSCRIBES_PER_HOUR_GLOBAL,
                "reason": "must be int in 1..1000000",
            },
        )
    if not (1 <= value <= 1_000_000):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": MAX_VOICE_TRANSCRIBES_PER_HOUR_GLOBAL,
                "reason": "must be int in 1..1000000",
            },
        )


def _validate_github_app_id(value: Any) -> None:
    """GitHub App numeric ID. Stored in JSONB `value` (non-sensitive)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_ID_KEY,
                "reason": "must be int in 1..2**63-1",
            },
        )
    if not (1 <= value <= (2**63 - 1)):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_ID_KEY,
                "reason": "must be int in 1..2**63-1",
            },
        )


def _validate_github_app_client_id(value: Any) -> None:
    """GitHub App OAuth client ID. Non-empty ASCII string ≤255 chars."""
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_CLIENT_ID_KEY,
                "reason": "must be non-empty ASCII string ≤255 chars",
            },
        )
    if len(value) > 255 or not value.isascii():
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_CLIENT_ID_KEY,
                "reason": "must be non-empty ASCII string ≤255 chars",
            },
        )


def _validate_github_app_slug(value: Any) -> None:
    """GitHub App slug used to build the installation URL.

    The slug is the app's URL-safe name shown in
    https://github.com/apps/{slug}/installations/new — it is NOT the numeric
    App ID or the OAuth Client ID. Non-empty ASCII string, max 255 chars.
    """
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_SLUG_KEY,
                "reason": "must be non-empty ASCII string ≤255 chars",
            },
        )
    if len(value) > 255 or not value.isascii():
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_SLUG_KEY,
                "reason": "must be non-empty ASCII string ≤255 chars",
            },
        )


def _validate_github_app_private_key(value: Any) -> None:
    """Structural PEM check at the API boundary.

    We deliberately do NOT parse the key with
    `cryptography.hazmat.primitives.serialization.load_pem_private_key` here
    — that would pull the heavy hazmat layer onto every PUT. The structural
    check (begins-with `-----BEGIN`, contains `-----END`, bounded length) is
    the API contract; if the bytes happen to be non-PEM, S02's first
    JWT-sign call will surface a structured error at decrypt-and-sign time.
    Operator gets a fast PUT response; bad PEM surfaces at the call site
    that actually needs to use it. NEVER include the value in the error.
    """
    if not isinstance(value, str):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_PRIVATE_KEY_KEY,
                "reason": (
                    "must be a PEM-encoded string starting with '-----BEGIN'"
                    f" and length in {_PEM_MIN_LEN}..{_PEM_MAX_LEN}"
                ),
            },
        )
    if not (_PEM_MIN_LEN <= len(value) <= _PEM_MAX_LEN):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_PRIVATE_KEY_KEY,
                "reason": (
                    f"must be a PEM-encoded string of length"
                    f" {_PEM_MIN_LEN}..{_PEM_MAX_LEN}"
                ),
            },
        )
    if not value.startswith("-----BEGIN") or "-----END" not in value:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": GITHUB_APP_PRIVATE_KEY_KEY,
                "reason": (
                    "must be a PEM-encoded string with"
                    " '-----BEGIN' and '-----END' armor"
                ),
            },
        )


def _generate_webhook_secret() -> str:
    """Server-side seed for `github_app_webhook_secret`.

    `secrets.token_urlsafe(32)` yields ~43 url-safe base64 chars (256 bits of
    entropy). Re-calling `POST .../generate` is intentionally destructive
    (D025): a fresh secret breaks every in-flight webhook delivery until the
    GitHub App's webhook secret is rotated to match. The destructive
    semantics are an operator safety contract, not a bug.
    """
    return secrets.token_urlsafe(32)


def _validate_vapid_public_key(value: Any) -> None:
    """Structural check on a VAPID public key (P-256 raw point, b64url).

    The expected shape is a url-safe-base64 ASCII string of the uncompressed
    P-256 public point (65 bytes → ~88 chars b64url, no padding). We accept
    any non-empty ASCII string ≤ 256 chars to leave operator slack — the
    pywebpush library will surface a structured error at sign time if the
    bytes don't decode to a valid EC point. Bool rejected explicitly so a
    JSON `true` doesn't silently coerce to "True".
    """
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": VAPID_PUBLIC_KEY_KEY,
                "reason": (
                    "must be a non-empty url-safe-base64 ASCII string"
                    f" ≤ {_VAPID_PUBLIC_KEY_MAX_LEN} chars"
                ),
            },
        )
    if len(value) > _VAPID_PUBLIC_KEY_MAX_LEN or not value.isascii():
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "invalid_value_for_key",
                "key": VAPID_PUBLIC_KEY_KEY,
                "reason": (
                    "must be a non-empty url-safe-base64 ASCII string"
                    f" ≤ {_VAPID_PUBLIC_KEY_MAX_LEN} chars"
                ),
            },
        )


def _b64url_no_pad(raw: bytes) -> str:
    """Encode raw bytes as url-safe base64 with trailing '=' padding stripped.

    RFC 8292 §3.2 mandates this shape on the wire for VAPID. Centralized so
    the public-key and private-key generators stay byte-identical.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_vapid_keypair() -> tuple[str, str]:
    """Generate a fresh VAPID keypair → (public_b64url, private_b64url).

    Per RFC 8292 §3.2:
      - Public key is the uncompressed P-256 point (0x04 || X || Y), 65 bytes.
      - Private key is the raw 32-byte scalar.

    Both are url-safe-base64 with padding stripped. The pywebpush library
    accepts both raw-bytes and PEM forms; we standardize on b64url so the
    public key can be served verbatim to browsers without re-encoding.
    """
    private = ec.generate_private_key(ec.SECP256R1())
    public_raw = private.public_key().public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )
    # Private scalar as raw 32 bytes via DER → integer round-trip would be
    # heavier than necessary; cryptography exposes the private numbers
    # directly.
    private_int = private.private_numbers().private_value
    private_raw = private_int.to_bytes(32, byteorder="big")
    return _b64url_no_pad(public_raw), _b64url_no_pad(private_raw)


def _generate_vapid_public_part() -> str:
    """Generator hook for vapid_public_key — paired with the private generator.

    Both halves of the keypair are produced by the atomic
    /admin/settings/vapid_keys/generate endpoint; this hook exists so the
    _VALIDATORS registry stays uniform (every server-seeded key has a
    generator). It MUST NOT be called outside the atomic two-key write — a
    standalone generate of just the public half would emit an unpaired key
    that no one holds the private for.
    """
    raise RuntimeError(
        "vapid_public_key is generated atomically with vapid_private_key — "
        "use POST /admin/settings/vapid_keys/generate, not the per-key "
        "generate endpoint"
    )


def _generate_vapid_private_part() -> str:
    """Generator hook for vapid_private_key — paired with the public generator.

    Same atomic-pair contract as the public hook — see its docstring.
    """
    raise RuntimeError(
        "vapid_private_key is generated atomically with vapid_public_key — "
        "use POST /admin/settings/vapid_keys/generate, not the per-key "
        "generate endpoint"
    )


_VALIDATORS: dict[str, _SettingSpec] = {
    WORKSPACE_VOLUME_SIZE_GB_KEY: _SettingSpec(
        validator=_validate_workspace_volume_size_gb,
        sensitive=False,
        generator=None,
    ),
    IDLE_TIMEOUT_SECONDS_KEY: _SettingSpec(
        validator=_validate_idle_timeout_seconds,
        sensitive=False,
        generator=None,
    ),
    MIRROR_IDLE_TIMEOUT_SECONDS_KEY: _SettingSpec(
        validator=_validate_mirror_idle_timeout_seconds,
        sensitive=False,
        generator=None,
    ),
    GROK_STT_API_KEY: _SettingSpec(
        validator=None,
        sensitive=True,
        generator=None,
    ),
    MAX_VOICE_TRANSCRIBES_PER_HOUR_GLOBAL: _SettingSpec(
        validator=_validate_max_voice_transcribes_per_hour_global,
        sensitive=False,
        generator=None,
    ),
    GITHUB_APP_ID_KEY: _SettingSpec(
        validator=_validate_github_app_id,
        sensitive=False,
        generator=None,
    ),
    GITHUB_APP_CLIENT_ID_KEY: _SettingSpec(
        validator=_validate_github_app_client_id,
        sensitive=False,
        generator=None,
    ),
    GITHUB_APP_CLIENT_SECRET_KEY: _SettingSpec(
        validator=None,
        sensitive=True,
        generator=None,
    ),
    GITHUB_APP_SLUG_KEY: _SettingSpec(
        validator=_validate_github_app_slug,
        sensitive=False,
        generator=None,
    ),
    GITHUB_APP_PRIVATE_KEY_KEY: _SettingSpec(
        validator=_validate_github_app_private_key,
        sensitive=True,
        generator=None,
    ),
    GITHUB_APP_WEBHOOK_SECRET_KEY: _SettingSpec(
        validator=None,
        sensitive=True,
        generator=_generate_webhook_secret,
    ),
    # VAPID public key: non-sensitive (browsers fetch it unauthenticated)
    # but server-generated as half of an atomic keypair. The generator hook
    # raises if invoked alone — the atomic two-key write happens in
    # POST /admin/settings/vapid_keys/generate. Operator MAY also PUT a
    # value (validator runs) for the rare "I already have a keypair" path.
    VAPID_PUBLIC_KEY_KEY: _SettingSpec(
        validator=_validate_vapid_public_key,
        sensitive=False,
        generator=_generate_vapid_public_part,
    ),
    # VAPID private key: sensitive (Fernet-encrypted at rest, never logged).
    # No PUT validator — server-side seed only. Same atomic-pair contract:
    # the per-key generate endpoint refuses; vapid_keys/generate is the only
    # writer.
    VAPID_PRIVATE_KEY_KEY: _SettingSpec(
        validator=None,
        sensitive=True,
        generator=_generate_vapid_private_part,
    ),
}


# Module-load assertion: any key that registers a generator MUST also be
# sensitive UNLESS it is in the explicit non-sensitive-generator allowlist.
# Generators exist to seed server-side secrets; storing a generated value as
# plaintext JSONB would defeat the purpose for everything *except* the VAPID
# public key, which is intentionally world-readable but still server-paired
# with its private half. Catching this at import time keeps a future
# misregistration from silently shipping.
for _spec_key, _spec in _VALIDATORS.items():
    if (
        _spec.generator is not None
        and not _spec.sensitive
        and _spec_key not in _NON_SENSITIVE_GENERATOR_OK
    ):
        raise AssertionError(
            f"setting spec {_spec_key!r} declares a generator but"
            " sensitive=False; generators are sensitive-only by design"
            " (add to _NON_SENSITIVE_GENERATOR_OK if this is intentional)"
        )


def _compute_workspace_size_warnings(
    session: Session, new_value: int
) -> list[SystemSettingShrinkWarning]:
    """Return one warning row per existing volume whose size_gb > new_value.

    usage_bytes is reported as None in this slice — the backend container
    does not mount the workspace_volume host bind, so on-disk usage is not
    reachable. S04 will add a backend→orchestrator usage lookup; the schema
    is forward-compatible.
    """
    statement = (
        select(WorkspaceVolume)
        .where(WorkspaceVolume.size_gb > new_value)
        .order_by(col(WorkspaceVolume.created_at))
    )
    rows = session.exec(statement).all()
    return [
        SystemSettingShrinkWarning(
            user_id=row.user_id,
            team_id=row.team_id,
            size_gb=row.size_gb,
            usage_bytes=None,
        )
        for row in rows
    ]


def _redact(row: SystemSetting) -> SystemSettingPublic:
    """Project a SystemSetting row into its public, redaction-safe shape.

    Sensitive rows always return `value=None` regardless of whether
    `value_encrypted` is populated; the `has_value` boolean is the source
    of truth for the FE's `Set` vs `Replace` rendering decision.
    """
    return SystemSettingPublic(
        key=row.key,
        sensitive=row.sensitive,
        has_value=row.has_value,
        value=None if row.sensitive else row.value,
        updated_at=row.updated_at,
    )


@router.get("/settings")
def list_system_settings(
    session: SessionDep,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """List all registered system settings, ordered by key.

    Returns `{data: [SystemSettingPublic, ...], count}`. One entry per key in
    `_VALIDATORS` — keys with no DB row are returned with `has_value=False` so
    the frontend always renders the full settings panel on a fresh deployment.
    The full set is expected to stay tiny (one row per registered key), so no
    pagination. Sensitive rows have their `value` redacted to `null` — clients
    use `has_value` to render the `Set` vs `Replace` UI.
    """
    existing = {
        row.key: row
        for row in session.exec(select(SystemSetting)).all()
    }
    data: list[SystemSettingPublic] = []
    for key in sorted(_VALIDATORS.keys()):
        spec = _VALIDATORS[key]
        row = existing.get(key)
        if row is None:
            data.append(
                SystemSettingPublic(
                    key=key,
                    sensitive=spec.sensitive,
                    has_value=False,
                    value=None,
                    updated_at=None,
                )
            )
        else:
            data.append(_redact(row))
    logger.info(
        "system_settings_listed actor_id=%s count=%s",
        current_user.id,
        len(data),
    )
    return {"data": data, "count": len(data)}


@router.get("/settings/{key}", response_model=SystemSettingPublic)
def get_system_setting(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    key: str,
) -> Any:
    """Return a single system setting or 404. Sensitive rows are redacted."""
    row = session.get(SystemSetting, key)
    if row is None:
        raise HTTPException(status_code=404, detail="setting_not_found")
    logger.info(
        "system_setting_read actor_id=%s key=%s",
        current_user.id,
        key,
    )
    return _redact(row)


def _upsert_jsonb(session: Session, key: str, value: Any) -> Any:
    """UPSERT a non-sensitive JSONB value. Returns the resulting row."""
    upsert = text(
        """
        INSERT INTO system_settings
            (key, value, value_encrypted, sensitive, has_value, updated_at)
        VALUES
            (:key, CAST(:value AS JSONB), NULL, FALSE, TRUE, NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            value_encrypted = NULL,
            sensitive = FALSE,
            has_value = TRUE,
            updated_at = NOW()
        RETURNING key, value, value_encrypted, sensitive, has_value, updated_at
        """
    )
    result = session.execute(upsert, {"key": key, "value": json.dumps(value)})
    return result.one()


def _upsert_encrypted(session: Session, key: str, plaintext: str) -> Any:
    """Encrypt plaintext and UPSERT into BYTEA `value_encrypted`.

    The plaintext is consumed in this function and never logged or
    returned. `value` is forced NULL so a stale non-sensitive payload from
    a prior misregistration cannot linger on a sensitive row.
    """
    ciphertext = encrypt_setting(plaintext)
    upsert = text(
        """
        INSERT INTO system_settings
            (key, value, value_encrypted, sensitive, has_value, updated_at)
        VALUES
            (:key, NULL, :ct, TRUE, TRUE, NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = NULL,
            value_encrypted = EXCLUDED.value_encrypted,
            sensitive = TRUE,
            has_value = TRUE,
            updated_at = NOW()
        RETURNING key, value, value_encrypted, sensitive, has_value, updated_at
        """
    )
    result = session.execute(upsert, {"key": key, "ct": ciphertext})
    return result.one()


@router.put("/settings/{key}", response_model=SystemSettingPutResponse)
def put_system_setting(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    key: str,
    body: SystemSettingPut,
) -> Any:
    """Validate, UPSERT, and return the setting plus any shrink warnings.

    Reject-by-default on unknown keys. Per-key validators raise 422 with
    `{detail: 'invalid_value_for_key', key, reason}` on bad input.

    Sensitive keys (`spec.sensitive=True`) take the encrypted-storage path:
    the value is Fernet-encrypted, written to BYTEA `value_encrypted`, and
    `value` is NULLed. The PUT response for sensitive keys returns
    `value=None` — the plaintext does NOT cross the API boundary on PUT.

    Non-sensitive keys take the JSONB path and behave as in M002.

    For `workspace_volume_size_gb`, also computes the partial-apply shrink
    warnings (D015): rows with size_gb > new_value are reported but not
    rewritten. New volumes pick up the new default; existing rows keep their
    historical cap (cap divergence allowed).
    """
    spec = _VALIDATORS.get(key)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "unknown_setting_key", "key": key},
        )
    if spec.validator is not None:
        spec.validator(body.value)

    previous = session.get(SystemSetting, key)
    previous_value_present = previous is not None and previous.has_value

    if spec.sensitive:
        # Sensitive PUT path: validator already accepted the value, encrypt
        # and store. Force the plaintext to str (validator guarantees it
        # for the only sensitive PUT-able key today, github_app_private_key).
        if not isinstance(body.value, str):
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": "invalid_value_for_key",
                    "key": key,
                    "reason": "sensitive value must be a string",
                },
            )
        row = _upsert_encrypted(session, key, body.value)
        session.commit()
        warnings: list[SystemSettingShrinkWarning] = []
        # Sensitive keys never carry workspace-shrink semantics today; if a
        # future sensitive key needs a similar partial-apply hook, register
        # it explicitly rather than dispatching by key here.
    else:
        row = _upsert_jsonb(session, key, body.value)
        session.commit()
        warnings = []
        if key == WORKSPACE_VOLUME_SIZE_GB_KEY:
            warnings = _compute_workspace_size_warnings(session, body.value)

    logger.info(
        "system_setting_updated actor_id=%s key=%s sensitive=%s previous_value_present=%s",
        current_user.id,
        key,
        str(spec.sensitive).lower(),
        str(previous_value_present).lower(),
    )
    if warnings:
        logger.info(
            "system_setting_shrink_warnings_emitted key=%s actor_id=%s affected=%s",
            key,
            current_user.id,
            len(warnings),
        )

    # PutResponse exposes `value` for back-compat with non-sensitive M002
    # callers. For sensitive rows the value is None (plaintext does not
    # cross the API boundary on PUT — only on the one-shot generate path).
    return SystemSettingPutResponse(
        key=row.key,
        value=None if spec.sensitive else row.value,
        updated_at=row.updated_at,
        warnings=warnings,
    )


@router.post(
    "/settings/vapid_keys/generate",
    response_model=VapidKeysGenerateResponse,
)
def generate_vapid_keys(
    *,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """Atomically generate a fresh VAPID keypair and persist both halves.

    Writes the public key to ``system_settings.value`` (JSONB, non-sensitive
    — every browser fetches it from /api/v1/push/vapid_public_key) and the
    private key encrypted into ``system_settings.value_encrypted`` (sensitive,
    Fernet-encrypted at rest). Returns BOTH plaintext values exactly once
    (the only moment the private key crosses the backend → UI boundary;
    subsequent admin GETs return the redacted shape).

    Re-calling this endpoint is intentionally destructive (D025): every
    existing push subscription becomes unverifiable until the affected
    devices re-subscribe. Operators should rotate carefully — the response
    body's ``overwrote_existing`` flag is true on a re-generate so the UI
    can require a confirmation step.

    Logs ``admin.vapid_keys.generated actor_id=<uuid> overwrote=<bool>``
    (carries the public-key prefix only — never the raw key).

    NOTE: This route MUST be declared before
    ``/settings/{key}/generate`` — FastAPI matches in declaration order and
    the catch-all otherwise swallows ``vapid_keys`` as a literal key.
    """
    public_b64, private_b64 = _generate_vapid_keypair()

    # Detect whether either row already had a value so the response can
    # carry the destructive-overwrite signal.
    pub_existing = session.get(SystemSetting, VAPID_PUBLIC_KEY_KEY)
    priv_existing = session.get(SystemSetting, VAPID_PRIVATE_KEY_KEY)
    overwrote_existing = bool(
        (pub_existing is not None and pub_existing.has_value)
        or (priv_existing is not None and priv_existing.has_value)
    )

    # Write public into JSONB, private encrypted into BYTEA. The two writes
    # share a single transaction commit — if the encrypt step fails after
    # the public write, the rollback unwinds both.
    _upsert_jsonb(session, VAPID_PUBLIC_KEY_KEY, public_b64)
    _upsert_encrypted(session, VAPID_PRIVATE_KEY_KEY, private_b64)
    session.commit()

    logger.info(
        "admin.vapid_keys.generated actor_id=%s overwrote=%s key_prefix=%s",
        current_user.id,
        str(overwrote_existing).lower(),
        public_b64[:4],
    )

    return VapidKeysGenerateResponse(
        public_key=public_b64,
        private_key=private_b64,
        overwrote_existing=overwrote_existing,
    )


@router.post(
    "/settings/{key}/generate",
    response_model=SystemSettingGenerateResponse,
)
def generate_system_setting(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    key: str,
) -> Any:
    """Server-side seed a generator-backed sensitive setting.

    Returns the freshly-generated plaintext value EXACTLY ONCE; subsequent
    GETs return `value=null, has_value=true`. Re-calling this endpoint is
    intentionally destructive (D025): a fresh webhook secret breaks every
    in-flight webhook until GitHub is updated to match. Operators are
    expected to rotate upstream first, generate here second.

    422 shapes:
      - `unknown_setting_key` for an unregistered key (matches PUT).
      - `no_generator_for_key` for a registered key with no generator
        (e.g. `github_app_private_key`, which has no server-side seed —
        the operator pastes the PEM via PUT).
    """
    spec = _VALIDATORS.get(key)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "unknown_setting_key", "key": key},
        )
    if spec.generator is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "no_generator_for_key", "key": key},
        )
    # VAPID keys are paired — refuse the per-key generate endpoint and
    # redirect operators to the atomic two-key handler. Catching this here
    # is preferable to letting the per-key generator hooks raise RuntimeError
    # mid-write, which would surface as a 500.
    if key in {VAPID_PUBLIC_KEY_KEY, VAPID_PRIVATE_KEY_KEY}:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "use_atomic_endpoint_for_vapid_keys",
                "key": key,
                "reason": (
                    "VAPID public and private keys must be generated"
                    " atomically — POST /admin/settings/vapid_keys/generate"
                ),
            },
        )
    # Generators are sensitive-only by construction (the module-load assertion
    # widens for VAPID public-key, which is handled by the atomic endpoint).
    plaintext = spec.generator()
    row = _upsert_encrypted(session, key, plaintext)
    session.commit()

    logger.info(
        "system_setting_generated actor_id=%s key=%s",
        current_user.id,
        key,
    )

    # The plaintext crosses the API boundary exactly once — on this
    # response. Subsequent GETs always redact to value=None.
    return SystemSettingGenerateResponse(
        key=row.key,
        value=plaintext,
        has_value=True,
        generated=True,
        updated_at=row.updated_at,
    )
