from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.client import (
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    WorkflowExecutionStatus,
)
from temporalio.service import RPCError, RPCStatusCode

from backend.temporal import schedules
from backend.temporal.workflows.feeder_workflows import FeedResult


def _rpc_error(status: RPCStatusCode) -> RPCError:
    return RPCError("boom", status, b"")


class TestBuildSchedule:
    def test_daily_calendar_in_configured_timezone(self):
        with patch("backend.temporal.schedules.PETKIT_TIMEZONE", "America/New_York"):
            s = schedules.build_schedule(device_id=7, hour=8, minute=30, amount=15)
        [cal] = s.spec.calendars
        assert cal.hour[0].start == 8
        assert cal.minute[0].start == 30
        assert s.spec.time_zone_name == "America/New_York"
        assert s.policy.overlap == ScheduleOverlapPolicy.SKIP

    def test_action_starts_scheduled_feed_workflow(self):
        s = schedules.build_schedule(device_id=7, hour=8, minute=30, amount=15)
        assert isinstance(s.action, ScheduleActionStartWorkflow)
        assert s.action.workflow == "FeedWorkflow"
        assert s.action.id == "feed-7-scheduled"
        assert s.action.execution_timeout == schedules.FEED_WORKFLOW_TIMEOUT
        [feed_input] = s.action.args
        assert feed_input.amount == 15
        assert feed_input.feed_type == "scheduled"

    def test_old_schedule_input_still_decodes(self):
        """Schedules saved before the skip window moved to config still have a
        skip_if_fed_within_hours key. It must be ignored, not break decoding."""
        from temporalio.converter import value_to_type
        from backend.temporal.workflows.feeder_workflows import FeedInput

        legacy = {"device_id": 7, "amount": 10, "feed_type": "scheduled", "skip_if_fed_within_hours": 0.1}
        decoded = value_to_type(FeedInput, legacy)
        assert decoded.feed_type == "scheduled"
        assert decoded.amount == 10

    def test_workflow_ids_do_not_collide_across_devices(self):
        assert not schedules.manual_feed_workflow_id(10).startswith(
            schedules.feed_workflow_id_prefix(1)
        )


class TestUpsertAndDelete:
    @pytest.mark.asyncio
    async def test_creates_when_missing(self):
        client = MagicMock()
        client.create_schedule = AsyncMock()
        await schedules.upsert_schedule(client, 1, 9, 0, 20)
        client.create_schedule.assert_awaited_once()
        assert client.create_schedule.call_args[0][0] == "feeding-schedule-1"

    @pytest.mark.asyncio
    async def test_updates_when_exists(self):
        client = MagicMock()
        client.create_schedule = AsyncMock(side_effect=ScheduleAlreadyRunningError())
        handle = MagicMock()
        handle.update = AsyncMock()
        client.get_schedule_handle.return_value = handle

        await schedules.upsert_schedule(client, 1, 9, 0, 20)

        client.get_schedule_handle.assert_called_once_with("feeding-schedule-1")
        updater = handle.update.call_args[0][0]
        update = updater(None)
        assert update.schedule.spec.calendars[0].hour[0].start == 9

    @pytest.mark.asyncio
    async def test_delete_ignores_not_found(self):
        client = MagicMock()
        client.get_schedule_handle.return_value.delete = AsyncMock(
            side_effect=_rpc_error(RPCStatusCode.NOT_FOUND)
        )
        await schedules.delete_schedule(client, 1)

    @pytest.mark.asyncio
    async def test_delete_raises_other_errors(self):
        client = MagicMock()
        client.get_schedule_handle.return_value.delete = AsyncMock(
            side_effect=_rpc_error(RPCStatusCode.UNAVAILABLE)
        )
        with pytest.raises(RPCError):
            await schedules.delete_schedule(client, 1)

    @pytest.mark.asyncio
    async def test_describe_returns_none_when_missing(self):
        client = MagicMock()
        client.get_schedule_handle.return_value.describe = AsyncMock(
            side_effect=_rpc_error(RPCStatusCode.NOT_FOUND)
        )
        assert await schedules.describe_schedule(client, 1) is None


def _execution(id, status, close_time, run_id="r"):
    return SimpleNamespace(id=id, run_id=run_id, status=status, close_time=close_time)


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class TestLatestFeedResult:
    T0 = datetime(2025, 1, 15, 15, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_none_when_no_feeds(self):
        client = MagicMock()
        client.list_workflows.return_value = _AsyncIter([])
        assert await schedules.latest_feed_result(client, 1) is None

    @pytest.mark.asyncio
    async def test_picks_newest_for_this_device_only(self):
        client = MagicMock()
        client.list_workflows.return_value = _AsyncIter([
            _execution("feed-1-scheduled-2025-01-14T15:00:00Z", WorkflowExecutionStatus.COMPLETED, self.T0 - timedelta(days=1)),
            _execution("feed-10-manual", WorkflowExecutionStatus.COMPLETED, self.T0 + timedelta(hours=1)),
            _execution("feed-1-manual", WorkflowExecutionStatus.COMPLETED, self.T0, run_id="newest"),
        ])
        result = FeedResult(status="success", feed_type="manual", message="ok", timestamp="t", attempts=1)
        handle = MagicMock()
        handle.result = AsyncMock(return_value=result)
        client.get_workflow_handle_for.return_value = handle

        latest = await schedules.latest_feed_result(client, 1)

        assert latest["status"] == "success"
        assert client.get_workflow_handle_for.call_args.kwargs["run_id"] == "newest"

    @pytest.mark.asyncio
    async def test_timed_out_run_reported_as_failure(self):
        client = MagicMock()
        client.list_workflows.return_value = _AsyncIter([
            _execution("feed-1-scheduled-2025-01-15T15:00:00Z", WorkflowExecutionStatus.TIMED_OUT, self.T0),
        ])
        latest = await schedules.latest_feed_result(client, 1)
        assert latest["status"] == "failure"
        assert latest["feed_type"] == "scheduled"
        assert latest["message"] == "Feed workflow timed out"
