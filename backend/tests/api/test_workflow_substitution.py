"""Unit tests for `app.workflows.substitution.render_step_inputs`.

Covers:
  * Happy path: {prev.stdout} resolves to the immediately prior step's stdout.
  * Happy path: {prev[N].stdout} resolves to Nth previous step (0=immediate).
  * Happy path: {form.<field>} resolves to trigger_payload[<field>].
  * Happy path: {trigger.<key>} resolves to trigger_payload[<key>].
  * Multiple placeholders in the same string all resolve.
  * Missing variable raises SubstitutionError with the right `.missing`.
  * Deep-copy: the original snapshot dict is never mutated.
  * {prev.stdout} with no prior steps raises SubstitutionError.
  * Nested dict config is walked recursively.
  * List config values are walked recursively.
  * Non-string config values (int, bool, None) pass through unchanged.
  * Unrelated `{` characters in a string (not matching known tokens) raise
    SubstitutionError (they reference an undefined variable).
"""
from __future__ import annotations

import copy

import pytest

from app.workflows.substitution import SubstitutionError, render_step_inputs


def _snapshot(config: dict) -> dict:
    return {
        "id": "step-id",
        "workflow_id": "wf-id",
        "step_index": 0,
        "action": "shell",
        "config": config,
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_prev_stdout_resolves_to_last_step() -> None:
    snap = _snapshot({"cmd": ["echo", "{prev.stdout}"]})
    prior = [{"stdout": "hello from step 0"}]
    result = render_step_inputs(snap, {}, prior)
    assert result["config"]["cmd"] == ["echo", "hello from step 0"]


def test_prev_n_stdout_immediate() -> None:
    snap = _snapshot({"cmd": ["echo", "{prev[0].stdout}"]})
    prior = [{"stdout": "step0 out"}]
    result = render_step_inputs(snap, {}, prior)
    assert result["config"]["cmd"] == ["echo", "step0 out"]


def test_prev_n_stdout_second_back() -> None:
    snap = _snapshot({"cmd": ["{prev[1].stdout}"]})
    prior = [{"stdout": "older"}, {"stdout": "newer"}]
    result = render_step_inputs(snap, {}, prior)
    assert result["config"]["cmd"] == ["older"]


def test_form_field_resolves() -> None:
    snap = _snapshot({"cmd": ["git", "checkout", "{form.branch}"]})
    result = render_step_inputs(snap, {"branch": "main"}, [])
    assert result["config"]["cmd"] == ["git", "checkout", "main"]


def test_trigger_key_resolves() -> None:
    snap = _snapshot({"cmd": ["echo", "{trigger.repo}"]})
    result = render_step_inputs(snap, {"repo": "myrepo"}, [])
    assert result["config"]["cmd"] == ["echo", "myrepo"]


def test_multiple_placeholders_in_same_string() -> None:
    snap = _snapshot({"prompt": "Check out {form.branch} and run: {prev.stdout}"})
    prior = [{"stdout": "npm test"}]
    result = render_step_inputs(snap, {"branch": "feature-x"}, prior)
    assert result["config"]["prompt"] == "Check out feature-x and run: npm test"


def test_nested_dict_config_walked() -> None:
    snap = _snapshot({
        "outer": {
            "inner": "{form.val}",
        }
    })
    result = render_step_inputs(snap, {"val": "resolved"}, [])
    assert result["config"]["outer"]["inner"] == "resolved"


def test_list_in_config_walked() -> None:
    snap = _snapshot({"items": ["{form.a}", "{form.b}"]})
    result = render_step_inputs(snap, {"a": "alpha", "b": "beta"}, [])
    assert result["config"]["items"] == ["alpha", "beta"]


def test_non_string_values_pass_through() -> None:
    snap = _snapshot({"count": 42, "flag": True, "nothing": None})
    result = render_step_inputs(snap, {}, [])
    assert result["config"]["count"] == 42
    assert result["config"]["flag"] is True
    assert result["config"]["nothing"] is None


def test_no_placeholders_unchanged() -> None:
    snap = _snapshot({"cmd": ["ls", "-la"]})
    result = render_step_inputs(snap, {}, [])
    assert result["config"]["cmd"] == ["ls", "-la"]


# ---------------------------------------------------------------------------
# SubstitutionError cases
# ---------------------------------------------------------------------------


def test_missing_form_field_raises() -> None:
    snap = _snapshot({"cmd": ["{form.missing_field}"]})
    with pytest.raises(SubstitutionError) as exc_info:
        render_step_inputs(snap, {}, [])
    assert exc_info.value.missing == "form.missing_field"


def test_missing_trigger_key_raises() -> None:
    snap = _snapshot({"cmd": ["{trigger.ghost}"]})
    with pytest.raises(SubstitutionError) as exc_info:
        render_step_inputs(snap, {}, [])
    assert exc_info.value.missing == "trigger.ghost"


def test_prev_stdout_no_prior_raises() -> None:
    snap = _snapshot({"cmd": ["{prev.stdout}"]})
    with pytest.raises(SubstitutionError) as exc_info:
        render_step_inputs(snap, {}, [])
    assert exc_info.value.missing == "prev.stdout"


def test_prev_n_out_of_range_raises() -> None:
    snap = _snapshot({"cmd": ["{prev[5].stdout}"]})
    prior = [{"stdout": "only one"}]
    with pytest.raises(SubstitutionError) as exc_info:
        render_step_inputs(snap, {}, prior)
    assert "prev[5]" in exc_info.value.missing


def test_unknown_token_raises() -> None:
    snap = _snapshot({"cmd": ["{unknown.thing}"]})
    with pytest.raises(SubstitutionError) as exc_info:
        render_step_inputs(snap, {}, [])
    assert exc_info.value.missing == "unknown.thing"


# ---------------------------------------------------------------------------
# Deep-copy: original snapshot never mutated
# ---------------------------------------------------------------------------


def test_original_snapshot_not_mutated() -> None:
    snap = _snapshot({"cmd": ["{form.branch}"]})
    original_cmd = copy.deepcopy(snap["config"]["cmd"])
    render_step_inputs(snap, {"branch": "main"}, [])
    # The original must be unchanged.
    assert snap["config"]["cmd"] == original_cmd


def test_original_trigger_payload_not_mutated() -> None:
    snap = _snapshot({"val": "{form.x}"})
    payload = {"x": "orig"}
    render_step_inputs(snap, payload, [])
    assert payload == {"x": "orig"}
