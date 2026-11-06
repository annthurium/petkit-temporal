"""Device snapshots in Firestore.

The worker writes one document per feeder (collection ``feeders``, document
ID = device ID) after every feed, device command, and refresh. The API reads
them, so it never needs a PetKit session (ADR 0006).
"""

from datetime import datetime, timezone

from google.cloud import firestore

from backend.config import FIRESTORE_COLLECTION, FIRESTORE_DATABASE, GOOGLE_CLOUD_PROJECT
from backend.serializers import firestore_safe

_db: firestore.AsyncClient | None = None


def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=GOOGLE_CLOUD_PROJECT, database=FIRESTORE_DATABASE)
    return _db


def _collection():
    return get_db().collection(FIRESTORE_COLLECTION)


async def write_feeder_snapshot(feeder: dict, records: dict) -> None:
    """Replace the feeder's snapshot with fresh data from PetKit."""
    doc = firestore_safe({
        **feeder,
        "records": records,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await _collection().document(str(feeder["id"])).set(doc)


async def get_feeder_snapshot(device_id: int) -> dict | None:
    snap = await _collection().document(str(device_id)).get()
    return snap.to_dict() if snap.exists else None


async def list_feeder_snapshots() -> list[dict]:
    return [doc.to_dict() async for doc in _collection().stream()]
