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


@dataclass
class FeederStatus:
    device_id: int
    name: str
    food: int | None
    online: bool | None
    error_msg: str | None


@activity.defn
async def manual_feed(input: ManualFeedInput) -> dict:
    """Trigger a manual feed on a feeder device."""
    from pypetkitapi.command import FeederCommand

    client = await get_client()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
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


@activity.defn
async def cancel_feed(device_id: int) -> dict:
    """Cancel an in-progress manual feed."""
    from pypetkitapi.command import FeederCommand

    client = await get_client()
    feeders = get_feeders(client)

    if device_id not in feeders:
        raise ApplicationError(f"Feeder {device_id} not found", non_retryable=True)

    await client.send_api_request(device_id, FeederCommand.CANCEL_MANUAL_FEED, None)
    return {"status": "ok", "device_id": device_id}


@activity.defn
async def get_feeder_status(device_id: int) -> FeederStatus:
    """Get current status of a feeder device."""
    client = await get_client()
    feeders = get_feeders(client)

    if device_id not in feeders:
        raise ApplicationError(f"Feeder {device_id} not found", non_retryable=True)

    feeder = feeders[device_id]
    state = feeder.state

    wifi = getattr(state, "wifi", None) if state else None
    return FeederStatus(
        device_id=device_id,
        name=feeder.name,
        food=getattr(state, "food", None) if state else None,
        online=bool(wifi) if wifi is not None else None,
        error_msg=getattr(state, "error_msg", None) if state else None,
    )
