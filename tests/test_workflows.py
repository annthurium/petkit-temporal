"""Tests for Temporal activities and workflow dataclasses.

The activity functions are tested directly by mocking the PetKit client.
Full workflow integration tests require a running Temporal test server
(started via WorkflowEnvironment.start_time_skipping()) which downloads
a binary on first run — those can be run separately with:
    pytest tests/test_workflows.py -k integration
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio import workflow

import pytest
from temporalio.exceptions import ApplicationError

from backend.temporal.activities.feeder_activities import (
    ManualFeedInput,
    trigger_feed,
)
from backend.temporal.workflows.feeder_workflows import (
    ACTIVITY_RETRY_POLICY,
    DailyScheduledFeedingInput,
    DailyScheduledFeedingWorkflow,
    FeedingScheduleStatus,
    ManualFeedSignal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feeder(device_id=100, name="TestFeeder", online=True, error_msg=None, food=1):
    state = SimpleNamespace(
        wifi=1 if online else 0,
        food=food,
        error_msg=error_msg,
    )
    return SimpleNamespace(id=device_id, name=name, state=state)


# ---------------------------------------------------------------------------
# Activity: trigger_feed
# ---------------------------------------------------------------------------

class TestTriggerFeedActivity:
    @pytest.mark.asyncio
    async def test_sends_single_hopper_feed(self):
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            result = await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            assert result == {"status": "ok", "device_id": 100}
            fake_client.send_api_request.assert_awaited_once()
            call_args = fake_client.send_api_request.call_args[0]
            assert call_args[0] == 100
            assert call_args[2] == {"amount": 10}

    @pytest.mark.asyncio
    async def test_sends_dual_hopper_feed(self):
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            result = await trigger_feed(ManualFeedInput(device_id=100, amount1=5, amount2=3))
            assert result == {"status": "ok", "device_id": 100}
            call_args = fake_client.send_api_request.call_args[0]
            assert call_args[2] == {"amount1": 5, "amount2": 3}

    @pytest.mark.asyncio
    async def test_raises_for_unknown_feeder(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError, match="not found"):
                await trigger_feed(ManualFeedInput(device_id=999, amount=10))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_trigger_feed_input_defaults(self):
        inp = ManualFeedInput(device_id=1)
        assert inp.amount is None
        assert inp.amount1 is None
        assert inp.amount2 is None

    def test_daily_scheduled_feeding_input_defaults(self):
        inp = DailyScheduledFeedingInput(device_id=1, amount=10, hour=7)
        assert inp.minute == 0
        assert inp.timezone == "America/Los_Angeles"

    def test_manual_feed_signal_defaults(self):
        sig = ManualFeedSignal()
        assert sig.amount is None
        assert sig.amount1 is None
        assert sig.amount2 is None


# ---------------------------------------------------------------------------
# Workflow class (unit-level)
# ---------------------------------------------------------------------------

class TestDailyScheduledFeedingWorkflowUnit:
    def test_initial_state(self):
        wf = DailyScheduledFeedingWorkflow()
        assert wf._manual_feed_request is None
        assert wf._skip_next_scheduled is False

    def test_manual_feed_now_signal(self):
        wf = DailyScheduledFeedingWorkflow()
        sig = ManualFeedSignal(amount=5)
        wf.manual_feed_now(sig)
        assert wf._manual_feed_request is sig

    def test_status_query(self):
        wf = DailyScheduledFeedingWorkflow()
        status = wf.status()
        assert status == FeedingScheduleStatus(
            skip_next_scheduled=False,
            amount=0,
            hour=0,
            minute=0,
        )

    def test_status_reflects_state_changes(self):
        wf = DailyScheduledFeedingWorkflow()
        wf._skip_next_scheduled = True
        wf._amount = 15
        wf._hour = 8
        wf._minute = 30
        status = wf.status()
        assert status == FeedingScheduleStatus(
            skip_next_scheduled=True,
            amount=15,
            hour=8,
            minute=30,
        )


# ---------------------------------------------------------------------------
# Retry policy configuration
# ---------------------------------------------------------------------------

class TestRetryPolicyConfiguration:
    def test_maximum_attempts(self):
        assert ACTIVITY_RETRY_POLICY.maximum_attempts == 3

    def test_backoff_coefficient(self):
        assert ACTIVITY_RETRY_POLICY.backoff_coefficient == 2.0

    def test_initial_interval(self):
        assert ACTIVITY_RETRY_POLICY.initial_interval == timedelta(seconds=2)

    def test_maximum_interval(self):
        assert ACTIVITY_RETRY_POLICY.maximum_interval == timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Error retry behavior
# ---------------------------------------------------------------------------

class TestApplicationErrorRetryBehavior:
    """Business errors (feeder not found) should be non-retryable.
    Transient errors (network failures) should propagate for Temporal to retry.
    """

    @pytest.mark.asyncio
    async def test_trigger_feed_not_found_is_non_retryable(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await trigger_feed(ManualFeedInput(device_id=999, amount=10))
            assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_trigger_feed_network_error_is_retryable(self):
        """Transient errors propagate as-is for Temporal to retry."""
        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = ConnectionError("network down")
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            with pytest.raises(ConnectionError):
                await trigger_feed(ManualFeedInput(device_id=100, amount=10))


# ---------------------------------------------------------------------------
# Workflow run() loop — skip-after-manual-feed logic
# ---------------------------------------------------------------------------


class _LoopBreak(Exception):
    """Sentinel to exit the infinite workflow loop in tests."""


class TestSkipAfterManualFeedLogic:
    """Test the skip-next-scheduled logic in the workflow's main loop.

    These tests mock Temporal's workflow module to drive the run() loop
    directly without a Temporal test server.  A _LoopBreak sentinel is
    raised from wait_condition to terminate the loop after each scenario.
    """

    def _make_input(self, **overrides):
        defaults = dict(device_id=100, amount=10, hour=8, minute=0, timezone="UTC")
        defaults.update(overrides)
        return DailyScheduledFeedingInput(**defaults)

    def _workflow_patches(self, wait_side_effect, mock_now=None):
        """Return a combined context manager that patches workflow internals."""
        if mock_now is None:
            mock_now = datetime(2025, 1, 15, 7, 0)
        return (
            patch.object(workflow, "now", return_value=mock_now),
            patch.object(workflow, "logger", MagicMock()),
            patch.object(workflow, "execute_activity", new_callable=AsyncMock),
            patch.object(workflow, "wait_condition", side_effect=wait_side_effect),
        )

    @pytest.mark.asyncio
    async def test_manual_feed_sets_skip_flag(self):
        """After a manual feed, skip_next_scheduled should be True."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                wf._manual_feed_request = ManualFeedSignal(amount=5)
                return
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            activity.assert_called_once()
            feed_input = activity.call_args[0][1]
            assert feed_input.device_id == 100
            assert feed_input.amount == 5
            assert wf._skip_next_scheduled is True

    @pytest.mark.asyncio
    async def test_skip_flag_prevents_scheduled_feed(self):
        """When skip is set, the next scheduled feed is skipped and the flag clears."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input(initial_skip_next=True))

            activity.assert_not_called()
            assert wf._skip_next_scheduled is False

    @pytest.mark.asyncio
    async def test_full_cycle_manual_then_skip_then_normal(self):
        """Manual feed -> skip next scheduled -> normal scheduled feed."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                wf._manual_feed_request = ManualFeedSignal(amount=5)
                return
            if call_count in (2, 3):
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            assert activity.call_count == 2
            # First: manual feed with signal amount
            assert activity.call_args_list[0][0][1].amount == 5
            # Second: scheduled feed with configured amount
            assert activity.call_args_list[1][0][1].amount == 10
            assert wf._skip_next_scheduled is False

    @pytest.mark.asyncio
    async def test_consecutive_manual_feeds_skip_once(self):
        """Two manual feeds back-to-back still only skip one scheduled feed."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count in (1, 2):
                wf._manual_feed_request = ManualFeedSignal(amount=7)
                return
            if call_count == 3:
                # This scheduled feed should be skipped
                raise asyncio.TimeoutError()
            if call_count == 4:
                # This one should proceed normally
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # 2 manual + 1 scheduled = 3 total (the first scheduled was skipped)
            assert activity.call_count == 3
            assert activity.call_args_list[0][0][1].amount == 7
            assert activity.call_args_list[1][0][1].amount == 7
            assert activity.call_args_list[2][0][1].amount == 10

    @pytest.mark.asyncio
    async def test_scheduled_feed_without_prior_manual(self):
        """A normal scheduled feed fires when no manual feed has occurred."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            activity.assert_called_once()
            feed_input = activity.call_args[0][1]
            assert feed_input.device_id == 100
            assert feed_input.amount == 10
            assert wf._skip_next_scheduled is False

    @pytest.mark.asyncio
    async def test_status_query_reflects_skip_flag(self):
        """The status query reports skip_next_scheduled after a manual feed."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                wf._manual_feed_request = ManualFeedSignal(amount=5)
                return
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity, mock_wait:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            status = wf.status()
            assert status.skip_next_scheduled is True
            assert status.amount == 10
            assert status.hour == 8

