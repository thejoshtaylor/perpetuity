FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# uv binary
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# `capsh` (used by the verification step that confirms cap_sys_admin is granted),
# `curl` (used by the compose healthcheck against /v1/health), and `e2fsprogs`
# which provides mkfs.ext4 for S02 loopback volume provisioning. losetup/mount/
# umount come from util-linux which is already present in python:3.12-slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libcap2-bin e2fsprogs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY orchestrator/pyproject.toml /app/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-workspace

COPY orchestrator/orchestrator /app/orchestrator

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8001"]
