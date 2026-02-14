from temporalio.client import Client

from backend.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE

_client: Client | None = None


async def get_temporal_client() -> Client:
    """Get or create a Temporal client singleton."""
    global _client
    if _client is None:
        _client = await Client.connect(
            TEMPORAL_HOST,
            namespace=TEMPORAL_NAMESPACE,
        )
    return _client
