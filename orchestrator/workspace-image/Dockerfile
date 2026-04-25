FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base toolchain for the workspace: tmux for pty durability (D012),
# bash as the user shell, git for repo cloning (M003), curl for general use,
# python3 + nodejs for the supported runtime stacks. NodeSource provides a
# current Node.js without pulling in a full nvm setup.
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

# Container started with `sleep infinity` from the orchestrator (T03);
# tmux sessions are launched via `docker exec` per session_id.
CMD ["sleep", "infinity"]
