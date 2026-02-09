import aiohttp
from pypetkitapi.client import PetKitClient
from pypetkitapi.feeder_container import Feeder
from backend.config import NUM_RETRIES, PETKIT_USERNAME, PETKIT_PASSWORD, PETKIT_REGION, PETKIT_TIMEZONE

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type




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

# TODO: Should I narrow this to only certain exceptions?
@retry(
    stop=stop_after_attempt(NUM_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),

)
async def send_api_request_with_retry(client, device_id, command, payload):
    await client.send_api_request(device_id, command, payload)


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
