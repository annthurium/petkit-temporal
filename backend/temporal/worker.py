"""
Temporal Worker Entrypoint

Run with:
    python -m backend.temporal.worker

This worker runs separately from the FastAPI server and processes
workflow and activity tasks from the Temporal server.
"""

import asyncio

from temporalio.worker import Worker

from backend.config import TEMPORAL_HOST, TEMPORAL_TASK_QUEUE
from backend.temporal.client import get_temporal_client
from backend.temporal.activities.feeder_activities import (
    alert_feed_failure,
    trigger_feed,
    verify_feed,
)
from backend.temporal.workflows.feeder_workflows import DailyScheduledFeedingWorkflow


async def run_worker() -> None:
    """Start the Temporal worker."""
    print(f"Connecting to Temporal at {TEMPORAL_HOST}...")
    client = await get_temporal_client()

    print(f"Starting worker on task queue: {TEMPORAL_TASK_QUEUE}")
    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[DailyScheduledFeedingWorkflow],
        activities=[trigger_feed, verify_feed, alert_feed_failure],
    )

    print("Worker started. Press Ctrl+C to stop.")
    await worker.run()


def main() -> None:
    """Entry point for the worker."""
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        print("\nWorker stopped.")


if __name__ == "__main__":
    main()
