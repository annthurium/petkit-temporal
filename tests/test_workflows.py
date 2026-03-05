"""Tests for Temporal activities and workflow dataclasses.

The activity functions are tested directly by mocking the PetKit client.
Full workflow integration tests require a running Temporal test server
(started via WorkflowEnvironment.start_time_skipping()) which downloads
a binary on first run — those can be run separately with:
    pytest tests/test_workflows.py -k integration
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio import workflow

import pytest
from temporalio.exceptions import ApplicationError

from backend.temporal.activities.feeder_activities import (
    FeedAlert,
    FeedAlertInput,
    ManualFeedInput,
    VerifyFeedInput,
    VerifyFeedResult,
    _dedup_check,
    _dedup_record,
    _feed_dedup_cache,
    alert_feed_failure,
    trigger_feed,
    verify_feed,
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
# Idempotency: dedup cache helpers
# ---------------------------------------------------------------------------


class TestDedupCache:
    def setup_method(self):
        _feed_dedup_cache.clear()

    def test_check_returns_none_for_unknown_key(self):
        assert _dedup_check("unknown") is None

    def test_record_then_check_returns_cached_result(self):
        _dedup_record("key1", {"status": "ok", "device_id": 100})
        assert _dedup_check("key1") == {"status": "ok", "device_id": 100}

    def test_expired_entry_returns_none(self):
        import time

        _feed_dedup_cache["old"] = ({"status": "ok"}, time.monotonic() - 1)
        assert _dedup_check("old") is None
        assert "old" not in _feed_dedup_cache

    def test_record_evicts_expired_entries(self):
        import time

        _feed_dedup_cache["stale"] = ({"status": "ok"}, time.monotonic() - 1)
        _dedup_record("fresh", {"status": "ok", "device_id": 200})
        assert "stale" not in _feed_dedup_cache
        assert _dedup_check("fresh") is not None


# ---------------------------------------------------------------------------
# Idempotency: trigger_feed retry dedup
# ---------------------------------------------------------------------------


class TestTriggerFeedIdempotency:
    def setup_method(self):
        _feed_dedup_cache.clear()

    def _activity_info_patch(self, **kwargs):
        """Patch temporalio.activity.info to return a fake ActivityInfo."""
        return patch(
            "temporalio.activity.info",
            return_value=MagicMock(**kwargs),
        )

    @pytest.mark.asyncio
    async def test_retry_returns_cached_result_without_calling_api(self):
        """A retried trigger_feed should return the cached result without hitting the PetKit API."""
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        info_kwargs = dict(
            workflow_id="wf-1", workflow_run_id="run-1", activity_id="act-1", attempt=1,
        )

        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
            self._activity_info_patch(**info_kwargs),
        ):
            # First call — should hit the API and cache the result
            result1 = await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            assert result1 == {"status": "ok", "device_id": 100}
            assert fake_client.send_api_request.await_count == 1

        # Simulate retry (attempt 2) — same activity identity, fresh mocks
        info_kwargs["attempt"] = 2
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
            self._activity_info_patch(**info_kwargs),
        ):
            fake_client.send_api_request.reset_mock()
            result2 = await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            assert result2 == {"status": "ok", "device_id": 100}
            # API should NOT have been called again
            fake_client.send_api_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_activity_ids_are_not_deduped(self):
        """Different activity invocations (different activity_id) should each call the API."""
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}

        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
            self._activity_info_patch(
                workflow_id="wf-1", workflow_run_id="run-1", activity_id="act-1", attempt=1,
            ),
        ):
            await trigger_feed(ManualFeedInput(device_id=100, amount=10))

        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
            self._activity_info_patch(
                workflow_id="wf-1", workflow_run_id="run-1", activity_id="act-2", attempt=1,
            ),
        ):
            await trigger_feed(ManualFeedInput(device_id=100, amount=5))

        assert fake_client.send_api_request.await_count == 2

    @pytest.mark.asyncio
    async def test_no_dedup_outside_activity_context(self):
        """Without an activity context (e.g. unit tests), dedup is skipped gracefully."""
        fake_client = AsyncMock()
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            # Call twice — both should hit the API since there's no activity context
            await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            assert fake_client.send_api_request.await_count == 2


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
            last_alert=None,
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
            last_alert=None,
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

    @pytest.mark.asyncio
    async def test_trigger_feed_petkit_error_wraps_as_retryable(self):
        """PypetkitError is wrapped in ApplicationError with the API message, and is retryable."""
        from pypetkitapi.exceptions import PypetkitError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = PypetkitError(
            "Device is offline. You cannot change your feeding plan."
        )
        feeders = {100: _make_feeder(100)}
        with (
            patch("backend.temporal.activities.feeder_activities.get_client", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value=feeders),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await trigger_feed(ManualFeedInput(device_id=100, amount=10))
            assert "offline" in str(exc_info.value).lower()
            assert exc_info.value.non_retryable is False


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
            patch("asyncio.sleep", new_callable=AsyncMock),
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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed
                VerifyFeedResult(outcome="verified", device_id=100),
            ]
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # trigger_feed + verify_feed = 2 calls per feed
            assert activity.call_count == 2
            feed_input = activity.call_args_list[0][0][1]
            assert feed_input.device_id == 100
            assert feed_input.amount == 5
            assert wf._skip_next_scheduled is True

    @pytest.mark.asyncio
    async def test_failed_manual_feed_does_not_set_skip_flag(self):
        """Failed manual feeds should not cause the next scheduled feed to be skipped."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                wf._manual_feed_request = ManualFeedSignal(amount=5)
                return
            raise _LoopBreak()

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed
                VerifyFeedResult(outcome="failed", device_id=100, error_msg="Device offline"),
                FeedAlert(device_id=100, reason="Device offline", timestamp="2025-01-15T08:00:00Z"),
            ]

            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            assert activity.call_count == 3
            assert wf._skip_next_scheduled is False

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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed (manual)
                VerifyFeedResult(outcome="verified", device_id=100),
                None,  # trigger_feed (scheduled)
                VerifyFeedResult(outcome="verified", device_id=100),
            ]
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # 2 feeds × 2 calls each (trigger + verify) = 4
            assert activity.call_count == 4
            # First: manual feed with signal amount
            assert activity.call_args_list[0][0][1].amount == 5
            # Second: scheduled feed with configured amount (index 2, after verify)
            assert activity.call_args_list[2][0][1].amount == 10
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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed (manual 1)
                VerifyFeedResult(outcome="verified", device_id=100),
                None,  # trigger_feed (manual 2)
                VerifyFeedResult(outcome="verified", device_id=100),
                None,  # trigger_feed (scheduled)
                VerifyFeedResult(outcome="verified", device_id=100),
            ]
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # 3 feeds × 2 calls each (trigger + verify) = 6
            assert activity.call_count == 6
            assert activity.call_args_list[0][0][1].amount == 7
            assert activity.call_args_list[2][0][1].amount == 7
            assert activity.call_args_list[4][0][1].amount == 10

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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed
                VerifyFeedResult(outcome="verified", device_id=100),
            ]
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # trigger_feed + verify_feed = 2 calls
            assert activity.call_count == 2
            feed_input = activity.call_args_list[0][0][1]
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

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(wait_effect)
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            activity.side_effect = [
                None,  # trigger_feed
                VerifyFeedResult(outcome="verified", device_id=100),
            ]
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            status = wf.status()
            assert status.skip_next_scheduled is True
            assert status.amount == 10
            assert status.hour == 8


# ---------------------------------------------------------------------------
# Activity: verify_feed
# ---------------------------------------------------------------------------


class TestVerifyFeedActivity:
    @pytest.mark.asyncio
    async def test_d4_records_confirm_feed(self):
        """Verification uses D4 feed statistics records."""
        fake_client = AsyncMock()
        now = datetime.now(timezone.utc)
        today = now.date()
        seconds = now.hour * 3600 + now.minute * 60 + now.second
        fake_events = [{"date": today, "seconds": seconds, "amount": 10}]
        feeder = SimpleNamespace(id=100)
        with (
            patch("backend.temporal.activities.feeder_activities.PETKIT_TIMEZONE", "UTC"),
            patch("backend.temporal.activities.feeder_activities.refresh_data", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={100: feeder}),
            patch("backend.client.fetch_d4_feed_events", return_value=fake_events),
        ):
            result = await verify_feed(
                VerifyFeedInput(
                    device_id=100,
                    not_before=(now - timedelta(seconds=30)).isoformat(),
                )
            )
            assert result.outcome == "verified"

    @pytest.mark.asyncio
    async def test_d4_records_older_than_threshold_fails(self):
        """Old D4 records should not verify a new feed command."""
        fake_client = AsyncMock()
        now = datetime.now(timezone.utc)
        today = now.date()
        # seconds=1 is 00:00:01 — well before any recent not_before threshold
        fake_events = [{"date": today, "seconds": 1, "amount": 10}]
        feeder = SimpleNamespace(id=100)
        with (
            patch("backend.temporal.activities.feeder_activities.PETKIT_TIMEZONE", "UTC"),
            patch("backend.temporal.activities.feeder_activities.refresh_data", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={100: feeder}),
            patch("backend.client.fetch_d4_feed_events", return_value=fake_events),
        ):
            result = await verify_feed(
                VerifyFeedInput(
                    device_id=100,
                    not_before=(now - timedelta(seconds=30)).isoformat(),
                )
            )
            assert result.outcome == "failed"

    @pytest.mark.asyncio
    async def test_no_feed_records_fails(self):
        """No feed records at all should fail verification."""
        fake_client = AsyncMock()
        feeder = SimpleNamespace(id=100)
        with (
            patch("backend.temporal.activities.feeder_activities.PETKIT_TIMEZONE", "UTC"),
            patch("backend.temporal.activities.feeder_activities.refresh_data", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={100: feeder}),
            patch("backend.client.fetch_d4_feed_events", return_value=[]),
        ):
            result = await verify_feed(VerifyFeedInput(device_id=100))
            assert result.outcome == "failed"

    @pytest.mark.asyncio
    async def test_feeder_not_found_during_verification(self):
        """Returns not verified when feeder disappears between trigger and verify."""
        fake_client = AsyncMock()
        with (
            patch("backend.temporal.activities.feeder_activities.refresh_data", return_value=fake_client),
            patch("backend.temporal.activities.feeder_activities.get_feeders", return_value={}),
        ):
            result = await verify_feed(VerifyFeedInput(device_id=999))
            assert result.outcome == "failed"
            assert "not found" in result.error_msg


# ---------------------------------------------------------------------------
# Activity: alert_feed_failure
# ---------------------------------------------------------------------------


class TestAlertFeedFailureActivity:
    @pytest.mark.asyncio
    async def test_returns_alert_with_details(self):
        result = await alert_feed_failure(
            FeedAlertInput(device_id=100, reason="Feed not confirmed")
        )
        assert result.device_id == 100
        assert result.reason == "Feed not confirmed"
        assert result.timestamp  # Non-empty ISO string


# ---------------------------------------------------------------------------
# Workflow run() loop — saga compensation flow
# ---------------------------------------------------------------------------


class TestSagaCompensationFlow:
    """Test the trigger -> verify -> compensate saga in the workflow loop."""

    def _make_input(self, **overrides):
        defaults = dict(device_id=100, amount=10, hour=8, minute=0, timezone="UTC")
        defaults.update(overrides)
        return DailyScheduledFeedingInput(**defaults)

    def _workflow_patches(self, wait_side_effect, activity_side_effect, mock_now=None):
        if mock_now is None:
            mock_now = datetime(2025, 1, 15, 7, 0)
        return (
            patch.object(workflow, "now", return_value=mock_now),
            patch.object(workflow, "logger", MagicMock()),
            patch.object(workflow, "execute_activity", new_callable=AsyncMock, side_effect=activity_side_effect),
            patch.object(workflow, "wait_condition", side_effect=wait_side_effect),
            patch("asyncio.sleep", new_callable=AsyncMock),
        )

    @pytest.mark.asyncio
    async def test_successful_verification_clears_alert(self):
        """When verify_feed returns verified, no compensation runs and alert is cleared."""
        wf = DailyScheduledFeedingWorkflow()
        wf._last_alert = {"device_id": 100, "reason": "old alert", "timestamp": "..."}
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        async def activity_effect(activity_fn, input, **kwargs):
            if activity_fn == trigger_feed:
                return {"status": "ok", "device_id": 100}
            if activity_fn == verify_feed:
                return VerifyFeedResult(outcome="verified", device_id=100)
            raise AssertionError(f"Unexpected activity: {activity_fn}")

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(
            wait_effect, activity_effect
        )
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # trigger_feed + verify_feed = 2 calls, no compensation
            assert activity.call_count == 2
            assert wf._last_alert is None

    @pytest.mark.asyncio
    async def test_failed_verification_triggers_compensation(self):
        """When verify_feed returns failed, alert_feed_failure runs."""
        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        async def activity_effect(activity_fn, input, **kwargs):
            if activity_fn == trigger_feed:
                return {"status": "ok", "device_id": 100}
            if activity_fn == verify_feed:
                return VerifyFeedResult(
                    outcome="failed", device_id=100, error_msg="Food hopper jammed"
                )
            if activity_fn == alert_feed_failure:
                return FeedAlert(
                    device_id=100,
                    reason="Food hopper jammed",
                    timestamp="2025-01-15T07:00:05+00:00",
                )
            raise AssertionError(f"Unexpected activity: {activity_fn}")

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(
            wait_effect, activity_effect
        )
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # trigger_feed + verify_feed + alert_feed_failure = 3 calls
            assert activity.call_count == 3
            assert wf._last_alert is not None
            assert wf._last_alert["reason"] == "Food hopper jammed"
            assert wf._last_alert["device_id"] == 100

    def test_alert_visible_in_status_query(self):
        """The status query includes last_alert after a failed verification."""
        wf = DailyScheduledFeedingWorkflow()
        wf._amount = 10
        wf._hour = 8
        wf._minute = 0
        wf._last_alert = {
            "device_id": 100,
            "reason": "Feed not confirmed",
            "timestamp": "2025-01-15T07:00:05+00:00",
        }
        status = wf.status()
        assert status.last_alert is not None
        assert status.last_alert["reason"] == "Feed not confirmed"

    def test_no_alert_in_status_when_no_failure(self):
        """The status query has last_alert=None when all feeds verified."""
        wf = DailyScheduledFeedingWorkflow()
        wf._amount = 10
        wf._hour = 8
        status = wf.status()
        assert status.last_alert is None

    @pytest.mark.asyncio
    async def test_trigger_failure_skips_verify_and_runs_compensation(self):
        """When trigger_feed itself fails, verify is skipped and compensation runs."""
        from temporalio.exceptions import ActivityError

        wf = DailyScheduledFeedingWorkflow()
        call_count = 0

        async def wait_effect(condition, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            raise _LoopBreak()

        async def activity_effect(activity_fn, input, **kwargs):
            if activity_fn == trigger_feed:
                raise ActivityError(
                    "Device is offline. You cannot change your feeding plan.",
                    scheduled_event_id=1,
                    started_event_id=2,
                    identity="test",
                    activity_type="trigger_feed",
                    activity_id="1",
                    retry_state=None,
                )
            if activity_fn == alert_feed_failure:
                return FeedAlert(
                    device_id=100,
                    reason=input.reason,
                    timestamp="2025-01-15T07:00:05+00:00",
                )
            raise AssertionError(f"Unexpected activity: {activity_fn}")

        mock_now, mock_log, mock_activity, mock_wait, mock_sleep = self._workflow_patches(
            wait_effect, activity_effect
        )
        with mock_now, mock_log, mock_activity as activity, mock_wait, mock_sleep:
            with pytest.raises(_LoopBreak):
                await wf.run(self._make_input())

            # trigger_feed (failed) + alert_feed_failure = 2 calls, no verify
            assert activity.call_count == 2
            assert wf._last_alert is not None
            assert "offline" in wf._last_alert["reason"].lower()
