from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pypetkitapi.exceptions import PetkitSessionExpiredError, PypetkitError

from backend.client import get_client, get_feeders, refresh_data, reset_client
from backend import config
from backend.config import PETKIT_TIMEZONE

# ApplicationError types the workflow inspects to decide what to do next.
FEEDER_NOT_FOUND = "FeederNotFound"  # permanent — no point retrying or verifying
FEED_REJECTED = "FeedRejected"  # PetKit returned an explicit error for the command
FEED_NOT_YET_RECORDED = "FeedNotYetRecorded"  # verification poll: retry later
COMMAND_REJECTED = "CommandRejected"  # PetKit returned an explicit error for a device command
UNKNOWN_COMMAND = "UnknownCommand"  # programming error — never retry


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


async def _client_with_feeder(device_id: int):
    """Return the PetKit client, making sure the feeder exists."""
    client = await get_client()
    if device_id in get_feeders(client):
        return client

    # The entities cache may be empty because of a stale session.
    # Try one refresh before giving up.
    client = await refresh_data()
    if device_id not in get_feeders(client):
        raise ApplicationError(
            f"Feeder {device_id} not found",
            type=FEEDER_NOT_FOUND,
            non_retryable=True,
        )
    return client


async def _send_feed_command(input: ManualFeedInput) -> None:
    from pypetkitapi.command import FeederCommand

    client = await _client_with_feeder(input.device_id)

    payload = {}
    if input.amount is not None:
        payload["amount"] = input.amount
    if input.amount1 is not None:
        payload["amount1"] = input.amount1
    if input.amount2 is not None:
        payload["amount2"] = input.amount2

    await client.send_api_request(input.device_id, FeederCommand.MANUAL_FEED, payload)


@activity.defn
async def trigger_feed(input: ManualFeedInput) -> dict:
    """Send a feed command to the device.

    This activity is NOT idempotent: if it times out after PetKit accepted the
    command, running it again dispenses food twice. So the workflow runs it
    with maximum_attempts=1 and decides whether to try again only after
    verify_feed checks the device's feed log.

    An expired PetKit session is the one failure we retry here. PetKit rejected
    the request before acting on it, so sending it again is safe.
    """
    try:
        try:
            await _send_feed_command(input)
        except PetkitSessionExpiredError:
            activity.logger.info("PetKit session expired; re-authenticating once")
            await reset_client()
            await _send_feed_command(input)
    except PypetkitError as e:
        raise ApplicationError(str(e), type=FEED_REJECTED) from e

    return {"status": "ok", "device_id": input.device_id}


# Device commands the API can send through DeviceCommandWorkflow, mapped to
# (pypetkitapi command class name, member name). Resolved lazily so this module
# doesn't import pypetkitapi.command at load time.
DEVICE_COMMANDS: dict[str, tuple[str, str]] = {
    "update_settings": ("DeviceCommand", "UPDATE_SETTING"),
    "reset_desiccant": ("FeederCommand", "RESET_DESICCANT"),
    "food_replenished": ("FeederCommand", "FOOD_REPLENISHED"),
}


@dataclass
class DeviceCommandInput:
    device_id: int
    command: str  # a key of DEVICE_COMMANDS
    payload: dict | None = None


def _resolve_command(name: str):
    import pypetkitapi.command as petkit_command

    try:
        class_name, member = DEVICE_COMMANDS[name]
    except KeyError:
        raise ApplicationError(
            f"Unknown device command: {name}", type=UNKNOWN_COMMAND, non_retryable=True
        )
    return getattr(getattr(petkit_command, class_name), member)


async def _send_device_command(input: DeviceCommandInput) -> None:
    command = _resolve_command(input.command)
    client = await _client_with_feeder(input.device_id)
    await client.send_api_request(input.device_id, command, input.payload)


@activity.defn
async def send_device_command(input: DeviceCommandInput) -> dict:
    """Send a non-feed command (settings, desiccant reset, food replenished).

    Unlike trigger_feed, these commands are idempotent (setting a value or
    resetting a counter twice has the same effect), so the workflow lets
    Temporal retry them normally.
    """
    try:
        try:
            await _send_device_command(input)
        except PetkitSessionExpiredError:
            activity.logger.info("PetKit session expired; re-authenticating once")
            await reset_client()
            await _send_device_command(input)
    except PypetkitError as e:
        raise ApplicationError(str(e), type=COMMAND_REJECTED) from e

    return {"status": "ok", "device_id": input.device_id, "command": input.command}


@dataclass
class VerifyFeedInput:
    device_id: int
    not_before: str | None = None  # isoformat time string


@dataclass
class VerifyFeedResult:
    device_id: int
    feed_at: str | None = None  # isoformat UTC timestamp of the matching feed event


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


async def _refreshed_client_with_feeder(device_id: int):
    """Refresh device data and make sure the feeder exists.

    Session expiry resets the client and re-raises, so the activity's retry
    policy gets a fresh session on the next attempt.
    """
    try:
        client = await refresh_data()
    except PetkitSessionExpiredError:
        await reset_client()
        raise

    if device_id not in get_feeders(client):
        raise ApplicationError(
            f"Feeder {device_id} not found",
            type=FEEDER_NOT_FOUND,
            non_retryable=True,
        )
    return client


@activity.defn
async def verify_feed(input: VerifyFeedInput) -> VerifyFeedResult:
    """Check the device's feed log for a feed event at or after not_before.

    PetKit has no webhooks, so we have to poll. Each call checks once: if
    there's no matching event yet, it raises a retryable FeedNotYetRecorded
    error and the workflow's retry policy decides how often and how long to
    keep checking. When retries run out, the workflow treats the feed as
    unconfirmed.
    """
    client = await _refreshed_client_with_feeder(input.device_id)

    not_before = _parse_iso_datetime(input.not_before)
    latest_event = await _latest_d4_feed_timestamp(client, input.device_id)

    if _is_after_threshold(latest_event, not_before):
        return VerifyFeedResult(
            device_id=input.device_id,
            feed_at=latest_event.isoformat() if latest_event else None,
        )

    raise ApplicationError(
        "Feed not yet recorded by device",
        type=FEED_NOT_YET_RECORDED,
    )


@dataclass
class RecentFeedCheckInput:
    device_id: int


@dataclass
class RecentFeedCheck:
    fed_recently: bool
    last_feed_at: str | None = None  # isoformat UTC
    window_hours: float = 0.0  # the FEED_SKIP_WINDOW_HOURS value used


@activity.defn
async def check_recent_feed(input: RecentFeedCheckInput) -> RecentFeedCheck:
    """Report whether the feeder dispensed food within FEED_SKIP_WINDOW_HOURS.

    Scheduled feeds use this to skip themselves after a recent manual feed, or
    after a feed from the PetKit app or the button on the device. It reads the
    device's own feed log, so no workflow has to remember earlier feeds.

    The window is read from config on every run (activities may read config;
    workflows may not), so changing it only takes a worker restart. Existing
    schedules don't need to be re-saved. 0 or less disables skipping.
    """
    window_hours = config.FEED_SKIP_WINDOW_HOURS
    if window_hours <= 0:
        return RecentFeedCheck(fed_recently=False, window_hours=window_hours)

    client = await _refreshed_client_with_feeder(input.device_id)
    latest_event = await _latest_d4_feed_timestamp(client, input.device_id)
    if latest_event is None:
        return RecentFeedCheck(fed_recently=False, window_hours=window_hours)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    return RecentFeedCheck(
        fed_recently=latest_event >= cutoff,
        last_feed_at=latest_event.isoformat(),
        window_hours=window_hours,
    )


@dataclass
class RefreshSnapshotsInput:
    device_id: int | None = None  # None = every feeder on the account


@dataclass
class RefreshSnapshotsResult:
    device_ids: list[int]


@activity.defn
async def refresh_feeder_snapshots(input: RefreshSnapshotsInput) -> RefreshSnapshotsResult:
    """Fetch fresh device data from PetKit and write snapshots to Firestore.

    This is how the API learns device state without its own PetKit session
    (ADR 0006). Read-only on the PetKit side, so it retries freely.
    """
    from backend import snapshots
    from backend.client import fetch_d4_feed_events
    from backend.serializers import d4_records_from_events, serialize_feeder, serialize_records

    try:
        client = await refresh_data()
    except PetkitSessionExpiredError:
        await reset_client()
        raise

    feeders = get_feeders(client)
    if input.device_id is not None:
        if input.device_id not in feeders:
            raise ApplicationError(
                f"Feeder {input.device_id} not found",
                type=FEEDER_NOT_FOUND,
                non_retryable=True,
            )
        feeders = {input.device_id: feeders[input.device_id]}

    for device_id, feeder in feeders.items():
        device_type = getattr(getattr(feeder, "device_nfo", None), "device_type", None)
        if device_type == "d4":
            # pypetkitapi doesn't parse D4 records; use the raw endpoint.
            records = d4_records_from_events(await fetch_d4_feed_events(client, device_id, days=7))
        else:
            records = serialize_records(feeder)
        await snapshots.write_feeder_snapshot(serialize_feeder(feeder), records)

    return RefreshSnapshotsResult(device_ids=list(feeders))


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
async def alert_feed_failure(input: FeedAlertInput) -> FeedAlert:
    """Compensation step: create an alert when a feed can't be confirmed.

    In a production system, this could send a push notification, email,
    or SMS. Here it logs the failure and returns alert data that ends up
    in the workflow result for the frontend.
    """
    activity.logger.warning(
        "FEED ALERT: Device %s - %s", input.device_id, input.reason
    )

    return FeedAlert(
        device_id=input.device_id,
        reason=input.reason,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
