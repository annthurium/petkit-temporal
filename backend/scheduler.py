import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pypetkitapi.command import FeederCommand

from backend.client import get_client, refresh_data
from backend.config import PETKIT_TIMEZONE

logger = logging.getLogger(__name__)

SCHEDULES_FILE = Path(__file__).parent / "schedules.json"

_task: asyncio.Task | None = None
_dispatched_this_minute: set[int] = set()  # device IDs already handled in the current scheduled minute


def load_schedules() -> dict[str, dict]:
    if SCHEDULES_FILE.exists():
        return json.loads(SCHEDULES_FILE.read_text())
    return {}


def save_schedules(schedules: dict[str, dict]) -> None:
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2))


def mark_skip(device_id: int) -> None:
    schedules = load_schedules()
    key = str(device_id)
    if key in schedules:
        schedules[key]["skip_next"] = True
        save_schedules(schedules)


async def _schedule_loop() -> None:
    # Polls every 30s. Since a scheduled minute lasts 60s, each schedule will
    # be seen ~2 times per window. _dispatched_this_minute prevents duplicates.
    tz = ZoneInfo(PETKIT_TIMEZONE)
    while True:
        try:
            now = datetime.now(tz)
            current_time = now.strftime("%H:%M")
            schedules = load_schedules()

            for device_id_str, sched in schedules.items():
                device_id = int(device_id_str)

                # Not this device's scheduled minute — clear it from the
                # dedup set so it's eligible again next time its minute arrives.
                if sched["time"] != current_time:
                    _dispatched_this_minute.discard(device_id)
                    continue

                # Already handled during this minute window (fed or skipped).
                if device_id in _dispatched_this_minute:
                    continue

                _dispatched_this_minute.add(device_id)

                # A manual feed was triggered since the last schedule tick,
                # so skip this cycle to avoid double-feeding.
                if sched.get("skip_next"):
                    logger.info("Skipping scheduled feed for device %s (manual feed override)", device_id)
                    schedules[device_id_str]["skip_next"] = False
                    save_schedules(schedules)
                    continue

                logger.info("Dispensing scheduled feed for device %s: %sg", device_id, sched["amount"])
                try:
                    client = await get_client()
                    await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, {"amount": sched["amount"]})
                    await refresh_data()
                except Exception:
                    logger.exception("Failed to dispense scheduled feed for device %s", device_id)

        except Exception:
            logger.exception("Error in schedule loop")

        await asyncio.sleep(30)


def start_scheduler() -> None:
    global _task
    _task = asyncio.create_task(_schedule_loop())
    logger.info("Feeding scheduler started")


async def stop_scheduler() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
        logger.info("Feeding scheduler stopped")
