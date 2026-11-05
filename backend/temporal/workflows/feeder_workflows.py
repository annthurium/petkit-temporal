import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from backend.temporal.activities.feeder_activities import (
        FEEDER_NOT_FOUND,
        UNKNOWN_COMMAND,
        DeviceCommandInput,
        FeedAlertInput,
        ManualFeedInput,
        RecentFeedCheckInput,
        VerifyFeedInput,
        alert_feed_failure,
        check_recent_feed,
        send_device_command,
        trigger_feed,
        verify_feed,
    )

# Read-only / side-effect-free activities can retry freely.
ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    backoff_coefficient=2.0,
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
)

# trigger_feed dispenses food, so it must never be retried blindly. The
# workflow decides whether to send the command again based on verify_feed.
TRIGGER_RETRY_POLICY = RetryPolicy(maximum_attempts=1)

# verify_feed polls through Temporal retries: it raises FeedNotYetRecorded
# until the event appears in the device's feed log. This keeps checking every
# 10s for about a minute without a hand-written polling loop.
VERIFY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=1.0,
    maximum_attempts=6,
    non_retryable_error_types=[FEEDER_NOT_FOUND],
)

# PetKit's cloud updates asynchronously; give the device a moment before the
# first verification check.
VERIFY_INITIAL_DELAY = timedelta(seconds=10)

# Device commands (settings, desiccant reset, food replenished) are idempotent,
# so they retry normally, except for errors that can never succeed.
DEVICE_COMMAND_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    backoff_coefficient=2.0,
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    non_retryable_error_types=[FEEDER_NOT_FOUND, UNKNOWN_COMMAND],
)

# How many times to send the feed command if verification can't confirm it.
MAX_FEED_ATTEMPTS = 2

ACTIVITY_TIMEOUT = timedelta(seconds=30)


@dataclass
class FeedInput:
    device_id: int
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None
    # "manual" | "scheduled". Scheduled feeds skip themselves if the feeder
    # dispensed food within FEED_SKIP_WINDOW_HOURS (read by check_recent_feed).
    feed_type: str = "manual"


@dataclass
class FeedResult:
    status: str  # "success" | "failure" | "skipped"
    feed_type: str
    message: str
    timestamp: str
    attempts: int = 0


def _failure_message(err: ActivityError) -> str:
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return cause.message
    return str(cause) if cause else str(err)


def _is_feeder_not_found(err: ActivityError) -> bool:
    return isinstance(err.cause, ApplicationError) and err.cause.type == FEEDER_NOT_FOUND


@workflow.defn
class FeedWorkflow:
    """Dispense one feed and confirm the device actually did it.

    Started by a Temporal Schedule (feed_type="scheduled") or by the API for
    "Feed now" (feed_type="manual"). Each run is short-lived: optional skip
    check -> trigger -> verify (-> retry once) -> alert on failure.
    """

    @workflow.run
    async def run(self, input: FeedInput) -> FeedResult:
        if input.feed_type == "scheduled":
            recent = await workflow.execute_activity(
                check_recent_feed,
                RecentFeedCheckInput(device_id=input.device_id),
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
                summary="Skip if the feeder dispensed food recently",
            )
            if recent.fed_recently:
                workflow.logger.info(
                    f"Skipping scheduled feed for device {input.device_id}: "
                    f"last feed at {recent.last_feed_at}"
                )
                return FeedResult(
                    status="skipped",
                    feed_type=input.feed_type,
                    message=f"Skipped: feeder already dispensed food at {recent.last_feed_at}",
                    timestamp=workflow.now().isoformat(),
                )

        feed_input = ManualFeedInput(
            device_id=input.device_id,
            amount=input.amount,
            amount1=input.amount1,
            amount2=input.amount2,
        )
        failure_reason = "Feed not confirmed by device"
        attempt = 0

        while attempt < MAX_FEED_ATTEMPTS:
            attempt += 1
            attempt_started = workflow.now()
            trigger_error: str | None = None

            try:
                await workflow.execute_activity(
                    trigger_feed,
                    feed_input,
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=TRIGGER_RETRY_POLICY,
                    summary=f"Send feed command (attempt {attempt})",
                )
            except ActivityError as e:
                trigger_error = _failure_message(e)
                if _is_feeder_not_found(e):
                    failure_reason = trigger_error
                    break
                # Other failures are ambiguous: a timeout may have happened
                # after PetKit accepted the command. The feed log decides.
                workflow.logger.warning(
                    f"trigger_feed failed for device {input.device_id}: {trigger_error}; "
                    "checking feed log before deciding"
                )

            await asyncio.sleep(VERIFY_INITIAL_DELAY.total_seconds())

            try:
                verified = await workflow.execute_activity(
                    verify_feed,
                    VerifyFeedInput(
                        device_id=input.device_id,
                        not_before=attempt_started.isoformat(),
                    ),
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=VERIFY_RETRY_POLICY,
                    summary="Wait for the feed to appear in the device's feed log",
                )
                return FeedResult(
                    status="success",
                    feed_type=input.feed_type,
                    message="Feed confirmed by device",
                    timestamp=verified.feed_at or workflow.now().isoformat(),
                    attempts=attempt,
                )
            except ActivityError as e:
                failure_reason = trigger_error or _failure_message(e)
                if _is_feeder_not_found(e):
                    break

        # Compensation: let the user know the pet may not have been fed.
        alert = await workflow.execute_activity(
            alert_feed_failure,
            FeedAlertInput(device_id=input.device_id, reason=failure_reason),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
            summary="Alert the user about the feed failure",
        )
        return FeedResult(
            status="failure",
            feed_type=input.feed_type,
            message=failure_reason,
            timestamp=alert.timestamp,
            attempts=attempt,
        )


@dataclass
class DeviceCommandResult:
    device_id: int
    command: str
    status: str = "ok"


@workflow.defn
class DeviceCommandWorkflow:
    """Send one non-feed command to a feeder (settings, desiccant reset, food replenished).

    A thin wrapper around a single activity. It exists so every PetKit call
    goes through the worker (ADR 0006) and gets retries plus a record in the
    Temporal UI.
    """

    @workflow.run
    async def run(self, input: DeviceCommandInput) -> DeviceCommandResult:
        await workflow.execute_activity(
            send_device_command,
            input,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEVICE_COMMAND_RETRY_POLICY,
            summary=f"Send {input.command}",
        )
        return DeviceCommandResult(device_id=input.device_id, command=input.command)
