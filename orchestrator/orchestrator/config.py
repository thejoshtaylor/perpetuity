"""Orchestrator runtime configuration.

Loaded from environment variables. Defaults are tuned for local docker-compose
development; production overrides land via env. Per the M002 CONTEXT and
slice plan, this module is the single home for tunables that downstream tasks
(T03 container provisioning, T04 WS bridge) read from — adding a knob here
in T01 lets later tasks consume it without re-editing config plumbing.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Orchestrator settings — read from env at process start."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # HTTP bind. Internal compose network only — no host port published.
    host: str = "0.0.0.0"
    port: int = 8001

    # Workspace image tag. Pulled once on startup (T02). Pull failure is a
    # boot blocker per M002 CONTEXT error-handling.
    workspace_image: str = "perpetuity/workspace:latest"

    # Per-container resource caps (T03 enforces; recorded here so the value is
    # one source of truth across the orchestrator).
    container_mem_limit: str = "2g"
    container_pids_limit: int = 512
    # 1.0 vCPU equivalent. Documented assumption from S01 task plan.
    container_nano_cpus: int = 1_000_000_000

    # Bind-mount root for per-(user, team) workspaces. T03 uses a plain
    # subdir under this path; S02 swaps in loopback-ext4 volumes mounted
    # at the same path.
    workspace_root: str = "/var/lib/perpetuity/workspaces"

    # Idle reaper default (S04 enforces; admin-overridable via system_settings
    # later in M002).
    idle_timeout_seconds: int = 15 * 60

    # Scrollback hard cap (orchestrator-side, never trust tmux to limit).
    scrollback_max_bytes: int = 100 * 1024

    # Redis. Internal compose network only.
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = Field(default="")

    # Backend ↔ orchestrator shared secret (two-key acceptance per D016).
    # T02 wires the auth middleware that consumes both.
    orchestrator_api_key: str = Field(default="")
    orchestrator_api_key_previous: str = Field(default="")


settings = Settings()
