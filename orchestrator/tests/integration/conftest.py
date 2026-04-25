"""Shared integration-test fixtures.

Integration tests in this package talk to the live compose stack: the
`redis` and `orchestrator` services from `docker-compose.yml`. Redis is
internal-network-only (per the M002 CONTEXT — `# Internal compose network
only — never publish a host port`), so these tests must run from inside
the compose network. Two paths:

  1. Run inside the orchestrator container itself:
     `docker compose exec orchestrator uv run pytest tests/integration/`

  2. Run from a sibling container on the compose network:
     `docker run --rm --network perpetuity_default ...`

The fixtures below auto-detect: if redis is reachable on `redis:6379`
(meaning we're inside the compose network), use that. Otherwise skip.

Tests are opt-out via env (`SKIP_INTEGRATION=1`) so the unit suite stays
hermetic.
"""

from __future__ import annotations

import os
import shutil
import socket

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return os.path.exists("/var/run/docker.sock")


def _resolve_redis_endpoint() -> tuple[str, int] | None:
    """Try the in-network DNS name first, then a host fallback."""
    candidates: list[tuple[str, int]] = [
        (os.environ.get("REDIS_HOST", "redis"), int(os.environ.get("REDIS_PORT", "6379"))),
        ("127.0.0.1", 6379),
    ]
    for host, port in candidates:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return (host, port)
        except OSError:
            continue
    return None


@pytest.fixture(autouse=True)
def _skip_if_no_docker(request: pytest.FixtureRequest) -> None:
    """Skip the test if `SKIP_INTEGRATION=1` is set."""
    if os.environ.get("SKIP_INTEGRATION") == "1":
        pytest.skip("SKIP_INTEGRATION=1 set")


@pytest.fixture
def redis_endpoint() -> tuple[str, int]:
    """Connection details for the live redis. Skips if unreachable."""
    endpoint = _resolve_redis_endpoint()
    if endpoint is None:
        pytest.skip(
            "redis not reachable — run from inside the compose network "
            "(e.g. `docker compose exec orchestrator uv run pytest "
            "tests/integration/`)"
        )
    return endpoint


@pytest.fixture
def docker_available() -> None:
    if not _docker_available():
        pytest.skip("docker not available on this host")
