FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base toolchain for the workspace: tmux for pty durability (D012),
# bash as the user shell, git for repo cloning (M003), curl for general use,
# python3 + nodejs for the supported runtime stacks. NodeSource provides a
# current Node.js without pulling in a full nvm setup. `script` (util-linux)
# is shipped by default on ubuntu:24.04 — M005's TTY workaround (MEM007)
# wraps the AI CLIs as `script -q /dev/null sh -c '...'` so a Celery worker
# can drive them without inheriting a real pty.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gnupg \
        python3 \
        python3-pip \
        tmux \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# AI CLIs (M005/S02). Pinned versions so the workspace image is reproducible —
# version drift in either CLI would silently change the runtime contract for
# every team. Bumping these is a deliberate, auditable image rebuild.
#   - @anthropic-ai/claude-code provides the `claude` binary used by the
#     `_direct_claude` workflow.
#   - @openai/codex provides the `codex` binary used by the `_direct_codex`
#     workflow.
# Both CLIs require a real pty; M005 wraps them with `script -q /dev/null`.
ARG CLAUDE_CODE_VERSION=1.0.30
ARG CODEX_VERSION=0.20.0
RUN npm install -g \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

# TTY smoke checks: invoke each CLI through the same `script -q /dev/null`
# wrapper M005's executor uses. If either CLI regresses on TTY discipline
# (e.g. claude/codex deciding to exit non-zero when stdin is not a tty),
# the build fails here rather than at runtime inside a Celery task. The
# `--version` subcommand is stable across both CLIs and does not require
# network or credentials.
RUN script -q /dev/null sh -c 'claude --version' \
    && script -q /dev/null sh -c 'codex --version'

# Container started with `sleep infinity` from the orchestrator (T03);
# tmux sessions are launched via `docker exec` per session_id.
CMD ["sleep", "infinity"]
