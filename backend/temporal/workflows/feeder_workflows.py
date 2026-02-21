import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    backoff_coefficient=2.0,
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
)
# TODO: should I add a separate retry policy for feeding?
# Increase backoff interval, maybe try a few more times
# since feeding is a critical task

# Continue-as-new after this many loop iterations to prevent unbounded event
# history growth. Each iteration adds ~2-6 events (timers + activities), so
# 100 iterations stays well under Temporal's 50k event limit.
CONTINUE_AS_NEW_AFTER_ITERATIONS = 100

with workflow.unsafe.imports_passed_through():
    from zoneinfo import ZoneInfo

    from backend.temporal.activities.feeder_activities import (
        FeedAlert,
        FeedAlertInput,
        ManualFeedInput,
        VerifyFeedInput,
        alert_feed_failure,
        trigger_feed,
        verify_feed,
    )


@dataclass
class ManualFeedSignal:
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@dataclass
class FeedingScheduleStatus:
    skip_next_scheduled: bool
    amount: int
    hour: int
    minute: int
    last_alert: dict | None = None
    last_feed_result: "FeedResultStatus | None" = None


@dataclass
class FeedResultStatus:
    status: str  # "success" | "failure"
    feed_type: str
    message: str
    timestamp: str


@dataclass
class DailyScheduledFeedingInput:
    device_id: int
    amount: int
    hour: int  # 0-23, hour of day in the specified timezone
    minute: int = 0  # 0-59
    timezone: str = "America/Los_Angeles"
    # Carried across continue-as-new boundaries to preserve logical state
    initial_skip_next: bool = False
    initial_last_alert: dict | None = None
    initial_last_feed_result: FeedResultStatus | None = None


@workflow.defn
class DailyScheduledFeedingWorkflow:
    """
    A workflow that feeds a pet at a specific time every day.

    This workflow:
    - Feeds at a specific time of day (e.g., 7:00 AM Pacific)
    - Repeats every 24 hours
    - Skips the next scheduled feed if a manual feed is triggered
    - Manual feeds can be triggered via signal at any time
    """

    def __init__(self) -> None:
        self._manual_feed_request: ManualFeedSignal | None = None
        self._skip_next_scheduled: bool = False
        self._amount: int = 0
        self._hour: int = 0
        self._minute: int = 0
        self._iterations: int = 0
        self._last_alert: dict | None = None
        self._last_feed_result: FeedResultStatus | None = None

    def _to_local_naive(self, timezone_str: str):
        """Convert workflow.now() (UTC) to a naive local datetime.

        Avoids datetime.astimezone() which does a C-level isinstance(tz, tzinfo)
        check that fails with Temporal's sandboxed _RestrictedProxy objects.
        Instead, we call tz.utcoffset() (a Python method the proxy can forward)
        and apply the offset manually.

        This method doesn't account for daylight savings time transitions.
        Although bunnies don't understand DST, they will just have to wait an extra hour once a year.
        We all have hardships in life.
        """
        tz = ZoneInfo(timezone_str)
        utc_now = workflow.now()
        offset = tz.utcoffset(utc_now.replace(tzinfo=None))
        return utc_now.replace(tzinfo=None) + offset

    def _get_wait_duration(self, input: DailyScheduledFeedingInput) -> timedelta:
        """Calculate how long to wait until the next scheduled feeding."""
        local_now = self._to_local_naive(input.timezone)
        scheduled_today = local_now.replace(
            hour=input.hour, minute=input.minute, second=0, microsecond=0
        )

        if local_now >= scheduled_today:
            scheduled_today += timedelta(days=1)

        return scheduled_today - local_now

    @workflow.run
    async def run(self, input: DailyScheduledFeedingInput) -> dict:
        self._amount = input.amount
        self._hour = input.hour
        self._minute = input.minute
        self._skip_next_scheduled = input.initial_skip_next
        self._last_alert = input.initial_last_alert
        self._last_feed_result = input.initial_last_feed_result

        while True:
            self._iterations += 1

            # Reset event history periodically to prevent unbounded growth
            if self._iterations >= CONTINUE_AS_NEW_AFTER_ITERATIONS:
                workflow.continue_as_new(
                    DailyScheduledFeedingInput(
                        device_id=input.device_id,
                        amount=self._amount,
                        hour=self._hour,
                        minute=self._minute,
                        timezone=input.timezone,
                        initial_skip_next=self._skip_next_scheduled,
                        initial_last_alert=self._last_alert,
                        initial_last_feed_result=self._last_feed_result,
                    )
                )

            # Calculate wait duration until next scheduled feeding
            wait_duration = self._get_wait_duration(input)
            local_now = self._to_local_naive(input.timezone)
            next_time = local_now + wait_duration
            workflow.logger.info(
                f"Next feeding scheduled in {wait_duration} at "
                f"{next_time.strftime('%Y-%m-%d %H:%M')}"
            )

            # Wait until scheduled time, or until a manual feed is requested.
            # wait_condition raises asyncio.TimeoutError when the timeout
            # expires (i.e. no manual feed signal arrived), which means the
            # scheduled feed time has been reached.
            try:
                await workflow.wait_condition(
                    lambda: self._manual_feed_request is not None,
                    timeout=wait_duration,
                )
            except asyncio.TimeoutError:
                pass

            # Determine feed amounts - manual request overrides scheduled
            if self._manual_feed_request is not None:
                feed_input = ManualFeedInput(
                    device_id=input.device_id,
                    amount=self._manual_feed_request.amount,
                    amount1=self._manual_feed_request.amount1,
                    amount2=self._manual_feed_request.amount2,
                )
                self._manual_feed_request = None
                is_manual = True
            else:
                # Scheduled feed time reached — skip if a manual feed was recently done
                if self._skip_next_scheduled:
                    workflow.logger.info(
                        f"Skipping scheduled feed for device {input.device_id} "
                        "due to recent manual feed"
                    )
                    self._skip_next_scheduled = False
                    continue

                feed_input = ManualFeedInput(device_id=input.device_id, amount=input.amount)
                is_manual = False

            # === SAGA: trigger -> verify -> compensate ===

            feed_type = "Manual" if is_manual else "Scheduled"
            failure_reason = None

            # Step 1: Trigger the feed
            try:
                await workflow.execute_activity(
                    trigger_feed,
                    feed_input,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
            except ActivityError as e:
                # The trigger itself failed (e.g. device offline, API error).
                # Extract the error message and skip straight to compensation.
                failure_reason = str(e.cause) if e.cause else str(e)
                workflow.logger.warning(
                    f"{feed_type} feeding failed for device "
                    f"{input.device_id}: {failure_reason}"
                )

            if failure_reason is None:
                workflow.logger.info(
                    f"{feed_type} feeding triggered for device {input.device_id}, "
                    "verifying..."
                )

                # Brief delay to let the device process the command.
                # PetKit's cloud API updates asynchronously after the command is sent.
                # TODO: make this a const at the top of the file, I no likey arbitrary magic numbers
                await asyncio.sleep(12)
                verify_not_before = workflow.now().isoformat()

                # Step 2: Verify the feed was executed by the device.
                verify_result = await workflow.execute_activity(
                    verify_feed,
                    VerifyFeedInput(
                        device_id=input.device_id,
                        is_manual=is_manual,
                        not_before=verify_not_before,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )

                if verify_result.outcome == "verified":
                    workflow.logger.info(
                        f"{feed_type} feeding verified for device {input.device_id}"
                    )
                    self._last_alert = None
                    self._last_feed_result = FeedResultStatus(
                        status="success",
                        feed_type=feed_type.lower(),
                        message="Feed confirmed by device",
                        timestamp=workflow.now().isoformat(),
                    )
                    if is_manual:
                        # Only skip the next scheduled run after a successful manual feed.
                        self._skip_next_scheduled = True
                else:
                    failure_reason = (
                        verify_result.error_msg or "Feed not confirmed by device"
                    )

            if failure_reason is not None:
                # Step 3 (compensation): Alert the user on failure
                workflow.logger.warning(
                    f"{feed_type} feeding NOT verified for device "
                    f"{input.device_id}: {failure_reason}"
                )
                alert = await workflow.execute_activity(
                    alert_feed_failure,
                    FeedAlertInput(
                        device_id=input.device_id,
                        reason=failure_reason,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
                self._last_alert = {
                    "device_id": alert.device_id,
                    "reason": alert.reason,
                    "timestamp": alert.timestamp,
                }
                self._last_feed_result = FeedResultStatus(
                    status="failure",
                    feed_type=feed_type.lower(),
                    message=failure_reason,
                    timestamp=alert.timestamp,
                )

    @workflow.signal
    def manual_feed_now(self, request: ManualFeedSignal) -> None:
        """Request an immediate manual feed. On success, the next scheduled feed is skipped."""
        self._manual_feed_request = request

    @workflow.query
    def status(self) -> FeedingScheduleStatus:
        """Get current workflow status."""
        return FeedingScheduleStatus(
            skip_next_scheduled=self._skip_next_scheduled,
            amount=self._amount,
            hour=self._hour,
            minute=self._minute,
            last_alert=self._last_alert,
            last_feed_result=self._last_feed_result,
        )
