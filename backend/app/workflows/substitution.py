"""Cross-step variable substitution engine for workflow step inputs.

`render_step_inputs(snapshot, trigger_payload, prior_step_runs)` walks the
`snapshot["config"]` dict (deep-copied so the original is never mutated),
replaces every `{var}` placeholder it recognises, and raises
`SubstitutionError` for any unknown variable.

Supported variables:
  * `{prev.stdout}`       — stdout of the immediately prior step run.
  * `{prev[N].stdout}`    — stdout of the step N positions back (0 = immediate).
  * `{form.<field>}`      — value from trigger_payload[<field>].
  * `{trigger.<key>}`     — value from trigger_payload[<key>] (alias / catch-all).

Substitution uses `str.replace` chains deliberately (NOT `str.format`).
A user-supplied prompt string containing unrelated `{` characters (e.g.
`{` in a code fence) must NOT raise a KeyError or leak format-spec
features — the MEM274 prompt-discipline constraint.

Rendered config values are NEVER logged. Only the missing variable NAME is
logged on `SubstitutionError`, keeping form-field values and prior step
stdout out of log files.
"""

from __future__ import annotations

import copy
import re
from typing import Any


class SubstitutionError(Exception):
    """Raised when a template references an undefined variable.

    Carries `missing` (the unresolved token string) for clean logging
    without exposing the surrounding rendered content.
    """

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"undefined substitution variable: {missing!r}")


# Matches {prev.stdout}, {prev[N].stdout}, {form.<field>}, {trigger.<key>}
# and any other {token} so we can detect unknown variables.
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")

# The prior-step index patterns.
_PREV_STDOUT_RE = re.compile(r"^prev\.stdout$")
_PREV_N_STDOUT_RE = re.compile(r"^prev\[(\d+)\]\.stdout$")
_FORM_RE = re.compile(r"^form\.(.+)$")
_TRIGGER_RE = re.compile(r"^trigger\.(.+)$")


def _resolve_token(
    token: str,
    trigger_payload: dict[str, Any],
    prior_step_runs: list[dict[str, Any]],
) -> str:
    """Resolve a single `{token}` to its string value.

    Raises `SubstitutionError` if `token` is not recognised or the
    referenced data is absent.
    """
    if _PREV_STDOUT_RE.match(token):
        if not prior_step_runs:
            raise SubstitutionError(token)
        return str(prior_step_runs[-1].get("stdout") or "")

    m = _PREV_N_STDOUT_RE.match(token)
    if m:
        n = int(m.group(1))
        # prior_step_runs[-1] is the immediately prior step (n=0).
        idx = -(n + 1)
        if abs(idx) > len(prior_step_runs):
            raise SubstitutionError(token)
        return str(prior_step_runs[idx].get("stdout") or "")

    m = _FORM_RE.match(token)
    if m:
        field = m.group(1)
        if field not in trigger_payload:
            raise SubstitutionError(token)
        return str(trigger_payload[field])

    m = _TRIGGER_RE.match(token)
    if m:
        key = m.group(1)
        if key not in trigger_payload:
            raise SubstitutionError(token)
        return str(trigger_payload[key])

    # `{prompt}` is a convenience shorthand for `{trigger.prompt}` used by
    # the AI executor's prompt_template field. Resolve it from trigger_payload
    # so the snapshot carries the rendered text (R018).
    if token == "prompt":
        if "prompt" not in trigger_payload:
            raise SubstitutionError(token)
        return str(trigger_payload["prompt"])

    raise SubstitutionError(token)


def _substitute_string(
    value: str,
    trigger_payload: dict[str, Any],
    prior_step_runs: list[dict[str, Any]],
) -> str:
    """Replace every `{placeholder}` in `value` using str.replace semantics.

    Iterates left-to-right over all placeholders found by the regex.  The
    regex-then-replace approach avoids `str.format` so arbitrary `{`/`}`
    characters in user content pass through unchanged.
    """
    tokens = _PLACEHOLDER_RE.findall(value)
    result = value
    for token in tokens:
        replacement = _resolve_token(token, trigger_payload, prior_step_runs)
        result = result.replace("{" + token + "}", replacement)
    return result


def _substitute_value(
    value: Any,
    trigger_payload: dict[str, Any],
    prior_step_runs: list[dict[str, Any]],
) -> Any:
    """Recursively substitute placeholders in a config value.

    * str  → run substitution.
    * dict → recurse into values.
    * list → recurse into elements.
    * anything else → return unchanged.
    """
    if isinstance(value, str):
        return _substitute_string(value, trigger_payload, prior_step_runs)
    if isinstance(value, dict):
        return {
            k: _substitute_value(v, trigger_payload, prior_step_runs)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _substitute_value(item, trigger_payload, prior_step_runs)
            for item in value
        ]
    return value


def render_step_inputs(
    snapshot: dict[str, Any],
    trigger_payload: dict[str, Any],
    prior_step_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a deep-copy of `snapshot` with all `{var}` placeholders resolved.

    Args:
        snapshot:        The frozen step snapshot stored on `step_runs.snapshot`.
                         Only `snapshot["config"]` is walked; the rest is
                         returned unchanged.
        trigger_payload: The `workflow_runs.trigger_payload` dict — source
                         for `{form.<field>}` and `{trigger.<key>}` vars.
        prior_step_runs: Ordered list of prior step-run dicts (each has at
                         least a `"stdout"` key). The last entry is the
                         immediately preceding step.

    Returns:
        A new dict (deep copy) with `config` values fully resolved.

    Raises:
        SubstitutionError: If any `{token}` in config references an unknown
                           or absent variable.
    """
    rendered = copy.deepcopy(snapshot)
    config = rendered.get("config")
    if config is not None:
        rendered["config"] = _substitute_value(
            config, trigger_payload or {}, prior_step_runs or []
        )
    return rendered
