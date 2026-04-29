"""Celery app factory for the M005 workflow runner (T03).

Single Celery `Celery("perpetuity")` instance bound to the existing Redis
broker (the same Redis that the rate-limiter and the orchestrator use).
The broker URL is composed from `REDIS_HOST` / `REDIS_PORT` /
`REDIS_PASSWORD` env using the same shape as
`app.core.rate_limit.RedisSlidingWindowRateLimiter._client`, so deployment
ops only need one Redis to think about.

Per MEM009, Postgres (`workflow_runs.status`, `step_runs.status`) is the
authoritative store for run state; Celery's role is dispatch only. We
deliberately do not configure a result backend — the worker writes status
straight to Postgres and the API reads from Postgres. Setting `backend=None`
also avoids the second round-trip to Redis on every task return.

Worker safety knobs (S05 will use these):
  * `task_acks_late=True` — the broker only ACKs the task after the
    worker function returns (or fails); a SIGKILL mid-task leaves the
    message un-ACKed and the broker re-delivers it on the next worker.
  * `task_reject_on_worker_lost=True` — if the worker process dies
    abnormally (OOM, segfault, k8s evict), Celery rejects (and re-queues)
    the in-flight task instead of silently dropping it. S05 ships the
    orphan-recovery beat task that pairs with this; T03 lands the flag now
    so S05 doesn't have to retroactively edit worker config.

Tasks live in `app.workflows.tasks` and are auto-discovered via
`autodiscover_tasks(["app.workflows"])`. Importing this module is enough
to register the task (`@celery_app.task(name="app.workflows.run_workflow")`).
"""

from __future__ import annotations

import logging
import os

from celery import Celery

logger = logging.getLogger("app.core.celery_app")


def _broker_url() -> str:
    """Compose the Redis broker URL from environment.

    Same env contract as `RedisSlidingWindowRateLimiter`: `REDIS_HOST`
    (default `redis`), `REDIS_PORT` (default `6379`), `REDIS_PASSWORD`
    (optional). DB index 0 — the rate-limiter writes its sorted-set keys
    into the same DB; the namespaces don't collide because Celery prefixes
    its keys (`celery-task-meta-*`, `_kombu.binding.*`).
    """
    host = os.environ.get("REDIS_HOST", "redis")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


celery_app = Celery(
    "perpetuity",
    broker=_broker_url(),
    backend=None,
    include=["app.workflows.tasks"],
)

# Worker behaviour. See module docstring for rationale on each flag.
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Always serialize task args/results as JSON — a Celery worker rebuild
    # without the matching pickle classes silently dies on legacy pickles,
    # and run_workflow only needs to pass a string `run_id` anyway.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Local time = UTC; consistent with the rest of the backend's
    # `get_datetime_utc` helper.
    timezone="UTC",
    enable_utc=True,
    # Ship one task per worker fetch. M005's per-step cost is dominated
    # by an orchestrator HTTP call, not Redis round-trips — fairness and
    # fast crash-recovery beat throughput here.
    worker_prefetch_multiplier=1,
    # S05/T03: Beat schedule for orphan-run recovery. Runs every 10 minutes
    # to fail WorkflowRuns whose Celery worker died without updating status.
    beat_schedule={
        "recover-orphan-runs": {
            "task": "app.workflows.recover_orphan_runs",
            "schedule": 600,  # 10 minutes in seconds
        },
    },
)
