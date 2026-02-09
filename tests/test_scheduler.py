import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from backend import scheduler


@pytest.fixture(autouse=True)
def _isolate_schedules(tmp_path, monkeypatch):
    """Redirect SCHEDULES_FILE to a temp directory for every test."""
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")


@pytest.fixture(autouse=True)
def _clear_dispatched():
    """Reset the module-level _dispatched_this_minute set between tests."""
    scheduler._dispatched_this_minute.clear()
    yield
    scheduler._dispatched_this_minute.clear()


# ---------------------------------------------------------------------------
# load_schedules / save_schedules
# ---------------------------------------------------------------------------

class TestLoadSchedules:
    def test_returns_empty_dict_when_file_missing(self):
        assert scheduler.load_schedules() == {}

    def test_returns_parsed_json(self, tmp_path, monkeypatch):
        data = {"123": {"time": "08:00", "amount": 10, "skip_next": False}}
        (tmp_path / "schedules.json").write_text(json.dumps(data))
        monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
        assert scheduler.load_schedules() == data


class TestSaveSchedules:
    def test_writes_json_to_disk(self):
        data = {"456": {"time": "12:00", "amount": 5, "skip_next": True}}
        scheduler.save_schedules(data)
        assert scheduler.load_schedules() == data


# ---------------------------------------------------------------------------
# mark_skip
# ---------------------------------------------------------------------------

class TestMarkSkip:
    def test_sets_skip_next_on_existing_schedule(self):
        scheduler.save_schedules({"1": {"time": "08:00", "amount": 10, "skip_next": False}})
        scheduler.mark_skip(1)
        assert scheduler.load_schedules()["1"]["skip_next"] is True

    def test_noop_for_missing_device(self):
        scheduler.save_schedules({"1": {"time": "08:00", "amount": 10, "skip_next": False}})
        scheduler.mark_skip(999)
        assert scheduler.load_schedules()["1"]["skip_next"] is False


# ---------------------------------------------------------------------------
# _schedule_loop
# ---------------------------------------------------------------------------

def _make_fake_now(time_str: str):
    """Return a datetime whose strftime('%H:%M') equals *time_str*."""
    dt = datetime(2025, 1, 15, int(time_str[:2]), int(time_str[3:]), tzinfo=None)

    class _TZ:
        key = "America/Los_Angeles"

    # We patch datetime.now to ignore the tz arg and return our fixed value.
    return dt


class TestScheduleLoop:
    @pytest.mark.asyncio
    async def test_dispatches_feed_when_time_matches(self):
        scheduler.save_schedules({"100": {"time": "07:30", "amount": 15, "skip_next": False}})

        fake_client = AsyncMock()
        fake_now = _make_fake_now("07:30")

        with (
            patch("backend.scheduler.get_client", return_value=fake_client),
            patch("backend.scheduler.refresh_data", new_callable=AsyncMock),
            patch("backend.scheduler.datetime") as mock_dt,
            patch("asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            mock_dt.now.return_value = fake_now

            with pytest.raises(asyncio.CancelledError):
                await scheduler._schedule_loop()

        fake_client.send_api_request.assert_awaited_once()
        call_args = fake_client.send_api_request.call_args
        assert call_args[0][0] == 100
        assert call_args[0][2] == {"amount": 15}

    @pytest.mark.asyncio
    async def test_skips_when_skip_next_is_set(self):
        scheduler.save_schedules({"100": {"time": "07:30", "amount": 15, "skip_next": True}})

        fake_client = AsyncMock()
        fake_now = _make_fake_now("07:30")

        with (
            patch("backend.scheduler.get_client", return_value=fake_client),
            patch("backend.scheduler.refresh_data", new_callable=AsyncMock),
            patch("backend.scheduler.datetime") as mock_dt,
            patch("asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            mock_dt.now.return_value = fake_now

            with pytest.raises(asyncio.CancelledError):
                await scheduler._schedule_loop()

        fake_client.send_api_request.assert_not_awaited()
        # skip_next should be reset to False
        assert scheduler.load_schedules()["100"]["skip_next"] is False

    @pytest.mark.asyncio
    async def test_does_not_refeed_in_same_minute_window(self):
        scheduler.save_schedules({"100": {"time": "07:30", "amount": 15, "skip_next": False}})
        scheduler._dispatched_this_minute.add(100)

        fake_client = AsyncMock()
        fake_now = _make_fake_now("07:30")

        with (
            patch("backend.scheduler.get_client", return_value=fake_client),
            patch("backend.scheduler.refresh_data", new_callable=AsyncMock),
            patch("backend.scheduler.datetime") as mock_dt,
            patch("asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            mock_dt.now.return_value = fake_now

            with pytest.raises(asyncio.CancelledError):
                await scheduler._schedule_loop()

        fake_client.send_api_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_dispatched_when_time_no_longer_matches(self):
        scheduler.save_schedules({"100": {"time": "07:30", "amount": 15, "skip_next": False}})
        scheduler._dispatched_this_minute.add(100)

        fake_now = _make_fake_now("08:00")  # different from schedule time

        with (
            patch("backend.scheduler.datetime") as mock_dt,
            patch("asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            mock_dt.now.return_value = fake_now

            with pytest.raises(asyncio.CancelledError):
                await scheduler._schedule_loop()

        assert 100 not in scheduler._dispatched_this_minute


# ---------------------------------------------------------------------------
# start_scheduler / stop_scheduler
# ---------------------------------------------------------------------------

class TestStartStopScheduler:
    @pytest.mark.asyncio
    async def test_start_creates_task_and_stop_cancels_it(self):
        with patch("backend.scheduler._schedule_loop", new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = None
            scheduler.start_scheduler()
            assert scheduler._task is not None

            await scheduler.stop_scheduler()
            assert scheduler._task is None
