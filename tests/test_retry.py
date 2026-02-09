from unittest.mock import AsyncMock

import pytest
from tenacity import wait_none

from backend.client import send_api_request_with_retry
from backend.config import NUM_RETRIES


@pytest.fixture(autouse=True)
def _disable_backoff():
    """Replace exponential backoff with no-wait so tests run instantly."""
    original_wait = send_api_request_with_retry.retry.wait
    send_api_request_with_retry.retry.wait = wait_none()
    yield
    send_api_request_with_retry.retry.wait = original_wait


class TestSendApiRequestWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        client = AsyncMock()
        await send_api_request_with_retry(client, 1, "FEED", {"amount": 5})
        assert client.send_api_request.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        client = AsyncMock()
        client.send_api_request = AsyncMock(
            side_effect=[ConnectionError(), ConnectionError(), None]
        )
        await send_api_request_with_retry(client, 1, "FEED", {"amount": 5})
        assert client.send_api_request.await_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        client = AsyncMock()
        client.send_api_request = AsyncMock(side_effect=ConnectionError("timeout"))
        with pytest.raises(ConnectionError):
            await send_api_request_with_retry(client, 1, "FEED", {"amount": 5})
        assert client.send_api_request.await_count == NUM_RETRIES

    @pytest.mark.asyncio
    async def test_passes_args_through(self):
        client = AsyncMock()
        await send_api_request_with_retry(client, 42, "CANCEL", {"key": "val"})
        client.send_api_request.assert_awaited_once_with(42, "CANCEL", {"key": "val"})
