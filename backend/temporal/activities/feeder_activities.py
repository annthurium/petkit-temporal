from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from backend.client import get_client, get_feeders, refresh_data


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
async def trigger_feed(input: ManualFeedInput) -> dict:
    """Trigger a feeding on a feeder device."""
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

    try:
        await client.send_api_request(input.device_id, FeederCommand.MANUAL_FEED, payload)
    except Exception as e:
        from pypetkitapi.exceptions import PypetkitError

        if isinstance(e, PypetkitError):
            # Wrap PypetkitError so the error message (e.g. "Device is offline")
            # propagates cleanly to the workflow. Left retryable because device
            # connectivity is transient — it may come back online between retries.
            raise ApplicationError(str(e)) from e
        raise

    return {"status": "ok", "device_id": input.device_id}


@dataclass
class VerifyFeedInput:
    device_id: int


@dataclass
class VerifyFeedResult:
    verified: bool
    device_id: int
    error_msg: str | None = None


@dataclass
class FeedAlertInput:
    device_id: int
    reason: str


@dataclass
class FeedAlert:
    device_id: int
    reason: str
    timestamp: str  # ISO format UTC


@activity.defn
async def verify_feed(input: VerifyFeedInput) -> VerifyFeedResult:
    """Verify that a feed command was actually executed by the device.

    Refreshes device data from the PetKit cloud API and checks
    manual_feed.is_executed. The workflow should add a short delay before
    calling this activity to give the device time to process the command.
    """
    client = await refresh_data()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
        return VerifyFeedResult(
            verified=False,
            device_id=input.device_id,
            error_msg="Feeder not found during verification",
        )

    feeder = feeders[input.device_id]
    state = getattr(feeder, "state", None)
    is_online = getattr(state, "online", None) if state else None
    error_msg = getattr(state, "error_msg", None) if state else None

    manual_feed = getattr(feeder, "manual_feed", None)

    if manual_feed is None:
        if is_online == 0:
            msg = "Feeder is offline — check power and wifi connection"
        else:
            msg = error_msg or "No manual feed data available from device"
        return VerifyFeedResult(
            verified=False,
            device_id=input.device_id,
            error_msg=msg,
        )

    is_executed = getattr(manual_feed, "is_executed", None)

    if is_executed == 1:
        return VerifyFeedResult(verified=True, device_id=input.device_id)

    if is_online == 0:
        msg = "Feeder is offline — check power and wifi connection"
    else:
        msg = error_msg or "Feed was not confirmed by device"

    return VerifyFeedResult(
        verified=False,
        device_id=input.device_id,
        error_msg=msg,
    )


@activity.defn
async def alert_feed_failure(input: FeedAlertInput) -> FeedAlert:
    """Compensation step: create an alert when feed verification fails.

    In a production system, this could send a push notification, email,
    or SMS. Here it logs the failure and returns alert data that the
    workflow stores as queryable state for the frontend.
    """
    from datetime import datetime, timezone

    activity.logger.warning(
        "FEED ALERT: Device %s - %s", input.device_id, input.reason
    )

    return FeedAlert(
        device_id=input.device_id,
        reason=input.reason,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
