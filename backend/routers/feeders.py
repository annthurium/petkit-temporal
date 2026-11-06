"""Feeder endpoints.

The API never talks to PetKit (ADR 0006). Reads come from Firestore snapshots
written by the worker; every action runs as a Temporal workflow.
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from temporalio.client import WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from backend import snapshots
from backend.config import TEMPORAL_TASK_QUEUE
from backend.serializers import EMPTY_RECORDS
from backend.temporal import schedules
from backend.temporal.activities.feeder_activities import (
    DeviceCommandInput,
    RefreshSnapshotsInput,
)
from backend.temporal.client import get_temporal_client
from backend.temporal.workflows.feeder_workflows import (
    DeviceCommandWorkflow,
    FeedInput,
    FeedWorkflow,
    RefreshFeedersWorkflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feeders", tags=["feeders"])


def _public_feeder(snapshot: dict) -> dict:
    """A snapshot without its feed history (served by /records instead)."""
    return {k: v for k, v in snapshot.items() if k != "records"}


async def _require_snapshot(device_id: int) -> dict:
    snapshot = await snapshots.get_feeder_snapshot(device_id)
    if snapshot is None:
        raise HTTPException(404, "Feeder not found")
    return snapshot


async def _require_feeder(device_id: int) -> None:
    await _require_snapshot(device_id)


# ---------------------------------------------------------------------------
# Waiting on workflows
# ---------------------------------------------------------------------------

# How long a request waits for its workflow before answering 202 "pending".
# The workflow keeps running either way.
WORKFLOW_WAIT = timedelta(seconds=25)
DEVICE_COMMAND_WORKFLOW_TIMEOUT = timedelta(minutes=5)


def _root_cause_message(err: BaseException) -> str:
    """Return the innermost failure message (WorkflowFailure -> Activity -> Application)."""
    while getattr(err, "cause", None) is not None:
        err = err.cause
    return getattr(err, "message", None) or str(err)


async def _start_and_wait(workflow_run, arg, *, id: str, summary: str, execution_timeout: timedelta):
    """Start a workflow and wait for it.

    Returns (handle, None) when it finished, or (handle, JSONResponse 202)
    when the wait ran out. Raises HTTPException 502 if the workflow failed.

    The UI polls the API rather than listening to Firestore, so waiting here
    means the next poll already sees the updated snapshot (ADR 0006).
    """
    temporal_client = await get_temporal_client()
    handle = await temporal_client.start_workflow(
        workflow_run,
        arg,
        id=id,
        task_queue=TEMPORAL_TASK_QUEUE,
        execution_timeout=execution_timeout,
        static_summary=summary,
    )
    try:
        await asyncio.wait_for(handle.result(), timeout=WORKFLOW_WAIT.total_seconds())
    except asyncio.TimeoutError:
        return handle, JSONResponse(
            status_code=202, content={"status": "pending", "workflow_id": handle.id}
        )
    except WorkflowFailureError as e:
        message = _root_cause_message(e)
        logger.warning("Workflow %s failed: %s", handle.id, message)
        raise HTTPException(502, message)
    return handle, None


# ---------------------------------------------------------------------------
# Reads (Firestore)
# ---------------------------------------------------------------------------


@router.get("")
async def list_feeders():
    feeders = await snapshots.list_feeder_snapshots()
    return [_public_feeder(f) for f in sorted(feeders, key=lambda f: f["id"])]


@router.get("/{device_id}")
async def get_feeder(device_id: int):
    return _public_feeder(await _require_snapshot(device_id))


@router.get("/{device_id}/records")
async def get_feeder_records(device_id: int):
    snapshot = await _require_snapshot(device_id)
    return snapshot.get("records") or dict(EMPTY_RECORDS)


# ---------------------------------------------------------------------------
# Refresh (PetKit -> Firestore, via the worker)
# ---------------------------------------------------------------------------


@router.post("/refresh")
async def refresh_all_feeders():
    """Refresh every feeder's snapshot. Also how a brand-new database gets data."""
    _, pending = await _start_and_wait(
        RefreshFeedersWorkflow.run,
        RefreshSnapshotsInput(device_id=None),
        id=f"refresh-feeders-manual-{uuid.uuid4().hex[:8]}",
        summary="Refresh all feeders",
        execution_timeout=timedelta(minutes=5),
    )
    if pending:
        return pending
    return await list_feeders()


@router.post("/{device_id}/refresh")
async def refresh_feeder(device_id: int):
    await _require_feeder(device_id)
    _, pending = await _start_and_wait(
        RefreshFeedersWorkflow.run,
        RefreshSnapshotsInput(device_id=device_id),
        id=f"refresh-feeder-{device_id}-{uuid.uuid4().hex[:8]}",
        summary="Refresh feeder",
        execution_timeout=timedelta(minutes=5),
    )
    if pending:
        return pending
    return _public_feeder(await _require_snapshot(device_id))


# ---------------------------------------------------------------------------
# Feeding
# ---------------------------------------------------------------------------


class ManualFeedRequest(BaseModel):
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@router.post("/{device_id}/feed")
async def manual_feed_endpoint(device_id: int, req: ManualFeedRequest):
    await _require_feeder(device_id)

    if req.amount is None and req.amount1 is None and req.amount2 is None:
        raise HTTPException(400, "Must provide amount, amount1, or amount2")

    logger.info("Manual feed requested for device %s: %s", device_id, req.model_dump(exclude_none=True))

    # Every feed runs through FeedWorkflow (trigger -> verify -> alert on failure).
    # The fixed workflow ID with a FAIL conflict policy means one manual feed at a
    # time per device: a second click while one is running gets a 409.
    temporal_client = await get_temporal_client()
    try:
        handle = await temporal_client.start_workflow(
            FeedWorkflow.run,
            FeedInput(
                device_id=device_id,
                amount=req.amount,
                amount1=req.amount1,
                amount2=req.amount2,
                feed_type="manual",
            ),
            id=schedules.manual_feed_workflow_id(device_id),
            task_queue=TEMPORAL_TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            execution_timeout=schedules.FEED_WORKFLOW_TIMEOUT,
            static_summary="Manual feed",
        )
    except WorkflowAlreadyStartedError:
        raise HTTPException(409, "A feed is already in progress for this device")

    return {"status": "ok", "via": "workflow", "workflow_id": handle.id}


# ---------------------------------------------------------------------------
# Device commands
# ---------------------------------------------------------------------------


class UpdateSettingsRequest(BaseModel):
    settings: dict


async def _run_device_command(device_id: int, command: str, payload: dict | None = None):
    await _require_feeder(device_id)
    handle, pending = await _start_and_wait(
        DeviceCommandWorkflow.run,
        DeviceCommandInput(device_id=device_id, command=command, payload=payload),
        id=f"device-command-{device_id}-{command}-{uuid.uuid4().hex[:8]}",
        summary=f"Device command: {command}",
        execution_timeout=DEVICE_COMMAND_WORKFLOW_TIMEOUT,
    )
    if pending:
        return pending
    return {"status": "ok", "workflow_id": handle.id}


@router.post("/{device_id}/settings")
async def update_settings(device_id: int, req: UpdateSettingsRequest):
    return await _run_device_command(device_id, "update_settings", req.settings)


@router.post("/{device_id}/desiccant/reset")
async def reset_desiccant(device_id: int):
    return await _run_device_command(device_id, "reset_desiccant")


@router.post("/{device_id}/food-replenished")
async def food_replenished(device_id: int):
    return await _run_device_command(device_id, "food_replenished")


# ---------------------------------------------------------------------------
# Schedules (Temporal only)
# ---------------------------------------------------------------------------


class ScheduleRequest(BaseModel):
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM, 24h
    amount: int = Field(gt=0)


async def _schedule_response(temporal_client, device_id: int) -> dict | None:
    schedule = await schedules.describe_schedule(temporal_client, device_id)
    if schedule is None:
        return None
    last_feed_result = await schedules.latest_feed_result(temporal_client, device_id)
    last_alert = None
    if last_feed_result and last_feed_result["status"] == "failure":
        last_alert = {
            "device_id": device_id,
            "reason": last_feed_result["message"],
            "timestamp": last_feed_result["timestamp"],
        }
    return {**schedule, "last_feed_result": last_feed_result, "last_alert": last_alert}


@router.get("/{device_id}/schedule")
async def get_schedule(device_id: int):
    """Describe the feeder's Temporal Schedule and its most recent feed result."""
    await _require_feeder(device_id)
    temporal_client = await get_temporal_client()
    return await _schedule_response(temporal_client, device_id)


@router.put("/{device_id}/schedule")
async def set_schedule(device_id: int, req: ScheduleRequest):
    """Create or update the feeder's daily Temporal Schedule."""
    await _require_feeder(device_id)
    hour, minute = (int(p) for p in req.time.split(":"))

    temporal_client = await get_temporal_client()
    await schedules.upsert_schedule(temporal_client, device_id, hour, minute, req.amount)
    return await _schedule_response(temporal_client, device_id)


@router.delete("/{device_id}/schedule")
async def delete_schedule(device_id: int):
    """Delete the feeder's Temporal Schedule. Feeds already running finish normally."""
    await _require_feeder(device_id)
    temporal_client = await get_temporal_client()
    await schedules.delete_schedule(temporal_client, device_id)
    return {"status": "ok"}
