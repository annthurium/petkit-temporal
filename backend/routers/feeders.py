from dataclasses import asdict
from datetime import timedelta

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pypetkitapi.command import DeviceCommand, FeederCommand
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.service import RPCError

from backend.client import get_client, refresh_data, get_feeders, fetch_d4_feed_events
from backend.config import TEMPORAL_TASK_QUEUE, PETKIT_TIMEZONE
from backend.temporal.client import get_temporal_client
from backend.temporal.workflows.feeder_workflows import (
    ManualFeedSignal,
    DailyScheduledFeedingInput,
    DailyScheduledFeedingWorkflow,
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

    # Signal the scheduled feeding workflow to trigger an immediate feed.
    # This ensures the feed goes through the workflow's saga (trigger -> verify -> compensate)
    # and that the next scheduled feed is skipped.
    temporal_client = await get_temporal_client()
    handle = temporal_client.get_workflow_handle(f"scheduled-feeding-{device_id}")

    try:
        await handle.signal(
            "manual_feed_now",
            ManualFeedSignal(amount=req.amount, amount1=req.amount1, amount2=req.amount2),
        )
    except RPCError:
        raise HTTPException(409, "No feeding schedule is running for this device")

    return {"status": "ok", "via": "workflow"}


class UpdateSettingsRequest(BaseModel):
    settings: dict


@router.post("/{device_id}/settings")
async def update_settings(device_id: int, req: UpdateSettingsRequest):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, DeviceCommand.UPDATE_SETTING, req.settings)
    return {"status": "ok"}


@router.post("/{device_id}/desiccant/reset")
async def reset_desiccant(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.RESET_DESICCANT, None)
    return {"status": "ok"}


@router.post("/{device_id}/food-replenished")
async def food_replenished(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.FOOD_REPLENISHED, None)
    return {"status": "ok"}


@router.post("/{device_id}/refresh")
async def refresh_feeder(device_id: int):
    client = await refresh_data()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    return _serialize_feeder(feeders[device_id])


class ScheduleRequest(BaseModel):
    time: str  # HH:MM
    amount: int


@router.get("/{device_id}/schedule")
async def get_schedule(device_id: int):
    """Query the Temporal workflow for the current schedule status."""
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")

    temporal_client = await get_temporal_client()
    workflow_id = f"scheduled-feeding-{device_id}"

    try:
        handle = temporal_client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        # Check that the workflow is actually running
        if desc.status is not None and desc.status.name != "RUNNING":
            return None
        result = await handle.query(DailyScheduledFeedingWorkflow.status)
        last_feed_result = asdict(result.last_feed_result) if result.last_feed_result else None
        return {
            "time": f"{result.hour:02d}:{result.minute:02d}",
            "amount": result.amount,
            "skip_next": result.skip_next_scheduled,
            "running": True,
            "workflow_id": workflow_id,
            "last_alert": result.last_alert,
            "last_feed_result": last_feed_result,
        }
    except RPCError:
        return None


@router.put("/{device_id}/schedule")
async def set_schedule(device_id: int, req: ScheduleRequest):
    """Start a Temporal workflow for scheduled daily feeding.

    If a workflow is already running for this device, it is terminated first.
    """
    petkit_client = await get_client()
    feeders = get_feeders(petkit_client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")

    # Parse HH:MM time string
    parts = req.time.split(":")
    hour = int(parts[0])
    minute = int(parts[1])

    temporal_client = await get_temporal_client()
    workflow_id = f"scheduled-feeding-{device_id}"

    # Terminate any existing workflow for this device
    try:
        handle = temporal_client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            await handle.terminate("Replaced by new schedule")
    except RPCError:
        pass  # No existing workflow, life goes on 😎

    await temporal_client.start_workflow(
        DailyScheduledFeedingWorkflow.run,
        DailyScheduledFeedingInput(
            device_id=device_id,
            amount=req.amount,
            hour=hour,
            minute=minute,
            timezone=PETKIT_TIMEZONE,
        ),
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
        static_summary="Setting schedule for feeder",
        retry_policy=RetryPolicy(
            maximum_attempts=0,  # unlimited retries
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
        ),
        memo={"device_id": device_id, "schedule": req.time, "amount": req.amount},
    )

    return {
        "time": req.time,
        "amount": req.amount,
        "skip_next": False,
        "workflow_id": workflow_id,
        "last_alert": None,
        "last_feed_result": None,
    }


@router.delete("/{device_id}/schedule")
async def delete_schedule(device_id: int):
    """Cancel the Temporal workflow for scheduled feeding."""
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")

    temporal_client = await get_temporal_client()
    workflow_id = f"scheduled-feeding-{device_id}"

    try:
        handle = temporal_client.get_workflow_handle(workflow_id)
        await handle.cancel()
    except RPCError:
        pass  # No workflow running! if it ain't broke don't fix it 

    return {"status": "ok"}
