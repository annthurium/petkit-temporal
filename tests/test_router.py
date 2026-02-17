from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError

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


def _make_rpc_error(message="not found"):
    """Create a mock RPCError for testing."""
    return RPCError(MagicMock(), MagicMock(), message)


@pytest.fixture
def fake_client():
    client = AsyncMock()
    client.petkit_entities = FAKE_FEEDERS
    return client


@pytest.fixture
def mock_temporal_client():
    """Create a mock Temporal client for testing.

    By default, workflow handle operations raise RPCError (no workflow running).
    get_workflow_handle is sync in the real SDK, so we use MagicMock for it.
    """
    tc = MagicMock()
    handle = MagicMock()
    handle.signal = AsyncMock(side_effect=_make_rpc_error())
    handle.query = AsyncMock(side_effect=_make_rpc_error())
    handle.describe = AsyncMock(side_effect=_make_rpc_error())
    handle.cancel = AsyncMock(side_effect=_make_rpc_error())
    tc.get_workflow_handle.return_value = handle
    tc.start_workflow = AsyncMock()
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
    async def test_dispatches_feed_direct(self, client, fake_client):
        """Falls back to direct API call when no workflow is running."""
        resp = await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["via"] == "direct"
        fake_client.send_api_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_feed_via_workflow(self, client, mock_temporal_client):
        """Signals workflow when one is running."""
        handle = mock_temporal_client.get_workflow_handle.return_value
        handle.signal = AsyncMock()  # No error = workflow is running

        resp = await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["via"] == "workflow"
        handle.signal.assert_awaited_once()

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

class TestScheduleCRUD:
    @pytest.mark.asyncio
    async def test_get_schedule_returns_none_when_no_workflow(self, client):
        resp = await client.get("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json() is None

    @pytest.mark.asyncio
    async def test_put_creates_schedule(self, client, mock_temporal_client):
        resp = await client.put("/api/feeders/1/schedule", json={"time": "09:00", "amount": 20})
        assert resp.status_code == 200
        body = resp.json()
        assert body["time"] == "09:00"
        assert body["amount"] == 20
        assert body["skip_next"] is False
        assert body["workflow_id"] == "scheduled-feeding-1"
        mock_temporal_client.start_workflow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_terminates_existing_workflow(self, client, mock_temporal_client):
        """When a workflow is already running, terminate it before starting a new one."""
        handle = mock_temporal_client.get_workflow_handle.return_value
        desc = MagicMock()
        desc.status = WorkflowExecutionStatus.RUNNING
        handle.describe = AsyncMock(return_value=desc)
        handle.terminate = AsyncMock()

        resp = await client.put("/api/feeders/1/schedule", json={"time": "08:00", "amount": 15})
        assert resp.status_code == 200
        handle.terminate.assert_awaited_once()
        mock_temporal_client.start_workflow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_schedule(self, client):
        resp = await client.delete("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_schedule_404_for_unknown_device(self, client):
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
