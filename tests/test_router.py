from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend import scheduler
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


@pytest.fixture(autouse=True)
def _isolate_schedules(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")


@pytest.fixture
def fake_client():
    client = AsyncMock()
    client.petkit_entities = FAKE_FEEDERS
    return client


@pytest_asyncio.fixture
async def client(fake_client):
    with (
        patch("backend.routers.feeders.get_client", return_value=fake_client),
        patch("backend.routers.feeders.refresh_data", return_value=fake_client),
        patch("backend.routers.feeders.get_feeders", side_effect=_mock_get_feeders),
        patch("backend.scheduler.start_scheduler"),
        patch("backend.scheduler.stop_scheduler", new_callable=AsyncMock),
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
    async def test_dispatches_feed(self, client, fake_client):
        resp = await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        fake_client.send_api_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_skip_on_schedule(self, client):
        scheduler.save_schedules({"1": {"time": "08:00", "amount": 10, "skip_next": False}})
        await client.post("/api/feeders/1/feed", json={"amount": 5})
        assert scheduler.load_schedules()["1"]["skip_next"] is True

    @pytest.mark.asyncio
    async def test_400_when_no_amount(self, client):
        resp = await client.post("/api/feeders/1/feed", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.post("/api/feeders/999/feed", json={"amount": 5})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeders/{device_id}/feed/cancel
# ---------------------------------------------------------------------------

class TestCancelFeed:
    @pytest.mark.asyncio
    async def test_cancels_feed(self, client, fake_client):
        resp = await client.post("/api/feeders/1/feed/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        fake_client.send_api_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_404_for_unknown(self, client):
        resp = await client.post("/api/feeders/999/feed/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Schedule CRUD: GET / PUT / DELETE /api/feeders/{device_id}/schedule
# ---------------------------------------------------------------------------

class TestScheduleCRUD:
    @pytest.mark.asyncio
    async def test_get_schedule_returns_none_when_empty(self, client):
        resp = await client.get("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json() is None

    @pytest.mark.asyncio
    async def test_put_creates_schedule(self, client):
        resp = await client.put("/api/feeders/1/schedule", json={"time": "09:00", "amount": 20})
        assert resp.status_code == 200
        body = resp.json()
        assert body["time"] == "09:00"
        assert body["amount"] == 20
        assert body["skip_next"] is False

    @pytest.mark.asyncio
    async def test_get_returns_saved_schedule(self, client):
        await client.put("/api/feeders/1/schedule", json={"time": "09:00", "amount": 20})
        resp = await client.get("/api/feeders/1/schedule")
        assert resp.status_code == 200
        assert resp.json()["time"] == "09:00"

    @pytest.mark.asyncio
    async def test_delete_removes_schedule(self, client):
        await client.put("/api/feeders/1/schedule", json={"time": "09:00", "amount": 20})
        resp = await client.delete("/api/feeders/1/schedule")
        assert resp.status_code == 200

        resp = await client.get("/api/feeders/1/schedule")
        assert resp.json() is None

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
