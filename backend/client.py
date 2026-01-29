import aiohttp
from pypetkitapi.client import PetKitClient
from pypetkitapi.const import DEVICES_FEEDER
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


async def shutdown():
    global _client, _session
    _client = None
    if _session:
        await _session.close()
        _session = None
