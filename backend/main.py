import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.routers.feeders import router as feeders_router
from backend.temporal.client import get_temporal_client
from backend.temporal.schedules import ensure_refresh_schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 5


async def _wait_or_stop(shutdown_event: asyncio.Event, seconds: float) -> bool:
    """Sleep up to `seconds`; return True if shutdown was requested meanwhile."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def ensure_refresh_schedule_with_retry(shutdown_event: asyncio.Event) -> None:
    """Make sure the snapshot refresh schedule exists (ADR 0006).

    Runs in the API, not the worker, so it also happens on Cloud Run where the
    API has no worker. Idempotent: creates the schedule only if it's missing.
    """
    while not shutdown_event.is_set():
        try:
            client = await get_temporal_client()
            if await ensure_refresh_schedule(client):
                logger.info("Created snapshot refresh schedule; first refresh running now")
            return
        except Exception as e:
            logger.warning("Could not ensure refresh schedule (%s); retrying", e)
            if await _wait_or_stop(shutdown_event, RETRY_DELAY_SECONDS):
                return


async def run_temporal_worker_with_retry(shutdown_event: asyncio.Event) -> None:
    """Run an in-process Temporal worker, reconnecting on failure (local dev)."""
    from backend.temporal.worker import build_worker

    while not shutdown_event.is_set():
        try:
            logger.info("Connecting to Temporal at %s...", config.TEMPORAL_ADDRESS)
            client = await get_temporal_client()
            worker = build_worker(client)
            logger.info("Embedded Temporal worker started on task queue: %s", config.TEMPORAL_TASK_QUEUE)
            await worker.run()
        except Exception as e:
            if shutdown_event.is_set():
                break
            logger.warning("Temporal worker error: %s", e)
            logger.info("Retrying in %s seconds...", RETRY_DELAY_SECONDS)
            if await _wait_or_stop(shutdown_event, RETRY_DELAY_SECONDS):
                break


@asynccontextmanager
async def lifespan(app: FastAPI):
    shutdown_event = asyncio.Event()
    tasks = [asyncio.create_task(ensure_refresh_schedule_with_retry(shutdown_event))]
    if config.RUN_EMBEDDED_WORKER:
        tasks.append(asyncio.create_task(run_temporal_worker_with_retry(shutdown_event)))
    else:
        logger.info("Embedded worker disabled; expecting a separate worker (RUN_EMBEDDED_WORKER=false)")

    yield

    shutdown_event.set()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if config.RUN_EMBEDDED_WORKER:
        from backend.client import shutdown as close_petkit_session

        await close_petkit_session()
        logger.info("Temporal worker stopped.")


app = FastAPI(title="PetKit Feeder Control", lifespan=lifespan)

# Only needed in local dev, where Vite serves the frontend on another port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """Liveness check. Touches nothing external, so it stays cheap and fast."""
    return {"status": "ok"}


app.include_router(feeders_router)

# In the container, the API also serves the built frontend from the same
# origin. Mounted last so /api routes take precedence.
if config.FRONTEND_DIST_DIR and Path(config.FRONTEND_DIST_DIR).is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST_DIR, html=True), name="frontend")
