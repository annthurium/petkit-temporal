"""
Temporal Worker Entrypoint

Run with:
    python -m backend.temporal.worker

Locally the API usually runs a worker in-process (RUN_EMBEDDED_WORKER), so you
only need this to run the worker on its own. On Cloud Run this is the worker
pool's entrypoint, with Worker Versioning on (WORKER_DEPLOYMENT_NAME and
WORKER_BUILD_ID set).
"""

import asyncio
import logging
import signal
from datetime import timedelta

from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import Worker, WorkerDeploymentConfig

from backend import config
from backend.temporal.client import connect_temporal_client
from backend.temporal.activities.feeder_activities import (
    alert_feed_failure,
    check_recent_feed,
    refresh_feeder_snapshots,
    send_device_command,
    trigger_feed,
    verify_feed,
)
from backend.temporal.workflows.feeder_workflows import (
    DeviceCommandWorkflow,
    FeedWorkflow,
    RefreshFeedersWorkflow,
)

logger = logging.getLogger(__name__)

# How long to let in-flight activities finish after SIGTERM before cancelling.
# Cloud Run allows 10s between SIGTERM and SIGKILL.
GRACEFUL_SHUTDOWN_TIMEOUT = timedelta(seconds=8)


def deployment_config() -> WorkerDeploymentConfig | None:
    """Worker Versioning config, or None when versioning is off (local dev).

    Every workflow is PINNED: a run finishes on the code version it started on.
    That's safe here because all our workflows are short-lived (ADR 0002), so
    old worker pools drain within minutes of a deploy.
    """
    if not (config.WORKER_DEPLOYMENT_NAME and config.WORKER_BUILD_ID):
        return None
    return WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(
            deployment_name=config.WORKER_DEPLOYMENT_NAME,
            build_id=config.WORKER_BUILD_ID,
        ),
        use_worker_versioning=True,
        default_versioning_behavior=VersioningBehavior.PINNED,
    )


def build_worker(client: Client) -> Worker:
    """Create the worker with every workflow and activity this app uses."""
    kwargs = {}
    deployment = deployment_config()
    if deployment is not None:
        kwargs["deployment_config"] = deployment
    return Worker(
        client,
        task_queue=config.TEMPORAL_TASK_QUEUE,
        workflows=[FeedWorkflow, DeviceCommandWorkflow, RefreshFeedersWorkflow],
        activities=[
            trigger_feed,
            verify_feed,
            check_recent_feed,
            alert_feed_failure,
            send_device_command,
            refresh_feeder_snapshots,
        ],
        graceful_shutdown_timeout=GRACEFUL_SHUTDOWN_TIMEOUT,
        **kwargs,
    )


async def run_worker() -> None:
    """Run the worker until SIGTERM/SIGINT, then shut down gracefully."""
    from backend.client import shutdown as close_petkit_session

    deployment = deployment_config()
    logger.info(
        "Connecting to Temporal at %s (namespace %s, task queue %s, version %s)",
        config.TEMPORAL_ADDRESS,
        config.TEMPORAL_NAMESPACE,
        config.TEMPORAL_TASK_QUEUE,
        f"{deployment.version.deployment_name}/{deployment.version.build_id}"
        if deployment
        else "unversioned",
    )
    client = await connect_temporal_client()
    worker = build_worker(client)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with worker:
        logger.info("Worker started")
        await stop.wait()
        logger.info("Shutdown signal received; draining in-flight tasks")
    await close_petkit_session()
    logger.info("Worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
