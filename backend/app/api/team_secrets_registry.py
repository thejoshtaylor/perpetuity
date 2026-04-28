"""Per-key validator registry for team_secrets (M005/S01/T02).

Mirrors the shape of `app/api/routes/admin.py::_VALIDATORS` for system_settings
(MEM158/MEM153 pattern): a closed set of registered keys, each carrying a
validator callable and a `sensitive` flag. Reject-by-default on unknown keys
keeps a typo from silently writing a row nothing reads — `lookup(key)` raises
`UnregisteredTeamSecretKeyError` instead of returning `None`, so call sites
cannot accidentally treat "missing spec" as "skip validation".

Locked for M005:

  * `claude_api_key`   — must start with `sk-ant-` (Anthropic admin/console
    keys), length >= 40
  * `openai_api_key`   — must start with `sk-`, length >= 40

Both are sensitive: the row's plaintext never leaves the encryption module,
and the API surface only ever exposes presence/absence + updated_at.

Future M005+ slices that add registered keys (e.g. a `github_pat` for
personal connections) extend `_VALIDATORS` here. Adding a key here without
also wiring the API route is harmless — the validator just sits unused.
Adding a route without registering the key fails loud (PUT returns 400
`unregistered_key`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

CLAUDE_API_KEY = "claude_api_key"
OPENAI_API_KEY = "openai_api_key"

# Minimum length for both providers' real keys is well above 40 in practice;
# we floor at 40 to catch obvious paste errors without false-rejecting future
# format variants the providers ship.
_MIN_KEY_LEN = 40


class UnregisteredTeamSecretKeyError(KeyError):
    """Raised when a team_secrets key is not in the validator registry.

    Subclasses KeyError so `_VALIDATORS[key]` style lookups still raise the
    expected exception type, while letting callers pattern-match on this
    specific subclass when translating to a 400 `unregistered_key` response.
    The `key` attribute carries the offending key so the caller doesn't have
    to re-parse the message.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class InvalidTeamSecretValueError(ValueError):
    """Raised by a validator when the plaintext fails shape checks.

    `key` and `reason` are exposed as attributes so the API layer (T03) can
    map this directly to `400 {detail: 'invalid_value_shape', key, hint}`
    without re-parsing the message. The plaintext value MUST NOT appear in
    the message, args, or any log line — only the failure reason ("prefix",
    "length", "type") leaks out.
    """

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"invalid_value_shape key={key!r} reason={reason}")


@dataclass(frozen=True)
class _SecretSpec:
    """Per-key registry entry.

    `validator` runs against the plaintext on PUT (raises
    `InvalidTeamSecretValueError` on failure). `sensitive=True` is the only
    supported value today — every team_secret is encrypted at rest. The flag
    is kept on the spec for shape-parity with `_SettingSpec` so future
    non-sensitive team-scoped key/value pairs can land in the same registry
    without a schema fork.
    """

    validator: Callable[[str], None]
    sensitive: bool


def _validate_claude_api_key(value: str) -> None:
    """Anthropic API keys: `sk-ant-` prefix, length >= 40.

    Reject non-str up front so the rest of the registry can assume str. The
    reason strings are intentionally short and shape-only — they're forwarded
    to the API caller as a hint, never logged with the value.
    """
    if not isinstance(value, str):
        raise InvalidTeamSecretValueError(CLAUDE_API_KEY, "must_be_string")
    if not value.startswith("sk-ant-"):
        raise InvalidTeamSecretValueError(CLAUDE_API_KEY, "bad_prefix")
    if len(value) < _MIN_KEY_LEN:
        raise InvalidTeamSecretValueError(CLAUDE_API_KEY, "too_short")


def _validate_openai_api_key(value: str) -> None:
    """OpenAI API keys: `sk-` prefix, length >= 40.

    The Anthropic prefix `sk-ant-` is a strict superset of `sk-`, so this
    validator will accept an `sk-ant-...` paste. That's a registration-time
    foot-gun (an admin pasting an Anthropic key into the OpenAI slot), not a
    storage-layer concern — we surface the slot in the UI and accept that
    the executor (S02+) will fail loud at call time on an upstream 401.
    """
    if not isinstance(value, str):
        raise InvalidTeamSecretValueError(OPENAI_API_KEY, "must_be_string")
    if not value.startswith("sk-"):
        raise InvalidTeamSecretValueError(OPENAI_API_KEY, "bad_prefix")
    if len(value) < _MIN_KEY_LEN:
        raise InvalidTeamSecretValueError(OPENAI_API_KEY, "too_short")


_VALIDATORS: dict[str, _SecretSpec] = {
    CLAUDE_API_KEY: _SecretSpec(
        validator=_validate_claude_api_key, sensitive=True
    ),
    OPENAI_API_KEY: _SecretSpec(
        validator=_validate_openai_api_key, sensitive=True
    ),
}


def lookup(key: str) -> _SecretSpec:
    """Return the spec for `key` or raise `UnregisteredTeamSecretKeyError`.

    Centralizes the lookup so call sites get the strict-typed exception
    instead of having to translate a bare `KeyError` from dict access.
    """
    spec = _VALIDATORS.get(key)
    if spec is None:
        raise UnregisteredTeamSecretKeyError(key)
    return spec


def registered_keys() -> tuple[str, ...]:
    """Return the registered keys in declaration order.

    Used by `list_team_secret_status` to render one row per registered key
    even when the team has no rows yet. Tuple (not list) so callers can't
    mutate the registry view.
    """
    return tuple(_VALIDATORS.keys())
