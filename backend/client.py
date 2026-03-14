from datetime import date, datetime, timedelta
from http import HTTPMethod
from zoneinfo import ZoneInfo

import aiohttp
from pypetkitapi.client import PetKitClient
from pypetkitapi.const import PetkitEndpoint
from pypetkitapi.feeder_container import Feeder

from backend.config import PETKIT_USERNAME, PETKIT_PASSWORD, PETKIT_REGION, PETKIT_TIMEZONE




_client: PetKitClient | None = None
_session: aiohttp.ClientSession | None = None


async def get_client() -> PetKitClient:
    global _client, _session
    if _client is None:
        _session = aiohttp.ClientSession()
        _client = PetKitClient(
            username=PETKIT_USERNAME,
            password=PETKIT_PASSWORD,
            region=PETKIT_REGION,
            timezone=PETKIT_TIMEZONE,
            session=_session,
        )
        await _client.get_devices_data()
    return _client


async def refresh_data() -> PetKitClient:
    client = await get_client()
    await client.get_devices_data()
    return client


def get_feeders(client: PetKitClient) -> dict[int, Feeder]:
    return {
        k: v for k, v in client.petkit_entities.items()
        if isinstance(v, Feeder)
    }


async def fetch_d4_feed_events(
    client: PetKitClient, device_id: int, days: int = 2,
) -> list[dict]:
    """Fetch D4 feed events from the raw feedStatistic endpoint.

    The pypetkitapi library doesn't parse D4 responses correctly — it expects
    {eat: [], feed: [], ...} but D4 returns {YYYYMMDD: {seconds: amount}}.
    We make the raw request ourselves and normalize the result.

    Returns list of dicts sorted newest-first:
        {"date": date, "seconds": int, "amount": value}
    where ``seconds`` is seconds-since-midnight in the device timezone.
    """
    try:
        tz = ZoneInfo(PETKIT_TIMEZONE)
    except Exception:
        from datetime import timezone
        tz = timezone.utc

    today = datetime.now(tz).date()
    events: list[dict] = []

    for days_ago in range(days):
        day = today - timedelta(days=days_ago)
        date_str = day.strftime("%Y%m%d")
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
            except (TypeError, ValueError):
                continue
            events.append({"date": day, "seconds": seconds, "amount": amount})

    events.sort(key=lambda e: (e["date"], e["seconds"]), reverse=True)
    return events


async def reset_client() -> None:
    """Tear down the cached client so the next get_client() re-authenticates.

    Called when the PetKit session token expires — clears the singleton and
    its HTTP session so a fresh login happens on the next request.
    """
    global _client, _session
    old_session = _session
    _client = None
    _session = None
    if old_session:
        await old_session.close()


async def shutdown():
    await reset_client()
