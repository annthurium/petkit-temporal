from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from backend.client import get_client, get_feeders


@dataclass
class ManualFeedInput:
    """
    PetKit makes several feeder types:
        - Single-hopper feeders (like the Fresh Element) use amount - one food compartment
        - Dual-hopper feeders (like the YumShare) use amount1 and amount2 - two separate
          food compartments that can dispense different foods

    The API expects different payload keys depending on which feeder model you're controlling:
        Single hopper: {"amount": 5} dispenses 5 portions
        Dual hopper: {"amount1": 3, "amount2": 2} dispenses 3 from hopper 1 and 2 from hopper 2

    All three fields are optional; the caller decides which to provide based on the feeder type.

    """
    device_id: int
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@activity.defn
async def manual_feed(input: ManualFeedInput) -> dict:
    """Trigger a manual feed on a feeder device."""
    from pypetkitapi.command import FeederCommand

    client = await get_client()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
        # petkit_entities dict is populated once, during app startup
        # here we are fetching a specific device from that cached data
        # if the device isn't in the dict, retries won't change the result
        raise ApplicationError(f"Feeder {input.device_id} not found", non_retryable=True)

    payload = {}
    if input.amount is not None:
        payload["amount"] = input.amount
    if input.amount1 is not None:
        payload["amount1"] = input.amount1
    if input.amount2 is not None:
        payload["amount2"] = input.amount2

    await client.send_api_request(input.device_id, FeederCommand.MANUAL_FEED, payload)
    return {"status": "ok", "device_id": input.device_id}
