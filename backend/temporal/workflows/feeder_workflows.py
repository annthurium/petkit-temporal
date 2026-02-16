import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    backoff_coefficient=2.0,
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
)

# Continue-as-new after this many loop iterations to prevent unbounded event
# history growth. Each iteration adds ~2-6 events (timers + activities), so
# 100 iterations stays well under Temporal's 50k event limit.
CONTINUE_AS_NEW_AFTER_ITERATIONS = 100

with workflow.unsafe.imports_passed_through():
    from zoneinfo import ZoneInfo

    from backend.temporal.activities.feeder_activities import (
        ManualFeedInput,
        manual_feed,
        get_feeder_status,
    )


@dataclass
class ManualFeedSignal:
    amount: int | None = None
    amount1: int | None = None
    amount2: int | None = None


@dataclass
class DailyScheduledFeedingInput:
    device_id: int
    amount: int
    hour: int  # 0-23, hour of day in the specified timezone
    minute: int = 0  # 0-59
    timezone: str = "America/Los_Angeles"
    max_feedings: int | None = None
    # Carried across continue-as-new boundaries to preserve logical state
    initial_feeding_count: int = 0
    initial_skip_next: bool = False


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
        self._feeding_count = 0
        self._manual_feed_request: ManualFeedSignal | None = None
        self._skip_next_scheduled: bool = False
        self._amount: int = 0
        self._hour: int = 0
        self._minute: int = 0
        self._iterations: int = 0

    def _to_local_naive(self, timezone_str: str):
        """Convert workflow.now() (UTC) to a naive local datetime.

        Avoids datetime.astimezone() which does a C-level isinstance(tz, tzinfo)
        check that fails with Temporal's sandboxed _RestrictedProxy objects.
        Instead, we call tz.utcoffset() (a Python method the proxy can forward)
        and apply the offset manually.
        TODO: Is this the correct approach? double check the Temporal docs.
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
        self._feeding_count = input.initial_feeding_count
        self._skip_next_scheduled = input.initial_skip_next

        while True:
            self._iterations += 1

            # Check if we've reached max feedings
            if input.max_feedings is not None and self._feeding_count >= input.max_feedings:
                return {
                    "status": "completed",
                    "total_feedings": self._feeding_count,
                }

            # Reset event history periodically to prevent unbounded growth
            if self._iterations >= CONTINUE_AS_NEW_AFTER_ITERATIONS:
                workflow.continue_as_new(
                    DailyScheduledFeedingInput(
                        device_id=input.device_id,
                        amount=self._amount,
                        hour=self._hour,
                        minute=self._minute,
                        timezone=input.timezone,
                        max_feedings=input.max_feedings,
                        initial_feeding_count=self._feeding_count,
                        initial_skip_next=self._skip_next_scheduled,
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
                # Skip the next scheduled feed since we just fed manually
                self._skip_next_scheduled = True
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

            # Check feeder status before feeding
            status = await workflow.execute_activity(
                get_feeder_status,
                input.device_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

            # Skip if explicitly offline (None means unknown — don't skip)
            if status.online is False:
                workflow.logger.warning(f"Feeder {input.device_id} is offline, skipping")
                continue

            if status.error_msg:
                workflow.logger.warning(f"Feeder has error: {status.error_msg}")
                continue

            # Execute the feeding
            await workflow.execute_activity(
                manual_feed,
                feed_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

            self._feeding_count += 1
            feed_type = "Manual" if is_manual else "Scheduled"
            workflow.logger.info(
                f"{feed_type} feeding #{self._feeding_count} completed for device {input.device_id}"
            )

    @workflow.signal
    def manual_feed_now(self, request: ManualFeedSignal) -> None:
        """Request an immediate manual feed. The next scheduled feed will be skipped."""
        self._manual_feed_request = request

    @workflow.query
    def status(self) -> dict:
        """Get current workflow status."""
        return {
            "feeding_count": self._feeding_count,
            "skip_next_scheduled": self._skip_next_scheduled,
            "amount": self._amount,
            "hour": self._hour,
            "minute": self._minute,
        }
