"""Tests for Temporal activities and FeedWorkflow.

Activities are tested directly with a mocked PetKit client. FeedWorkflow.run
is driven by patching temporalio.workflow internals (execute_activity, now,
logger), so no Temporal test server is needed.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError, TimeoutType

from backend.temporal.activities.feeder_activities import (
    RefreshSnapshotsInput,
    RefreshSnapshotsResult,
    refresh_feeder_snapshots,
    COMMAND_REJECTED,
    UNKNOWN_COMMAND,
    DeviceCommandInput,
    send_device_command,
    FEED_NOT_YET_RECORDED,
    FEED_REJECTED,
    FEEDER_NOT_FOUND,
    FeedAlert,
    FeedAlertInput,
    ManualFeedInput,
    RecentFeedCheck,
    RecentFeedCheckInput,
    VerifyFeedInput,
    VerifyFeedResult,
    alert_feed_failure,
    check_recent_feed,
    trigger_feed,
    verify_feed,
)
from backend.temporal.workflows.feeder_workflows import (
    RefreshFeedersWorkflow,
    DEVICE_COMMAND_RETRY_POLICY,
    DeviceCommandWorkflow,
    MAX_FEED_ATTEMPTS,
    TRIGGER_RETRY_POLICY,
    VERIFY_RETRY_POLICY,
    FeedInput,
    FeedWorkflow,
)

ACT = "backend.temporal.activities.feeder_activities"


def _make_feeder(device_id=100):
    return SimpleNamespace(id=device_id, name="TestFeeder", state=SimpleNamespace())


def _events_at(moment: datetime):
    """Fake fetch_d4_feed_events output with one event at *moment* (UTC)."""
    seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
    return [{"date": moment.date(), "seconds": seconds, "amount": 10}]


# ---------------------------------------------------------------------------
# Activity: trigger_feed
# ---------------------------------------------------------------------------


class TestTriggerFeedActivity:
    @pytest.mark.asyncio
    async def test_sends_single_hopper_feed(self):
        fake_client = AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            result = await trigger_feed(ManualFeedInput(device_id=100, amount=10))
        assert result == {"status": "ok", "device_id": 100}
        args = fake_client.send_api_request.call_args[0]
        assert args[0] == 100
        assert args[2] == {"amount": 10}

    @pytest.mark.asyncio
    async def test_sends_dual_hopper_feed(self):
        fake_client = AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            await trigger_feed(ManualFeedInput(device_id=100, amount1=5, amount2=3))
        assert fake_client.send_api_request.call_args[0][2] == {"amount1": 5, "amount2": 3}

    @pytest.mark.asyncio
    async def test_unknown_feeder_is_non_retryable_not_found(self):
        fake_client = AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={}),
            patch(f"{ACT}.refresh_data", return_value=fake_client),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await trigger_feed(ManualFeedInput(device_id=999, amount=10))
        assert exc_info.value.type == FEEDER_NOT_FOUND
        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_refreshes_before_declaring_not_found(self):
        stale, refreshed = AsyncMock(), AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=stale),
            patch(f"{ACT}.get_feeders", side_effect=[{}, {100: _make_feeder()}]),
            patch(f"{ACT}.refresh_data", return_value=refreshed),
        ):
            await trigger_feed(ManualFeedInput(device_id=100, amount=10))
        refreshed.send_api_request.assert_awaited_once()
        stale.send_api_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_session_reauthenticates_and_resends_once(self):
        """PetKit rejected the first request, so resending is safe."""
        from pypetkitapi.exceptions import PetkitSessionExpiredError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = [PetkitSessionExpiredError("expired"), None]
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
            patch(f"{ACT}.reset_client", new_callable=AsyncMock) as mock_reset,
        ):
            result = await trigger_feed(ManualFeedInput(device_id=100, amount=10))
        assert result["status"] == "ok"
        mock_reset.assert_awaited_once()
        assert fake_client.send_api_request.await_count == 2

    @pytest.mark.asyncio
    async def test_expired_session_twice_raises_feed_rejected(self):
        from pypetkitapi.exceptions import PetkitSessionExpiredError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = PetkitSessionExpiredError("expired")
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
            patch(f"{ACT}.reset_client", new_callable=AsyncMock),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await trigger_feed(ManualFeedInput(device_id=100, amount=10))
        assert exc_info.value.type == FEED_REJECTED
        assert fake_client.send_api_request.await_count == 2

    @pytest.mark.asyncio
    async def test_petkit_error_wrapped_as_feed_rejected(self):
        from pypetkitapi.exceptions import PypetkitError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = PypetkitError(
            "Device is offline. You cannot change your feeding plan."
        )
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await trigger_feed(ManualFeedInput(device_id=100, amount=10))
        assert exc_info.value.type == FEED_REJECTED
        assert "offline" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_network_error_propagates(self):
        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = ConnectionError("network down")
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            with pytest.raises(ConnectionError):
                await trigger_feed(ManualFeedInput(device_id=100, amount=10))


# ---------------------------------------------------------------------------
# Activity: verify_feed (one poll per attempt)
# ---------------------------------------------------------------------------


class TestVerifyFeedActivity:
    def _patches(self, events, feeders=None):
        return (
            patch(f"{ACT}.PETKIT_TIMEZONE", "UTC"),
            patch(f"{ACT}.refresh_data", return_value=AsyncMock()),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()} if feeders is None else feeders),
            patch("backend.client.fetch_d4_feed_events", return_value=events),
        )

    @pytest.mark.asyncio
    async def test_recent_event_verifies(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        p1, p2, p3, p4 = self._patches(_events_at(now))
        with p1, p2, p3, p4:
            result = await verify_feed(
                VerifyFeedInput(device_id=100, not_before=(now - timedelta(seconds=30)).isoformat())
            )
        assert isinstance(result, VerifyFeedResult)
        assert result.feed_at is not None

    @pytest.mark.asyncio
    async def test_old_event_raises_retryable_not_yet_recorded(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=2)
        p1, p2, p3, p4 = self._patches(_events_at(old))
        with p1, p2, p3, p4:
            with pytest.raises(ApplicationError) as exc_info:
                await verify_feed(VerifyFeedInput(device_id=100, not_before=now.isoformat()))
        assert exc_info.value.type == FEED_NOT_YET_RECORDED
        assert exc_info.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_no_events_raises_not_yet_recorded(self):
        p1, p2, p3, p4 = self._patches([])
        with p1, p2, p3, p4:
            with pytest.raises(ApplicationError) as exc_info:
                await verify_feed(VerifyFeedInput(device_id=100))
        assert exc_info.value.type == FEED_NOT_YET_RECORDED

    @pytest.mark.asyncio
    async def test_missing_feeder_is_non_retryable(self):
        p1, p2, p3, p4 = self._patches([], feeders={})
        with p1, p2, p3, p4:
            with pytest.raises(ApplicationError) as exc_info:
                await verify_feed(VerifyFeedInput(device_id=100))
        assert exc_info.value.type == FEEDER_NOT_FOUND
        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_expired_session_resets_client_and_raises(self):
        from pypetkitapi.exceptions import PetkitSessionExpiredError

        with (
            patch(f"{ACT}.refresh_data", side_effect=PetkitSessionExpiredError("expired")),
            patch(f"{ACT}.reset_client", new_callable=AsyncMock) as mock_reset,
        ):
            with pytest.raises(PetkitSessionExpiredError):
                await verify_feed(VerifyFeedInput(device_id=100))
        mock_reset.assert_awaited_once()


# ---------------------------------------------------------------------------
# Activity: check_recent_feed
# ---------------------------------------------------------------------------


class TestCheckRecentFeedActivity:
    def _patches(self, events, window_hours=4.0):
        return (
            patch(f"{ACT}.PETKIT_TIMEZONE", "UTC"),
            patch("backend.config.FEED_SKIP_WINDOW_HOURS", window_hours),
            patch(f"{ACT}.refresh_data", return_value=AsyncMock()),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
            patch("backend.client.fetch_d4_feed_events", return_value=events),
        )

    @pytest.mark.asyncio
    async def test_feed_inside_window(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        p1, p2, p3, p4, p5 = self._patches(_events_at(recent), window_hours=4)
        with p1, p2, p3, p4, p5:
            result = await check_recent_feed(RecentFeedCheckInput(device_id=100))
        assert result.fed_recently is True
        assert result.last_feed_at is not None
        assert result.window_hours == 4

    @pytest.mark.asyncio
    async def test_feed_outside_window(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        p1, p2, p3, p4, p5 = self._patches(_events_at(old), window_hours=0.1)
        with p1, p2, p3, p4, p5:
            result = await check_recent_feed(RecentFeedCheckInput(device_id=100))
        assert result.fed_recently is False

    @pytest.mark.asyncio
    async def test_window_read_at_run_time(self):
        """Changing config affects the next run; no schedule re-save needed."""
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        p1, p2, p3, p4, p5 = self._patches(_events_at(recent), window_hours=12)
        with p1, p2, p3, p4, p5:
            assert (await check_recent_feed(RecentFeedCheckInput(device_id=100))).fed_recently is True
        p1, p2, p3, p4, p5 = self._patches(_events_at(recent), window_hours=0.5)
        with p1, p2, p3, p4, p5:
            assert (await check_recent_feed(RecentFeedCheckInput(device_id=100))).fed_recently is False

    @pytest.mark.asyncio
    async def test_zero_window_disables_without_calling_petkit(self):
        with (
            patch("backend.config.FEED_SKIP_WINDOW_HOURS", 0),
            patch(f"{ACT}.refresh_data", new_callable=AsyncMock) as mock_refresh,
        ):
            result = await check_recent_feed(RecentFeedCheckInput(device_id=100))
        assert result.fed_recently is False
        mock_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_events(self):
        p1, p2, p3, p4, p5 = self._patches([])
        with p1, p2, p3, p4, p5:
            result = await check_recent_feed(RecentFeedCheckInput(device_id=100))
        assert result == RecentFeedCheck(fed_recently=False, window_hours=4.0)


# ---------------------------------------------------------------------------
# Activity: alert_feed_failure
# ---------------------------------------------------------------------------


class TestAlertFeedFailureActivity:
    @pytest.mark.asyncio
    async def test_returns_alert_with_details(self):
        result = await alert_feed_failure(FeedAlertInput(device_id=100, reason="Feed not confirmed"))
        assert result.device_id == 100
        assert result.reason == "Feed not confirmed"
        assert result.timestamp


# ---------------------------------------------------------------------------
# Retry policies
# ---------------------------------------------------------------------------


class TestRetryPolicies:
    def test_trigger_is_never_retried_by_temporal(self):
        assert TRIGGER_RETRY_POLICY.maximum_attempts == 1

    def test_verify_polls_at_fixed_interval(self):
        assert VERIFY_RETRY_POLICY.backoff_coefficient == 1.0
        assert VERIFY_RETRY_POLICY.maximum_attempts > 1
        assert FEEDER_NOT_FOUND in VERIFY_RETRY_POLICY.non_retryable_error_types


# ---------------------------------------------------------------------------
# FeedWorkflow.run
# ---------------------------------------------------------------------------


def _activity_error(cause: BaseException) -> ActivityError:
    err = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test",
        activity_type="x",
        activity_id="1",
        retry_state=None,
    )
    err.__cause__ = cause
    return err


def _not_yet_recorded():
    return _activity_error(ApplicationError("Feed not yet recorded by device", type=FEED_NOT_YET_RECORDED))


class _FakeActivities:
    """Scripted responses for workflow.execute_activity, keyed by activity fn."""

    def __init__(self, **responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, object, dict]] = []

    async def __call__(self, fn, arg, **kwargs):
        name = fn.__name__
        self.calls.append((name, arg, kwargs))
        if name == "refresh_feeder_snapshots" and name not in self.responses:
            # Snapshot refreshes succeed unless a test scripts otherwise.
            return RefreshSnapshotsResult(device_ids=[arg.device_id])
        queue = self.responses.get(name)
        if not queue:
            raise AssertionError(f"Unexpected activity call: {name}")
        result = queue.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def names(self):
        return [c[0] for c in self.calls]


async def _run(input: FeedInput, fake: _FakeActivities):
    async def execute_activity(fn, arg, **kwargs):
        return await fake(fn, arg, **kwargs)

    with (
        patch.object(workflow, "now", return_value=datetime(2025, 1, 15, 15, 0, tzinfo=timezone.utc)),
        patch.object(workflow, "logger", MagicMock()),
        patch.object(workflow, "execute_activity", side_effect=execute_activity),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        return await FeedWorkflow().run(input)


ALERT = FeedAlert(device_id=100, reason="x", timestamp="2025-01-15T15:02:00+00:00")
VERIFIED = VerifyFeedResult(device_id=100, feed_at="2025-01-15T15:00:10+00:00")


class TestFeedWorkflow:
    @pytest.mark.asyncio
    async def test_manual_feed_success(self):
        fake = _FakeActivities(trigger_feed=[{"status": "ok"}], verify_feed=[VERIFIED])
        result = await _run(FeedInput(device_id=100, amount=5), fake)

        assert result.status == "success"
        assert result.feed_type == "manual"
        assert result.attempts == 1
        assert result.timestamp == VERIFIED.feed_at
        assert fake.names() == ["trigger_feed", "verify_feed", "refresh_feeder_snapshots"]
        assert fake.calls[0][1].amount == 5
        assert fake.calls[0][2]["retry_policy"] is TRIGGER_RETRY_POLICY
        assert fake.calls[1][2]["retry_policy"] is VERIFY_RETRY_POLICY

    @pytest.mark.asyncio
    async def test_manual_feed_never_checks_recent_feeds(self):
        fake = _FakeActivities(trigger_feed=[{"status": "ok"}], verify_feed=[VERIFIED])
        await _run(FeedInput(device_id=100, amount=5), fake)
        assert "check_recent_feed" not in fake.names()

    @pytest.mark.asyncio
    async def test_scheduled_feed_skips_when_recently_fed(self):
        fake = _FakeActivities(
            check_recent_feed=[RecentFeedCheck(fed_recently=True, last_feed_at="2025-01-15T09:00:00+00:00")]
        )
        result = await _run(
            FeedInput(device_id=100, amount=10, feed_type="scheduled"), fake
        )
        assert result.status == "skipped"
        assert fake.names() == ["check_recent_feed"]
        assert fake.calls[0][1].device_id == 100

    @pytest.mark.asyncio
    async def test_scheduled_feed_proceeds_when_not_recently_fed(self):
        fake = _FakeActivities(
            check_recent_feed=[RecentFeedCheck(fed_recently=False)],
            trigger_feed=[{"status": "ok"}],
            verify_feed=[VERIFIED],
        )
        result = await _run(
            FeedInput(device_id=100, amount=10, feed_type="scheduled"), fake
        )
        assert result.status == "success"
        assert result.feed_type == "scheduled"
        assert fake.names() == ["check_recent_feed", "trigger_feed", "verify_feed", "refresh_feeder_snapshots"]

    @pytest.mark.asyncio
    async def test_unconfirmed_feed_retries_trigger_once_then_succeeds(self):
        fake = _FakeActivities(
            trigger_feed=[{"status": "ok"}, {"status": "ok"}],
            verify_feed=[_not_yet_recorded(), VERIFIED],
        )
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "success"
        assert result.attempts == 2
        assert fake.names() == ["trigger_feed", "verify_feed", "trigger_feed", "verify_feed", "refresh_feeder_snapshots"]

    @pytest.mark.asyncio
    async def test_gives_up_and_alerts_after_max_attempts(self):
        fake = _FakeActivities(
            trigger_feed=[{"status": "ok"}] * MAX_FEED_ATTEMPTS,
            verify_feed=[_not_yet_recorded()] * MAX_FEED_ATTEMPTS,
            alert_feed_failure=[ALERT],
        )
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "failure"
        assert result.attempts == MAX_FEED_ATTEMPTS
        assert result.message == "Feed not yet recorded by device"
        assert fake.names()[-2:] == ["alert_feed_failure", "refresh_feeder_snapshots"]
        assert fake.names().count("trigger_feed") == MAX_FEED_ATTEMPTS

    @pytest.mark.asyncio
    async def test_trigger_timeout_still_verifies_before_retrying(self):
        """A timed-out trigger may have dispensed food; the feed log decides."""
        timeout = _activity_error(TimeoutError("timed out", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]))
        fake = _FakeActivities(trigger_feed=[timeout], verify_feed=[VERIFIED])
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "success"
        assert fake.names() == ["trigger_feed", "verify_feed", "refresh_feeder_snapshots"]  # no second trigger

    @pytest.mark.asyncio
    async def test_rejected_trigger_reports_petkit_error(self):
        rejected = _activity_error(ApplicationError("Device is offline", type=FEED_REJECTED))
        fake = _FakeActivities(
            trigger_feed=[rejected, rejected],
            verify_feed=[_not_yet_recorded(), _not_yet_recorded()],
            alert_feed_failure=[ALERT],
        )
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "failure"
        assert result.message == "Device is offline"
        alert_call = next(c for c in fake.calls if c[0] == "alert_feed_failure")
        assert alert_call[1].reason == "Device is offline"

    @pytest.mark.asyncio
    async def test_feeder_not_found_alerts_without_verifying(self):
        missing = _activity_error(ApplicationError("Feeder 100 not found", type=FEEDER_NOT_FOUND, non_retryable=True))
        fake = _FakeActivities(trigger_feed=[missing], alert_feed_failure=[ALERT])
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "failure"
        assert result.attempts == 1
        assert fake.names() == ["trigger_feed", "alert_feed_failure", "refresh_feeder_snapshots"]

    @pytest.mark.asyncio
    async def test_verify_uses_attempt_start_time_as_threshold(self):
        fake = _FakeActivities(trigger_feed=[{"status": "ok"}], verify_feed=[VERIFIED])
        await _run(FeedInput(device_id=100, amount=5), fake)
        verify_input = fake.calls[1][1]
        assert verify_input.not_before == "2025-01-15T15:00:00+00:00"


# ---------------------------------------------------------------------------
# Activity: send_device_command
# ---------------------------------------------------------------------------


class TestSendDeviceCommandActivity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "command,expected",
        [
            ("update_settings", "update_setting"),
            ("reset_desiccant", "desiccant_reset"),
            ("food_replenished", "food_replenished"),
        ],
    )
    async def test_maps_command_to_petkit(self, command, expected):
        fake_client = AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            result = await send_device_command(
                DeviceCommandInput(device_id=100, command=command, payload={"x": 1})
            )
        assert result["status"] == "ok"
        device_id, petkit_command, payload = fake_client.send_api_request.call_args[0]
        assert device_id == 100
        assert str(petkit_command) == expected
        assert payload == {"x": 1}

    @pytest.mark.asyncio
    async def test_unknown_command_is_non_retryable(self):
        with pytest.raises(ApplicationError) as exc_info:
            await send_device_command(DeviceCommandInput(device_id=100, command="self_destruct"))
        assert exc_info.value.type == UNKNOWN_COMMAND
        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_unknown_feeder_is_non_retryable(self):
        fake_client = AsyncMock()
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={}),
            patch(f"{ACT}.refresh_data", return_value=fake_client),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await send_device_command(DeviceCommandInput(device_id=999, command="reset_desiccant"))
        assert exc_info.value.type == FEEDER_NOT_FOUND
        fake_client.send_api_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_petkit_error_wrapped_as_retryable_command_rejected(self):
        from pypetkitapi.exceptions import PypetkitError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = PypetkitError("Device is offline")
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await send_device_command(DeviceCommandInput(device_id=100, command="reset_desiccant"))
        assert exc_info.value.type == COMMAND_REJECTED
        assert exc_info.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_expired_session_reauthenticates_and_resends_once(self):
        from pypetkitapi.exceptions import PetkitSessionExpiredError

        fake_client = AsyncMock()
        fake_client.send_api_request.side_effect = [PetkitSessionExpiredError("expired"), None]
        with (
            patch(f"{ACT}.get_client", return_value=fake_client),
            patch(f"{ACT}.get_feeders", return_value={100: _make_feeder()}),
            patch(f"{ACT}.reset_client", new_callable=AsyncMock) as mock_reset,
        ):
            await send_device_command(DeviceCommandInput(device_id=100, command="food_replenished"))
        mock_reset.assert_awaited_once()
        assert fake_client.send_api_request.await_count == 2


# ---------------------------------------------------------------------------
# DeviceCommandWorkflow
# ---------------------------------------------------------------------------


class TestDeviceCommandWorkflow:
    @pytest.mark.asyncio
    async def test_runs_activity_with_retrying_policy(self):
        fake = _FakeActivities(send_device_command=[{"status": "ok"}])
        inp = DeviceCommandInput(device_id=100, command="update_settings", payload={"volume": 3})

        async def execute_activity(fn, arg, **kwargs):
            return await fake(fn, arg, **kwargs)

        with patch.object(workflow, "execute_activity", side_effect=execute_activity):
            result = await DeviceCommandWorkflow().run(inp)

        assert result.status == "ok"
        assert result.command == "update_settings"
        assert fake.calls[0][1] is inp
        assert fake.calls[0][2]["retry_policy"] is DEVICE_COMMAND_RETRY_POLICY

    def test_policy_never_retries_permanent_errors(self):
        assert DEVICE_COMMAND_RETRY_POLICY.maximum_attempts > 1
        assert FEEDER_NOT_FOUND in DEVICE_COMMAND_RETRY_POLICY.non_retryable_error_types
        assert UNKNOWN_COMMAND in DEVICE_COMMAND_RETRY_POLICY.non_retryable_error_types


class TestSnapshotRefreshInWorkflows:
    @pytest.mark.asyncio
    async def test_skipped_feed_does_not_refresh(self):
        fake = _FakeActivities(check_recent_feed=[RecentFeedCheck(fed_recently=True, last_feed_at="t")])
        await _run(FeedInput(device_id=100, amount=10, feed_type="scheduled"), fake)
        assert "refresh_feeder_snapshots" not in fake.names()

    @pytest.mark.asyncio
    async def test_refresh_failure_does_not_fail_the_feed(self):
        broken = _activity_error(ApplicationError("Firestore unavailable"))
        fake = _FakeActivities(
            trigger_feed=[{"status": "ok"}],
            verify_feed=[VERIFIED],
            refresh_feeder_snapshots=[broken],
        )
        result = await _run(FeedInput(device_id=100, amount=5), fake)
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_failed_device_command_does_not_refresh(self):
        fake = _FakeActivities(send_device_command=[_activity_error(ApplicationError("offline"))])

        async def execute_activity(fn, arg, **kwargs):
            return await fake(fn, arg, **kwargs)

        with patch.object(workflow, "execute_activity", side_effect=execute_activity):
            with pytest.raises(ActivityError):
                await DeviceCommandWorkflow().run(DeviceCommandInput(device_id=100, command="reset_desiccant"))
        assert fake.names() == ["send_device_command"]

    @pytest.mark.asyncio
    async def test_refresh_workflow_refreshes_requested_device(self):
        fake = _FakeActivities()

        async def execute_activity(fn, arg, **kwargs):
            return await fake(fn, arg, **kwargs)

        with patch.object(workflow, "execute_activity", side_effect=execute_activity):
            result = await RefreshFeedersWorkflow().run(RefreshSnapshotsInput(device_id=None))
        assert fake.names() == ["refresh_feeder_snapshots"]
        assert fake.calls[0][1].device_id is None
        assert result.device_ids == [None]


# ---------------------------------------------------------------------------
# Activity: refresh_feeder_snapshots
# ---------------------------------------------------------------------------


class TestRefreshFeederSnapshotsActivity:
    def _feeder(self, device_id, device_type):
        from tests.test_router import _make_feeder as full_feeder

        f = full_feeder(device_id=device_id)
        f.device_nfo.device_type = device_type
        return f

    @pytest.mark.asyncio
    async def test_writes_snapshot_for_every_feeder(self):
        now = datetime(2025, 1, 15, 8, 30, tzinfo=timezone.utc)
        feeders = {1: self._feeder(1, "d4"), 2: self._feeder(2, "d3")}
        with (
            patch(f"{ACT}.refresh_data", return_value=AsyncMock()),
            patch(f"{ACT}.get_feeders", return_value=feeders),
            patch("backend.client.fetch_d4_feed_events", return_value=_events_at(now)),
            patch("backend.snapshots.write_feeder_snapshot", new_callable=AsyncMock) as write,
        ):
            result = await refresh_feeder_snapshots(RefreshSnapshotsInput())

        assert result.device_ids == [1, 2]
        assert write.await_count == 2
        d4_feeder, d4_records = write.await_args_list[0].args
        assert d4_feeder["id"] == 1
        assert d4_records["feed"] == [{"date": "2025-01-15", "time": "08:30", "amount": 10}]
        d3_feeder, d3_records = write.await_args_list[1].args
        assert d3_feeder["id"] == 2
        assert d3_records == {"eat": [], "feed": [], "move": [], "pet": []}

    @pytest.mark.asyncio
    async def test_single_device(self):
        feeders = {1: self._feeder(1, "d3"), 2: self._feeder(2, "d3")}
        with (
            patch(f"{ACT}.refresh_data", return_value=AsyncMock()),
            patch(f"{ACT}.get_feeders", return_value=feeders),
            patch("backend.snapshots.write_feeder_snapshot", new_callable=AsyncMock) as write,
        ):
            result = await refresh_feeder_snapshots(RefreshSnapshotsInput(device_id=2))
        assert result.device_ids == [2]
        assert write.await_args.args[0]["id"] == 2

    @pytest.mark.asyncio
    async def test_unknown_device_is_non_retryable(self):
        with (
            patch(f"{ACT}.refresh_data", return_value=AsyncMock()),
            patch(f"{ACT}.get_feeders", return_value={}),
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await refresh_feeder_snapshots(RefreshSnapshotsInput(device_id=9))
        assert exc_info.value.type == FEEDER_NOT_FOUND
