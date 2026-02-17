import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from temporalio.worker import Worker

from backend.client import shutdown
from backend.config import TEMPORAL_HOST, TEMPORAL_TASK_QUEUE
from backend.routers.feeders import router as feeders_router
from backend.temporal.client import get_temporal_client
from backend.temporal.activities.feeder_activities import manual_feed
from backend.temporal.workflows.feeder_workflows import DailyScheduledFeedingWorkflow

logger = logging.getLogger(__name__)


async def run_temporal_worker_with_retry(shutdown_event: asyncio.Event) -> None:
    """Run the Temporal worker with automatic retry on connection failure."""
    retry_delay = 5  # seconds between retries

    while not shutdown_event.is_set():
        try:
            logger.info("Connecting to Temporal at %s...", TEMPORAL_HOST)
            client = await get_temporal_client()

            worker = Worker(
                client,
                task_queue=TEMPORAL_TASK_QUEUE,
                workflows=[DailyScheduledFeedingWorkflow],
                activities=[manual_feed],
            )

            logger.info("Temporal worker started on task queue: %s", TEMPORAL_TASK_QUEUE)
            await worker.run()
        except Exception as e:
            if shutdown_event.is_set():
                break
            logger.warning("Temporal worker error: %s", e)
            logger.info("Retrying in %s seconds...", retry_delay)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=retry_delay)
                break  # Shutdown was requested during wait
            except asyncio.TimeoutError:
                continue  # Retry connecting


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Temporal worker as a background task with retry logic
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(run_temporal_worker_with_retry(shutdown_event))

    yield

    # Signal shutdown and wait for worker to stop
    shutdown_event.set()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Temporal worker stopped.")
    await shutdown()


app = FastAPI(title="PetKit Feeder Control", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feeders_router)
