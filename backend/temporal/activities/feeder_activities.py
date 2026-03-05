import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from temporalio import activity
from temporalio.exceptions import ApplicationError

from backend.client import get_client, get_feeders, refresh_data
from backend.config import PETKIT_TIMEZONE

# ---------------------------------------------------------------------------
# Idempotency cache for trigger_feed
# ---------------------------------------------------------------------------
# Temporal retries an activity when it times out or the worker crashes after
# the API call succeeds but before the result is recorded.  For trigger_feed
# this would dispense food twice.  We cache successful results keyed by the
# activity's stable identity (workflow_id + run_id + activity_id), which
# stays the same across retry attempts.  A 5-minute TTL covers the worst-
# case retry window (3 attempts × 30s timeout + backoff ≈ 66s).

_DEDUP_TTL_SECONDS = 300

_feed_dedup_cache: dict[str, tuple[dict, float]] = {}


def _dedup_check(key: str) -> dict | None:
    """Return cached result if *key* exists and hasn't expired."""
    entry = _feed_dedup_cache.get(key)
    if entry is None:
        return None
    result, expiry = entry
    if time.monotonic() > expiry:
        del _feed_dedup_cache[key]
        return None
    return result


def _dedup_record(key: str, result: dict) -> None:
    """Store *result* under *key* with a TTL, evicting stale entries."""
    now = time.monotonic()
    expired = [k for k, (_, exp) in _feed_dedup_cache.items() if now > exp]
    for k in expired:
        del _feed_dedup_cache[k]
    _feed_dedup_cache[key] = (result, now + _DEDUP_TTL_SECONDS)


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
    """Trigger a feeding on a feeder device.

    Uses an in-process idempotency cache keyed by activity identity to
    prevent duplicate feeds when Temporal retries after a timeout where
    the API call actually succeeded.
    """
    # Build a dedup key from the activity's stable identity.  activity.info()
    # raises RuntimeError outside a real activity context (e.g. in unit tests);
    # in that case we skip the dedup check entirely.
    dedup_key: str | None = None
    try:
        info = activity.info()
        dedup_key = f"{info.workflow_id}-{info.workflow_run_id}-{info.activity_id}"
    except RuntimeError:
        pass

    if dedup_key is not None:
        cached = _dedup_check(dedup_key)
        if cached is not None:
            activity.logger.info(
                "Skipping duplicate feed for device %s (retry of activity %s, attempt %s)",
                input.device_id,
                info.activity_id,
                info.attempt,
            )
            return cached

    from pypetkitapi.command import FeederCommand

    client = await get_client()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
        # if the device isn't in the petkit entities cache, retries won't change the result
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

    result = {"status": "ok", "device_id": input.device_id}
    if dedup_key is not None:
        _dedup_record(dedup_key, result)
    return result


@dataclass
class VerifyFeedInput:
    device_id: int
    is_manual: bool = False
    not_before: str | None = None # isoformat time string 


@dataclass
class VerifyFeedResult:
    outcome: str  # "verified" | "failed"
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
    """Return latest D4 feed event timestamp from the shared feed statistics helper."""
    from backend.client import fetch_d4_feed_events
    from zoneinfo import ZoneInfo

    events = await fetch_d4_feed_events(client, device_id, days=2)
    if not events:
        return None

    try:
        tz = ZoneInfo(PETKIT_TIMEZONE)
    except Exception:
        tz = timezone.utc

    ev = events[0]  # newest first
    candidate_local = datetime(
        ev["date"].year, ev["date"].month, ev["date"].day, tzinfo=tz
    ) + timedelta(seconds=ev["seconds"])
    return candidate_local.astimezone(timezone.utc)


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

    Refreshes device data from the PetKit cloud API and checks D4 feed
    statistics records for a recent feed event after not_before.
    """
    client = await refresh_data()
    feeders = get_feeders(client)

    if input.device_id not in feeders:
        return VerifyFeedResult(
            outcome="failed",
            device_id=input.device_id,
            error_msg="Feeder not found during verification",
        )

    not_before = _parse_iso_datetime(input.not_before)
    latest_event = await _latest_d4_feed_timestamp(client, input.device_id)

    if _is_after_threshold(latest_event, not_before):
        return VerifyFeedResult(outcome="verified", device_id=input.device_id)

    return VerifyFeedResult(
        outcome="failed",
        device_id=input.device_id,
        error_msg="No recent feed record found in device telemetry",
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
