"""Unit tests for orchestrator/sessions.py — focused on the MEM264 fix.

The slice goal is to make user containers dial `team-mirror-<first8>:9418`
by DNS for the user-side hop. That requires the user container to attach
to the compose network (`perpetuity_default`) at create time. This test
file is the regression guard: if a future refactor drops the NetworkMode
key from `_build_container_config`, the user-side clone in S04 will fail
with `Could not resolve host` and these tests will go red first.
"""

from __future__ import annotations

import logging
import os

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402

from orchestrator import sessions as sessions_mod  # noqa: E402
from orchestrator.sessions import (  # noqa: E402
    _USER_NETWORK,
    _build_container_config,
)


def test_build_container_config_attaches_to_perpetuity_default() -> None:
    """The HostConfig MUST carry NetworkMode=perpetuity_default (MEM264)."""
    cfg = _build_container_config(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "/var/lib/perpetuity/workspaces/u/t",
        "/workspaces/22222222-2222-2222-2222-222222222222",
    )

    host = cfg.get("HostConfig")
    assert isinstance(host, dict), cfg
    # The key + value pair is the structural assertion. Spelled exactly
    # the way Docker expects so a typo in the source would fail here.
    assert host.get("NetworkMode") == "perpetuity_default"
    # Defense in depth: the module constant agrees with the value.
    assert _USER_NETWORK == "perpetuity_default"


def test_build_container_config_keeps_existing_host_config_keys() -> None:
    """Adding NetworkMode must not displace mem/cpu/binds/restart-policy."""
    cfg = _build_container_config(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "/host/u/t",
        "/workspaces/t",
    )
    host = cfg["HostConfig"]
    # Sanity: the keys we already shipped in M002 still exist.
    for required_key in ("Memory", "PidsLimit", "NanoCpus", "Binds", "RestartPolicy"):
        assert required_key in host, (required_key, host)


def test_provision_container_logs_network_attach_on_create(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First provision emits `network_mode_attached_to_user_container`.

    Future agents grepping for the MEM264 regression want a log marker that
    confirms the user container actually joined the compose network.
    """
    import asyncio

    user_id = "11111111-1111-1111-1111-111111111111"
    team_id = "22222222-2222-2222-2222-222222222222"

    # Stub out the volume + label helpers — we're testing the log line, not
    # the docker harness. _ensure_workspace_dir / ensure_volume_for run
    # before `create_or_replace`, so they need to be no-ops.
    async def _noop_ensure_volume_for(*_a: object, **_kw: object) -> None:
        return None

    async def _noop_find(*_a: object, **_kw: object) -> str | None:
        return None  # force the create path

    monkeypatch.setattr(
        sessions_mod, "ensure_volume_for", _noop_ensure_volume_for
    )
    monkeypatch.setattr(
        sessions_mod, "_find_container_by_labels", _noop_find
    )

    class _FakeContainer:
        id = "fakecontaineridabcdef0123456789"

        async def start(self) -> None:
            return None

    class _FakeContainers:
        async def create_or_replace(
            self, *, name: str, config: dict[str, object]
        ) -> _FakeContainer:
            return _FakeContainer()

    class _FakeDocker:
        containers = _FakeContainers()

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        cid, created = asyncio.run(
            sessions_mod.provision_container(
                _FakeDocker(),  # type: ignore[arg-type]
                user_id,
                team_id,
                pg=None,  # type: ignore[arg-type]
            )
        )

    assert created is True
    assert cid == "fakecontaineridabcdef0123456789"

    msgs = [r.message for r in caplog.records]
    # The fingerprint future agents will grep for.
    assert any(
        "network_mode_attached_to_user_container" in m
        and "network=perpetuity_default" in m
        for m in msgs
    ), msgs
