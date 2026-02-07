import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pypetkitapi.command import FeederCommand

from backend.client import get_client
from backend.config import PETKIT_TIMEZONE

logger = logging.getLogger(__name__)

SCHEDULES_FILE = Path(__file__).parent / "schedules.json"

_task: asyncio.Task | None = None
_fed_today: set[int] = set()  # device IDs already fed in the current minute window


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
    tz = ZoneInfo(PETKIT_TIMEZONE)
    while True:
        try:
            now = datetime.now(tz)
            current_time = now.strftime("%H:%M")
            schedules = load_schedules()

            for device_id_str, sched in schedules.items():
                device_id = int(device_id_str)

                if sched["time"] != current_time:
                    _fed_today.discard(device_id)
                    continue

                if device_id in _fed_today:
                    continue

                _fed_today.add(device_id)

                if sched.get("skip_next"):
                    logger.info("Skipping scheduled feed for device %s (manual feed override)", device_id)
                    schedules[device_id_str]["skip_next"] = False
                    save_schedules(schedules)
                    continue

                logger.info("Dispensing scheduled feed for device %s: %sg", device_id, sched["amount"])
                try:
                    client = await get_client()
                    await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, {"amount": sched["amount"]})
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
