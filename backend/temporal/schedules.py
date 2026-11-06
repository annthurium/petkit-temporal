"""Daily feeding schedules backed by Temporal Schedules.

Each feeder has at most one Schedule (id ``feeding-schedule-{device_id}``).
Every day at the configured local time it starts a short-lived FeedWorkflow.
Creating, reading, and deleting schedules only talks to the Temporal server,
so none of it needs a worker running.
"""

from dataclasses import asdict
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
    WorkflowExecutionStatus,
)
from temporalio.service import RPCError, RPCStatusCode

from backend.config import (
    FEED_SKIP_WINDOW_HOURS,
    PETKIT_TIMEZONE,
    SNAPSHOT_REFRESH_MINUTES,
    TEMPORAL_TASK_QUEUE,
)
from backend.temporal.activities.feeder_activities import RefreshSnapshotsInput
from backend.temporal.workflows.feeder_workflows import (
    FeedInput,
    FeedResult,
    FeedWorkflow,
    RefreshFeedersWorkflow,
)

# Upper bound on a single feed run. If no worker picks it up within this time,
# the run times out and shows as a failure, rather than feeding hours late.
FEED_WORKFLOW_TIMEOUT = timedelta(hours=1)

# If the Temporal server itself was unavailable at the scheduled time, still
# start the feed when it comes back, but only within this window.
SCHEDULE_CATCHUP_WINDOW = timedelta(minutes=30)

# How many recent closed FeedWorkflows to scan when looking for a device's
# latest result.
_RECENT_FEEDS_SCAN_LIMIT = 50


REFRESH_SCHEDULE_ID = "feeder-snapshot-refresh"


def schedule_id(device_id: int) -> str:
    return f"feeding-schedule-{device_id}"


def feed_workflow_id_prefix(device_id: int) -> str:
    return f"feed-{device_id}-"


def manual_feed_workflow_id(device_id: int) -> str:
    # Fixed ID + "fail if running" conflict policy = one manual feed at a time
    # per device; a double-clicked button can't dispense twice.
    return f"{feed_workflow_id_prefix(device_id)}manual"


def scheduled_feed_workflow_id(device_id: int) -> str:
    # Temporal appends the scheduled timestamp to this for each run.
    return f"{feed_workflow_id_prefix(device_id)}scheduled"


def _is_not_found(err: RPCError) -> bool:
    return err.status == RPCStatusCode.NOT_FOUND


def build_schedule(device_id: int, hour: int, minute: int, amount: int) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            FeedWorkflow.run,
            FeedInput(device_id=device_id, amount=amount, feed_type="scheduled"),
            id=scheduled_feed_workflow_id(device_id),
            task_queue=TEMPORAL_TASK_QUEUE,
            execution_timeout=FEED_WORKFLOW_TIMEOUT,
            static_summary=f"Scheduled feed: {amount}g",
        ),
        spec=ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    hour=[ScheduleRange(hour)],
                    minute=[ScheduleRange(minute)],
                )
            ],
            # Temporal applies the timezone, DST changes included.
            time_zone_name=PETKIT_TIMEZONE,
        ),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=SCHEDULE_CATCHUP_WINDOW,
        ),
    )


async def upsert_schedule(
    client: Client, device_id: int, hour: int, minute: int, amount: int
) -> None:
    """Create the device's schedule, or replace its spec and action if it exists."""
    schedule = build_schedule(device_id, hour, minute, amount)
    try:
        await client.create_schedule(schedule_id(device_id), schedule)
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id(device_id))
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))


async def delete_schedule(client: Client, device_id: int) -> None:
    try:
        await client.get_schedule_handle(schedule_id(device_id)).delete()
    except RPCError as e:
        if not _is_not_found(e):
            raise


async def describe_schedule(client: Client, device_id: int) -> dict | None:
    """Return the device's schedule settings, or None if it has no schedule."""
    try:
        desc = await client.get_schedule_handle(schedule_id(device_id)).describe()
    except RPCError as e:
        if _is_not_found(e):
            return None
        raise

    calendar = desc.schedule.spec.calendars[0]
    hour = calendar.hour[0].start
    minute = calendar.minute[0].start

    amount = None
    action = desc.schedule.action
    if isinstance(action, ScheduleActionStartWorkflow) and action.args:
        # Described schedules return workflow args as raw payloads.
        [feed_input] = await client.data_converter.decode(action.args, [FeedInput])
        amount = feed_input.amount

    next_times = desc.info.next_action_times
    return {
        "time": f"{hour:02d}:{minute:02d}",
        "amount": amount,
        "timezone": desc.schedule.spec.time_zone_name,
        "next_run": next_times[0].isoformat() if next_times else None,
        "paused": desc.schedule.state.paused,
        "skip_window_hours": FEED_SKIP_WINDOW_HOURS,
    }


async def latest_feed_result(client: Client, device_id: int) -> dict | None:
    """Return the result of the device's most recently closed FeedWorkflow.

    Covers both scheduled and manual feeds. Reading the result of a closed
    workflow only reads its history, so no worker is needed.
    """
    prefix = feed_workflow_id_prefix(device_id)
    latest = None
    async for wf in client.list_workflows(
        "WorkflowType = 'FeedWorkflow' AND ExecutionStatus != 'Running'",
        limit=_RECENT_FEEDS_SCAN_LIMIT,
    ):
        if not wf.id.startswith(prefix) or wf.close_time is None:
            continue
        if latest is None or wf.close_time > latest.close_time:
            latest = wf

    if latest is None:
        return None

    if latest.status == WorkflowExecutionStatus.COMPLETED:
        handle = client.get_workflow_handle_for(FeedWorkflow.run, latest.id, run_id=latest.run_id)
        result: FeedResult = await handle.result()
        return asdict(result)

    status_name = latest.status.name.lower().replace("_", " ") if latest.status else "unknown"
    feed_type = "manual" if latest.id == manual_feed_workflow_id(device_id) else "scheduled"
    return asdict(
        FeedResult(
            status="failure",
            feed_type=feed_type,
            message=f"Feed workflow {status_name}",
            timestamp=latest.close_time.isoformat(),
        )
    )


def build_refresh_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            RefreshFeedersWorkflow.run,
            RefreshSnapshotsInput(device_id=None),
            id="refresh-feeders",
            task_queue=TEMPORAL_TASK_QUEUE,
            execution_timeout=timedelta(minutes=10),
            static_summary="Refresh all feeder snapshots",
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=SNAPSHOT_REFRESH_MINUTES))]
        ),
        # Missed refreshes aren't worth catching up on; the next one will do.
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=timedelta(minutes=1),
        ),
    )


async def ensure_refresh_schedule(client: Client) -> bool:
    """Create the periodic snapshot refresh schedule if it doesn't exist.

    Returns True if it was created. A newly created schedule runs once right
    away so a fresh Firestore database gets data without waiting an interval.
    """
    try:
        await client.create_schedule(
            REFRESH_SCHEDULE_ID, build_refresh_schedule(), trigger_immediately=True
        )
        return True
    except ScheduleAlreadyRunningError:
        return False
