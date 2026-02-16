"""Tests for Temporal activities and workflow dataclasses.

The activity functions are tested directly by mocking the PetKit client.
Full workflow integration tests require a running Temporal test server
(started via WorkflowEnvironment.start_time_skipping()) which downloads
a binary on first run — those can be run separately with:
    pytest tests/test_workflows.py -k integration
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from backend.temporal.activities.feeder_activities import (
    ManualFeedInput,
    FeederStatus,
    manual_feed,
    cancel_feed,
    get_feeder_status,
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
# Activity: manual_feed
# ---------------------------------------------------------------------------

class TestManualFeedActivity:
    @pytest.mark.asyncio
    async def test_sends_single_hopper_feed(self):
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            result = await manual_feed(ManualFeedInput(device_id=100, amount=10))
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
            result = await manual_feed(ManualFeedInput(device_id=100, amount1=5, amount2=3))
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
                await manual_feed(ManualFeedInput(device_id=999, amount=10))


# ---------------------------------------------------------------------------
# Activity: cancel_feed
# ---------------------------------------------------------------------------

class TestCancelFeedActivity:
    @pytest.mark.asyncio
    async def test_cancels_feed(self):
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            result = await cancel_feed(100)
            assert result == {"status": "ok", "device_id": 100}
            fake_client.send_api_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_for_unknown_feeder(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError, match="not found"):
                await cancel_feed(999)


# ---------------------------------------------------------------------------
# Activity: get_feeder_status
# ---------------------------------------------------------------------------

class TestGetFeederStatusActivity:
    @pytest.mark.asyncio
    async def test_returns_status(self):
        fake_client = AsyncMock()
        feeder = _make_feeder(100, online=True, food=1, error_msg=None)
        feeders = {100: feeder}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            status = await get_feeder_status(100)
            assert isinstance(status, FeederStatus)
            assert status.device_id == 100
            assert status.online is True
            assert status.food == 1
            assert status.error_msg is None

    @pytest.mark.asyncio
    async def test_returns_offline_status(self):
        fake_client = AsyncMock()
        feeder = _make_feeder(100, online=False)
        feeders = {100: feeder}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            status = await get_feeder_status(100)
            assert status.online is False

    @pytest.mark.asyncio
    async def test_returns_none_online_when_wifi_missing(self):
        """When the feeder state has no wifi attribute, online should be None (not False).

        Regression test: the workflow previously used `if not status.online` which
        treated None as offline, silently skipping all scheduled feedings.
        """
        fake_client = AsyncMock()
        state = SimpleNamespace(food=1, error_msg=None)  # no wifi attribute
        feeder = SimpleNamespace(id=100, name="NoWifiFeeder", state=state)
        feeders = {100: feeder}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            status = await get_feeder_status(100)
            assert status.online is None

    @pytest.mark.asyncio
    async def test_raises_for_unknown_feeder(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError, match="not found"):
                await get_feeder_status(999)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_manual_feed_input_defaults(self):
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
        assert wf._feeding_count == 0
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
            feeding_count=0,
            skip_next_scheduled=False,
            amount=0,
            hour=0,
            minute=0,
        )

    def test_status_reflects_state_changes(self):
        wf = DailyScheduledFeedingWorkflow()
        wf._feeding_count = 3
        wf._skip_next_scheduled = True
        wf._amount = 15
        wf._hour = 8
        wf._minute = 30
        status = wf.status()
        assert status == FeedingScheduleStatus(
            feeding_count=3,
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
    async def test_manual_feed_not_found_is_non_retryable(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await manual_feed(ManualFeedInput(device_id=999, amount=10))
            assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_cancel_feed_not_found_is_non_retryable(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await cancel_feed(999)
            assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_get_feeder_status_not_found_is_non_retryable(self):
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await get_feeder_status(999)
            assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_manual_feed_network_error_is_retryable(self):
        """Transient errors propagate as-is for Temporal to retry."""
        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = ConnectionError("network down")
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            with pytest.raises(ConnectionError):
                await manual_feed(ManualFeedInput(device_id=100, amount=10))

    @pytest.mark.asyncio
    async def test_get_feeder_status_network_error_is_retryable(self):
        """Transient errors from get_client propagate for Temporal to retry."""
        with patch(
            "backend.temporal.activities.feeder_activities.get_client",
            side_effect=ConnectionError("cannot connect"),
        ):
            with pytest.raises(ConnectionError):
                await get_feeder_status(100)
