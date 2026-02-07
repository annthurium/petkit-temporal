from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pypetkitapi.command import DeviceCommand, FeederCommand

from backend.client import get_client, refresh_data, get_feeders
from backend.scheduler import load_schedules, save_schedules, mark_skip

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
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    return _serialize_records(feeders[device_id])


class ManualFeedRequest(BaseModel):
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@router.post("/{device_id}/feed")
async def manual_feed(device_id: int, req: ManualFeedRequest):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")

    payload = {}
    if req.amount is not None:
        payload["amount"] = req.amount
    if req.amount1 is not None:
        payload["amount1"] = req.amount1
    if req.amount2 is not None:
        payload["amount2"] = req.amount2

    if not payload:
        raise HTTPException(400, "Must provide amount, amount1, or amount2")

    await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, payload)
    mark_skip(device_id)
    return {"status": "ok"}


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


@router.post("/{device_id}/schedule/remove")
async def remove_schedule(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    await client.send_api_request(device_id, FeederCommand.REMOVE_DAILY_FEED, None)
    return {"status": "ok"}


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
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    schedules = load_schedules()
    return schedules.get(str(device_id))


@router.put("/{device_id}/schedule")
async def set_schedule(device_id: int, req: ScheduleRequest):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    schedules = load_schedules()
    schedules[str(device_id)] = {
        "time": req.time,
        "amount": req.amount,
        "skip_next": False,
    }
    save_schedules(schedules)
    return schedules[str(device_id)]


@router.delete("/{device_id}/schedule")
async def delete_schedule(device_id: int):
    client = await get_client()
    feeders = get_feeders(client)
    if device_id not in feeders:
        raise HTTPException(404, "Feeder not found")
    schedules = load_schedules()
    schedules.pop(str(device_id), None)
    save_schedules(schedules)
    return {"status": "ok"}
