from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPMethod
from zoneinfo import ZoneInfo

from temporalio import activity
from temporalio.exceptions import ApplicationError
from pypetkitapi.const import PetkitEndpoint

from backend.client import get_client, get_feeders, refresh_data
from backend.config import PETKIT_TIMEZONE


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
    not_before: str | None = None


@dataclass
class VerifyFeedResult:
    outcome: str  # "verified" | "failed" | "unknown"
    device_id: int
    error_msg: str | None = None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_after_threshold(
    event_time: datetime | None, threshold: datetime | None, tolerance_seconds: int = 90
) -> bool:
    if event_time is None:
        return False
    if threshold is None:
        return True
    return _normalize_datetime(event_time) >= _normalize_datetime(threshold) - timedelta(
        seconds=tolerance_seconds
    )


async def _latest_d4_feed_timestamp(client, device_id: int) -> datetime | None:
    """Return latest D4 feed event timestamp from raw feed statistics endpoint."""
    try:
        petkit_tz = ZoneInfo(PETKIT_TIMEZONE)
    except Exception:
        petkit_tz = timezone.utc

    today = datetime.now(petkit_tz).date()
    latest: datetime | None = None

    for days_ago in range(2):
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
        for seconds_str in day_data.keys():
            try:
                seconds = int(seconds_str)
            except (TypeError, ValueError):
                continue
            candidate_local = datetime(
                day.year, day.month, day.day, tzinfo=petkit_tz
            ) + timedelta(seconds=seconds)
            candidate = candidate_local.astimezone(timezone.utc)
            if latest is None or candidate > latest:
                latest = candidate
    return latest


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
    manual_feed.is_executed. Some devices/API responses can omit manual_feed
    briefly even when a feed succeeds, so this verifier falls back to feeder
    online/error state to avoid false negatives.
    """
    client = await refresh_data()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
        return VerifyFeedResult(
            outcome="failed",
            device_id=input.device_id,
            error_msg="Feeder not found during verification",
        )

    # TODO: we have 2 different ways of caching feeder data in this project. Clean that up.
    feeder = feeders[input.device_id]
    state = getattr(feeder, "state", None)
    device_nfo = getattr(feeder, "device_nfo", None)
    device_type = getattr(device_nfo, "device_type", None)
    is_online = getattr(state, "online", None) if state else None
    error_msg = getattr(state, "error_msg", None) if state else None
    not_before = _parse_iso_datetime(input.not_before)

    if device_type == "d4":
        latest_event = await _latest_d4_feed_timestamp(client, input.device_id)
        if _is_after_threshold(latest_event, not_before):
            return VerifyFeedResult(outcome="verified", device_id=input.device_id)

    manual_feed = getattr(feeder, "manual_feed", None)

    if manual_feed is None:
        if is_online == 0:
            msg = "Feeder is offline — check power and wifi connection"
            return VerifyFeedResult(
                outcome="failed",
                device_id=input.device_id,
                error_msg=msg,
            )
        if error_msg:
            return VerifyFeedResult(
                outcome="failed",
                device_id=input.device_id,
                error_msg=error_msg,
            )
        # Strict mode: absence of manual_feed metadata is inconclusive.
        return VerifyFeedResult(
            outcome="unknown",
            device_id=input.device_id,
            error_msg="Feed confirmation unavailable from device telemetry",
        )

    is_executed = getattr(manual_feed, "is_executed", None)

    if is_executed == 1:
        # Non-D4 models expose execution status directly.
        return VerifyFeedResult(outcome="verified", device_id=input.device_id)

    if is_online == 0:
        msg = "Feeder is offline — check power and wifi connection"
    else:
        msg = error_msg or "Feed was not confirmed by device"

    return VerifyFeedResult(
        outcome="failed",
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
