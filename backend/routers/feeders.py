from datetime import datetime, timedelta
from http import HTTPMethod

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pypetkitapi.command import DeviceCommand, FeederCommand
from pypetkitapi.const import PetkitEndpoint
from temporalio.service import RPCError

from backend.client import get_client, refresh_data, get_feeders, send_api_request_with_retry
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
    """Fetch feed history directly from the D4 feedStatistic endpoint.

    The pypetkitapi library doesn't parse this response correctly — it expects
    {eat: [], feed: [], ...} but D4 returns {YYYYMMDD: {seconds: amount}, realAmount: N}.
    We make the raw request ourselves and convert it.
    """
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]
    all_events = []

    for date_str in dates:
        params = {"date": date_str, "type": 0, "deviceId": device_id}
        response = await client.req.request(
            method=HTTPMethod.POST,
            url=f"d4/{PetkitEndpoint.FEED_STATISTIC}",
            params=params,
            headers=await client.get_session_id(),
        )
        if not isinstance(response, dict):
            continue
        day_data = response.get(date_str)
        if not isinstance(day_data, dict):
            continue
        for seconds_str, amount in day_data.items():
            try:
                seconds = int(seconds_str)
            except ValueError:
                continue
            hours, remainder = divmod(seconds, 3600)
            minutes = remainder // 60
            all_events.append({
                "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
                "time": f"{hours:02d}:{minutes:02d}",
                "amount": amount,
            })

    all_events.sort(key=lambda e: (e["date"], e["time"]), reverse=True)
    return {"eat": [], "feed": all_events, "move": [], "pet": []}


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

    # Try to signal the scheduled feeding workflow if one is running.
    # This ensures manual feeds reset the schedule timer and skip the next scheduled feed.
    try:
        temporal_client = await get_temporal_client()
        handle = temporal_client.get_workflow_handle(f"scheduled-feeding-{device_id}")
        await handle.signal(
            "manual_feed_now",
            ManualFeedSignal(amount=req.amount, amount1=req.amount1, amount2=req.amount2),
        )
        return {"status": "ok", "via": "workflow"}
    except RPCError:
        # No workflow running for this device, fall back to direct API call
        pass
    except Exception as e:
        logger.warning("Unexpected error signaling workflow: %s: %s", type(e).__name__, e)

    # Direct API call fallback
    payload = {}
    if req.amount is not None:
        payload["amount"] = req.amount
    if req.amount1 is not None:
        payload["amount1"] = req.amount1
    if req.amount2 is not None:
        payload["amount2"] = req.amount2

    await send_api_request_with_retry(petkit_client, device_id, FeederCommand.MANUAL_FEED, payload)
    return {"status": "ok", "via": "direct"}


@router.post("/{device_id}/feed/cancel")
async def cancel_feed(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.CANCEL_MANUAL_FEED, None)
    return {"status": "ok"}


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


# Currently unused in the UI — controls PetKit's built-in device schedule,
# not the Temporal-managed schedule.
@router.post("/{device_id}/schedule/remove")
async def remove_schedule(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.REMOVE_DAILY_FEED, None)
    return {"status": "ok"}


# Currently unused in the UI — see remove_schedule above.
@router.post("/{device_id}/schedule/restore")
async def restore_schedule(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.RESTORE_DAILY_FEED, None)
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
        # Extract the schedule input from the workflow
        return {
            "time": f"{desc.search_attributes.get('ScheduledHour', [''])[0]}:{desc.search_attributes.get('ScheduledMinute', [''])[0]}",
            "amount": result.get("feeding_count", 0),
            "running": True,
            "workflow_id": workflow_id,
            **result,
        }
    except RPCError:
        return None


@router.put("/{device_id}/schedule")
async def set_schedule(device_id: int, req: ScheduleRequest):
    """Start a Temporal workflow for scheduled daily feeding.

    If a workflow is already running for this device, it is cancelled first.
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

    # Cancel any existing workflow for this device
    try:
        handle = temporal_client.get_workflow_handle(workflow_id)
        await handle.cancel()
    except RPCError:
        pass  # No existing workflow, that's fine

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
    )

    return {
        "time": req.time,
        "amount": req.amount,
        "skip_next": False,
        "workflow_id": workflow_id,
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
        pass  # No workflow running, that's fine

    return {"status": "ok"}
