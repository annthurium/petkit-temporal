from temporalio.client import Client

from backend import config

_client: Client | None = None


def _plugins() -> list:
    if not config.TEMPORAL_CLOUD_RUN_IDENTITY:
        return []
    from temporalio.contrib.gcp.cloud_run.id import CloudRunIdPlugin

    # Unique worker identity per Cloud Run instance, from instance metadata.
    return [CloudRunIdPlugin()]


async def connect_temporal_client() -> Client:
    """Open a new Temporal client from config.

    Local dev server by default; Temporal Cloud when TEMPORAL_API_KEY is set
    (API-key auth requires TLS).
    """
    return await Client.connect(
        config.TEMPORAL_ADDRESS,
        namespace=config.TEMPORAL_NAMESPACE,
        api_key=config.TEMPORAL_API_KEY,
        tls=config.TEMPORAL_API_KEY is not None,
        plugins=_plugins(),
    )


async def get_temporal_client() -> Client:
    """Get or create a Temporal client singleton."""
    global _client
    if _client is None:
        _client = await connect_temporal_client()
    return _client
