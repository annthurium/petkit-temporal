from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.main import app


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_feeder(device_id: int = 1, name: str = "TestFeeder"):
    """Build a fake Feeder-like object with the attributes the serializer reads."""
    state = SimpleNamespace(
        battery_power=100,
        battery_status=1,
        food=1,
        food1=None,
        food2=None,
        eating=0,
        feeding=0,
        desiccant_left_days=20,
        error_code=0,
        error_msg=None,
        weight=None,
        pim=None,
        wifi=1,
        feed_state=SimpleNamespace(
            eat_count=3,
            eat_avg=10,
            add_amount_total=30,
            plan_amount_total=30,
            real_amount_total=28,
        ),
    )
    settings = SimpleNamespace(
        light_mode=1,
        light_multi_range=None,
        volume=5,
        feed_sound=1,
        system_sound_enable=1,
        tone_mode=None,
        eat_notify=1,
        feed_notify=1,
        food_notify=1,
        food_warn=1,
        food_warn_range=None,
        eat_detection=1,
        eat_sensitivity=None,
        move_detection=None,
        pet_detection=None,
        surplus_control=None,
        surplus_standard=None,
        camera=None,
    )
    device_nfo = SimpleNamespace(device_type="d3")
    manual_feed = SimpleNamespace(amount=5, amount1=None, amount2=None, time=None, status=None)

    return SimpleNamespace(
        id=device_id,
        name=name,
        firmware="1.0.0",
        state=state,
        settings=settings,
        device_nfo=device_nfo,
        manual_feed=manual_feed,
        device_records=None,
    )


FAKE_FEEDER = _make_feeder(device_id=1)
FAKE_FEEDERS = {1: FAKE_FEEDER}


def _mock_get_feeders(_client):
    return FAKE_FEEDERS


@pytest.fixture
def fake_client():
    client = AsyncMock()
    client.petkit_entities = FAKE_FEEDERS
    return client


@pytest.fixture
def mock_temporal_client():
    """Mock Temporal client. start_workflow returns a handle with the requested ID."""
    tc = MagicMock()

    async def start_workflow(*args, **kwargs):
        return SimpleNamespace(id=kwargs["id"])

    tc.start_workflow = AsyncMock(side_effect=start_workflow)
    return tc


@pytest_asyncio.fixture
async def client(fake_client, mock_temporal_client):
    with (
        patch("backend.routers.feeders.get_client", return_value=fake_client),
        patch("backend.routers.feeders.refresh_data", return_value=fake_client),
        patch("backend.routers.feeders.get_feeders", side_effect=_mock_get_feeders),
        patch("backend.routers.feeders.get_temporal_client", return_value=mock_temporal_client),
        patch("backend.main.run_temporal_worker_with_retry", new_callable=AsyncMock),
        patch("backend.client.shutdown", new_callable=AsyncMock),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# GET /api/feeders
# ---------------------------------------------------------------------------

class TestListFeeders:
    @pytest.mark.asyncio
    async def test_returns_list(self, client):
        resp = await client.get("/api/feeders")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["name"] == "TestFeeder"


# ---------------------------------------------------------------------------
# GET /api/feeders/{device_id}
# ---------------------------------------------------------------------------

class TestGetFeeder:
    @pytest.mark.asyncio
    async def test_returns_feeder(self, client):
        resp = await client.get("/api/feeders/1")
        assert resp.status_code == 200
        assert resp.json()["id"] == 1

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.get("/api/feeders/999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeders/{device_id}/feed
# ---------------------------------------------------------------------------

class TestManualFeed:
    @pytest.mark.asyncio
    async def test_starts_feed_workflow(self, client, mock_temporal_client):
        """Manual feeds start a FeedWorkflow, whether or not a schedule exists."""
        from temporalio.common import WorkflowIDConflictPolicy

        resp = await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "ok", "via": "workflow", "workflow_id": "feed-1-manual"}

        mock_temporal_client.start_workflow.assert_awaited_once()
        args, kwargs = mock_temporal_client.start_workflow.call_args
        feed_input = args[1]
        assert feed_input.device_id == 1
        assert feed_input.amount == 5
        assert feed_input.feed_type == "manual"
        assert kwargs["id"] == "feed-1-manual"
        assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.FAIL

    @pytest.mark.asyncio
    async def test_409_when_feed_already_running(self, client, mock_temporal_client):
        from temporalio.exceptions import WorkflowAlreadyStartedError

        mock_temporal_client.start_workflow.side_effect = WorkflowAlreadyStartedError(
            "feed-1-manual", "FeedWorkflow"
        )
        resp = await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_400_when_no_amount(self, client):
        resp = await client.post("/api/feeders/1/feed", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.post("/api/feeders/999/feed", json={"amount": 5})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Schedule CRUD: GET / PUT / DELETE /api/feeders/{device_id}/schedule
# ---------------------------------------------------------------------------

SCHEDULE = {
    "time": "09:00",
    "amount": 20,
    "timezone": "America/Los_Angeles",
    "next_run": "2025-01-16T17:00:00+00:00",
    "paused": False,
    "skip_window_hours": 12.0,
}


@pytest.fixture
def mock_schedules():
    with (
        patch("backend.temporal.schedules.describe_schedule", new_callable=AsyncMock, return_value=None) as describe,
        patch("backend.temporal.schedules.upsert_schedule", new_callable=AsyncMock) as upsert,
        patch("backend.temporal.schedules.delete_schedule", new_callable=AsyncMock) as delete,
        patch("backend.temporal.schedules.latest_feed_result", new_callable=AsyncMock, return_value=None) as latest,
    ):
        yield SimpleNamespace(describe=describe, upsert=upsert, delete=delete, latest=latest)


class TestScheduleCRUD:
    @pytest.mark.asyncio
    async def test_get_returns_none_without_schedule(self, client, mock_schedules):
        resp = await client.get("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json() is None

    @pytest.mark.asyncio
    async def test_get_includes_latest_result_and_alert(self, client, mock_schedules):
        mock_schedules.describe.return_value = SCHEDULE
        mock_schedules.latest.return_value = {
            "status": "failure",
            "feed_type": "scheduled",
            "message": "Device is offline",
            "timestamp": "2025-01-15T17:01:00+00:00",
            "attempts": 2,
        }
        resp = await client.get("/api/feeders/1/schedule")
        body = resp.json()
        assert body["time"] == "09:00"
        assert body["next_run"] == SCHEDULE["next_run"]
        assert body["last_feed_result"]["status"] == "failure"
        assert body["last_alert"] == {
            "device_id": 1,
            "reason": "Device is offline",
            "timestamp": "2025-01-15T17:01:00+00:00",
        }

    @pytest.mark.asyncio
    async def test_successful_result_has_no_alert(self, client, mock_schedules):
        mock_schedules.describe.return_value = SCHEDULE
        mock_schedules.latest.return_value = {
            "status": "success", "feed_type": "manual", "message": "ok",
            "timestamp": "2025-01-15T17:01:00+00:00", "attempts": 1,
        }
        body = (await client.get("/api/feeders/1/schedule")).json()
        assert body["last_alert"] is None

    @pytest.mark.asyncio
    async def test_put_upserts_schedule(self, client, mock_schedules, mock_temporal_client):
        mock_schedules.describe.return_value = SCHEDULE
        resp = await client.put("/api/feeders/1/schedule", json={"time": "09:00", "amount": 20})
        assert resp.status_code == 200
        assert resp.json()["time"] == "09:00"
        mock_schedules.upsert.assert_awaited_once_with(mock_temporal_client, 1, 9, 0, 20)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("time", ["9:00", "24:00", "12:60", "noon"])
    async def test_put_rejects_bad_time(self, client, mock_schedules, time):
        resp = await client.put("/api/feeders/1/schedule", json={"time": time, "amount": 20})
        assert resp.status_code == 422
        mock_schedules.upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_schedule(self, client, mock_schedules, mock_temporal_client):
        resp = await client.delete("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        mock_schedules.delete.assert_awaited_once_with(mock_temporal_client, 1)

    @pytest.mark.asyncio
    async def test_schedule_404_for_unknown_device(self, client, mock_schedules):
        resp = await client.get("/api/feeders/999/schedule")
        assert resp.status_code == 404

        resp = await client.put("/api/feeders/999/schedule", json={"time": "09:00", "amount": 20})
        assert resp.status_code == 404

        resp = await client.delete("/api/feeders/999/schedule")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeders/{device_id}/refresh
# ---------------------------------------------------------------------------

class TestRefreshFeeder:
    @pytest.mark.asyncio
    async def test_returns_refreshed_feeder(self, client):
        resp = await client.post("/api/feeders/1/refresh")
        assert resp.status_code == 200
        assert resp.json()["id"] == 1

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.post("/api/feeders/999/refresh")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeders/{device_id}/settings
# ---------------------------------------------------------------------------

class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_updates_settings(self, client, fake_client):
        resp = await client.post("/api/feeders/1/settings", json={"settings": {"lightMode": 1}})
        assert resp.status_code == 200
        fake_client.send_api_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.post("/api/feeders/999/settings", json={"settings": {"lightMode": 1}})
        assert resp.status_code == 404
