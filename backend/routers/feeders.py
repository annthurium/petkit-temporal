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

from backend.client import get_client, refresh_data, get_feeders, fetch_d4_feed_events
from backend.config import TEMPORAL_TASK_QUEUE
from backend.temporal import schedules
from backend.temporal.activities.feeder_activities import DeviceCommandInput
from backend.temporal.client import get_temporal_client
from backend.temporal.workflows.feeder_workflows import (
    DeviceCommandWorkflow,
    FeedInput,
    FeedWorkflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feeders", tags=["feeders"])


def _serialize_feeder(feeder) -> dict:
    """Convert a Feeder object to a JSON-safe dict."""
    state = feeder.state
    settings = feeder.settings
    feed_state = getattr(state, "feed_state", None) if state else None
    device_nfo = getattr(feeder, "device_nfo", None)

    return {
        "id": feeder.id,
        "name": feeder.name,
        "type": getattr(device_nfo, "device_type", None) if device_nfo else None,
        "firmware": feeder.firmware,
        "state": {
            "battery_power": getattr(state, "battery_power", None),
            "battery_status": getattr(state, "battery_status", None),
            "food": getattr(state, "food", None),
            "food1": getattr(state, "food1", None),
            "food2": getattr(state, "food2", None),
            "eating": getattr(state, "eating", None),
            "feeding": getattr(state, "feeding", None),
            "desiccant_left_days": getattr(state, "desiccant_left_days", None),
            "error_code": getattr(state, "error_code", None),
            "error_msg": getattr(state, "error_msg", None),
            "weight": getattr(state, "weight", None),
            "pim": getattr(state, "pim", None),
            "online": getattr(state, "wifi", None),
        } if state else None,
        "settings": {
            "light_mode": getattr(settings, "light_mode", None),
            "light_multi_range": getattr(settings, "light_multi_range", None),
            "volume": getattr(settings, "volume", None),
            "feed_sound": getattr(settings, "feed_sound", None),
            "system_sound_enable": getattr(settings, "system_sound_enable", None),
            "tone_mode": getattr(settings, "tone_mode", None),
            "eat_notify": getattr(settings, "eat_notify", None),
            "feed_notify": getattr(settings, "feed_notify", None),
            "food_notify": getattr(settings, "food_notify", None),
            "food_warn": getattr(settings, "food_warn", None),
            "food_warn_range": getattr(settings, "food_warn_range", None),
            "eat_detection": getattr(settings, "eat_detection", None),
            "eat_sensitivity": getattr(settings, "eat_sensitivity", None),
            "move_detection": getattr(settings, "move_detection", None),
            "pet_detection": getattr(settings, "pet_detection", None),
            "surplus_control": getattr(settings, "surplus_control", None),
            "surplus_standard": getattr(settings, "surplus_standard", None),
            "camera": getattr(settings, "camera", None),
        } if settings else None,
        "feed_state": {
            "eat_count": getattr(feed_state, "eat_count", None),
            "eat_avg": getattr(feed_state, "eat_avg", None),
            "add_amount_total": getattr(feed_state, "add_amount_total", None),
            "plan_amount_total": getattr(feed_state, "plan_amount_total", None),
            "real_amount_total": getattr(feed_state, "real_amount_total", None),
        } if feed_state else None,
        "manual_feed": {
            "amount": getattr(feeder.manual_feed, "amount", None),
            "amount1": getattr(feeder.manual_feed, "amount1", None),
            "amount2": getattr(feeder.manual_feed, "amount2", None),
            "time": getattr(feeder.manual_feed, "time", None),
            "status": getattr(feeder.manual_feed, "status", None),
        } if getattr(feeder, "manual_feed", None) else None,
    }


async def _fetch_d4_records(client, device_id: int) -> dict:
    """Fetch feed history for a D4 feeder, formatted for the records endpoint."""
    events = await fetch_d4_feed_events(client, device_id, days=7)
    feed_list = []
    for ev in events:
        hours, remainder = divmod(ev["seconds"], 3600)
        minutes = remainder // 60
        feed_list.append({
            "date": ev["date"].strftime("%Y-%m-%d"),
            "time": f"{hours:02d}:{minutes:02d}",
            "amount": ev["amount"],
        })
    return {"eat": [], "feed": feed_list, "move": [], "pet": []}


def _serialize_records(feeder) -> dict:
    records = feeder.device_records
    if not records:
        return {"eat": [], "feed": [], "move": [], "pet": []}

    def _serialize_record_list(items):
        if not items:
            return []
        result = []
        for item in items:
            result.append({
                k: v for k, v in vars(item).items()
                if not k.startswith("_")
            })
        return result

    return {
        "eat": _serialize_record_list(getattr(records, "eat", None)),
        "feed": _serialize_record_list(getattr(records, "feed", None)),
        "move": _serialize_record_list(getattr(records, "move", None)),
        "pet": _serialize_record_list(getattr(records, "pet", None)),
    }


@router.get("")
async def list_feeders():
    client = await get_client()
    feeders = get_feeders(client)
    return [_serialize_feeder(f) for f in feeders.values()]


@router.get("/{device_id}")
async def get_feeder(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    return _serialize_feeder(feeders[device_id])


@router.get("/{device_id}/records")
async def get_feeder_records(device_id: int):
    client = await refresh_data()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    feeder = feeders[device_id]
    device_type = getattr(getattr(feeder, "device_nfo", None), "device_type", None)
    if device_type == "d4":
        return await _fetch_d4_records(client, device_id)
    return _serialize_records(feeder)


class ManualFeedRequest(BaseModel):
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@router.post("/{device_id}/feed")
async def manual_feed_endpoint(device_id: int, req: ManualFeedRequest):
    petkit_client = await get_client()
    feeders = get_feeders(petkit_client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")

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


class UpdateSettingsRequest(BaseModel):
    settings: dict


# How long a device-command request waits for its workflow before answering
# 202 "pending". The workflow keeps running either way.
DEVICE_COMMAND_WAIT = timedelta(seconds=25)
DEVICE_COMMAND_WORKFLOW_TIMEOUT = timedelta(minutes=5)


def _root_cause_message(err: BaseException) -> str:
    """Return the innermost failure message (WorkflowFailure -> Activity -> Application)."""
    while getattr(err, "cause", None) is not None:
        err = err.cause
    return getattr(err, "message", None) or str(err)


async def _run_device_command(device_id: int, command: str, payload: dict | None = None):
    """Run a DeviceCommandWorkflow and wait for its result.

    Until Firestore exists (step 3), the UI refreshes right after a write, so
    we wait for the outcome instead of returning as soon as the workflow
    starts. See ADR 0006.
    """
    await _require_feeder(device_id)
    temporal_client = await get_temporal_client()
    handle = await temporal_client.start_workflow(
        DeviceCommandWorkflow.run,
        DeviceCommandInput(device_id=device_id, command=command, payload=payload),
        id=f"device-command-{device_id}-{command}-{uuid.uuid4().hex[:8]}",
        task_queue=TEMPORAL_TASK_QUEUE,
        execution_timeout=DEVICE_COMMAND_WORKFLOW_TIMEOUT,
        static_summary=f"Device command: {command}",
    )
    try:
        await asyncio.wait_for(handle.result(), timeout=DEVICE_COMMAND_WAIT.total_seconds())
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=202, content={"status": "pending", "workflow_id": handle.id}
        )
    except WorkflowFailureError as e:
        message = _root_cause_message(e)
        logger.warning("Device command %s failed for %s: %s", command, device_id, message)
        raise HTTPException(502, message)
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


@router.post("/{device_id}/refresh")
async def refresh_feeder(device_id: int):
    client = await refresh_data()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    return _serialize_feeder(feeders[device_id])


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


async def _require_feeder(device_id: int) -> None:
    client = await get_client()
    if device_id not in get_feeders(client):
        raise HTTPException(404, "Feeder not found")


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
