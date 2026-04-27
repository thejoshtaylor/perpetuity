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

    # Host-side directory where per-volume .img files live (one per
    # workspace_volume row, named `<volume_id>.img`). Bind-mounted 1:1
    # from host into the orchestrator container so volumes survive
    # orchestrator restarts. S02/T03.
    vols_dir: str = "/var/lib/perpetuity/vols"

    # Postgres connection URL for the orchestrator's read-of-(user, team)
    # workspace_volume row. Backend owns schema migrations (s04); the
    # orchestrator only reads/inserts rows. Default targets compose-internal
    # `db` host on the standard port; tests can override via DATABASE_URL.
    database_url: str = "postgresql://postgres:changethis@db:5432/app"

    # Default per-volume size_gb for fresh workspace_volume rows. D015 says
    # the per-row size_gb is the source of truth for the effective cap, so
    # this default ONLY governs new-row creation. S03 will replace this
    # with a system_settings lookup; until then 4 GB is the contract.
    default_volume_size_gb: int = 4

    # Idle reaper default (S04 enforces; admin-overridable via system_settings
    # later in M002).
    idle_timeout_seconds: int = 15 * 60

    # Per-team mirror idle reaper defaults (M004/S03). The reaper resolves
    # ``mirror_idle_timeout_seconds`` from system_settings on every tick;
    # this fallback is only used when the row is missing/invalid or pg
    # is unreachable. 30 minutes matches the user-session reaper default
    # ergonomically — tuned shorter than 24h would shred warm caches; the
    # 60s floor in the admin validator (T01) prevents the operator from
    # weaponizing the reaper into a per-tick teardown.
    mirror_idle_timeout_seconds: int = 30 * 60

    # How often the team-mirror reaper wakes. Env-overridable so the
    # integration suite can run with a 1s tick. Range [1, 300] mirrors
    # the user-session reaper.
    mirror_reaper_interval_seconds: int = 30

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

    # GitHub REST API base. Production points at the public host;
    # tests respx-mock this URL directly. Trailing slash is normalized
    # off at call sites.
    github_api_base_url: str = "https://api.github.com"

    # GitHub git-clone base. Production points at the public host with
    # https://; the M004/S04 e2e overrides this to a credential-free
    # `git://mock-github-x:9418` so it can drive the full two-hop clone +
    # auto-push round-trip without a real TLS handshake or a real
    # api.github.com round-trip. NEVER overridden in production. Trailing
    # slash is normalized off at call sites.
    github_clone_base_url: str = "https://github.com"


settings = Settings()
