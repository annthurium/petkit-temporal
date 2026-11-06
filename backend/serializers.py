"""Turn pypetkitapi objects into plain dicts for device snapshots.

Pure functions with no PetKit or Firestore I/O. The worker builds snapshots
with these; the API only ever sees the resulting dicts (ADR 0006).
"""

import json


def serialize_feeder(feeder) -> dict:
    """Convert a Feeder object to a JSON-safe dict (the shape the frontend expects)."""
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


EMPTY_RECORDS = {"eat": [], "feed": [], "move": [], "pet": []}


def d4_records_from_events(events: list[dict]) -> dict:
    """Format fetch_d4_feed_events output for the records endpoint."""
    feed_list = []
    for ev in events:
        hours, remainder = divmod(ev["seconds"], 3600)
        minutes = remainder // 60
        feed_list.append({
            "date": ev["date"].strftime("%Y-%m-%d"),
            "time": f"{hours:02d}:{minutes:02d}",
            "amount": ev["amount"],
        })
    return {**EMPTY_RECORDS, "feed": feed_list}


def serialize_records(feeder) -> dict:
    """Records for non-D4 feeders, from pypetkitapi's parsed device_records."""
    records = getattr(feeder, "device_records", None)
    if not records:
        return dict(EMPTY_RECORDS)

    def _serialize_record_list(items):
        if not items:
            return []
        return [
            {k: v for k, v in vars(item).items() if not k.startswith("_")}
            for item in items
        ]

    return {
        key: _serialize_record_list(getattr(records, key, None))
        for key in EMPTY_RECORDS
    }


def firestore_safe(value):
    """Make a value storable in Firestore.

    Firestore accepts maps, arrays, strings, numbers, booleans, and null, but
    not an array directly inside another array. Nested arrays become JSON
    strings (none of them are read by the frontend today). Anything else that
    isn't a basic type becomes its string form.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): firestore_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [
            json.dumps(item, default=str) if isinstance(item, (list, tuple)) else firestore_safe(item)
            for item in value
        ]
    return str(value)
