from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend import snapshots
from backend.serializers import firestore_safe


class TestFirestoreSafe:
    def test_basic_types_unchanged(self):
        value = {"a": 1, "b": "x", "c": None, "d": True, "e": 1.5, "f": [1, 2]}
        assert firestore_safe(value) == value

    def test_nested_arrays_become_json_strings(self):
        # Firestore rejects an array directly inside an array.
        assert firestore_safe({"light_multi_range": [[0, 1440], [60, 120]]}) == {
            "light_multi_range": ["[0, 1440]", "[60, 120]"]
        }

    def test_arrays_inside_maps_inside_arrays_are_fine(self):
        assert firestore_safe([{"x": [1, 2]}]) == [{"x": [1, 2]}]

    def test_unknown_objects_become_strings(self):
        class Thing:
            def __str__(self):
                return "thing"

        assert firestore_safe({"t": Thing(), 5: "int key"}) == {"t": "thing", "5": "int key"}


class TestSnapshotStore:
    @pytest.fixture
    def collection(self):
        coll = MagicMock()
        db = MagicMock()
        db.collection.return_value = coll
        with patch("backend.snapshots.get_db", return_value=db):
            yield coll

    @pytest.mark.asyncio
    async def test_write_uses_device_id_as_document_id(self, collection):
        doc = collection.document.return_value
        doc.set = AsyncMock()

        await snapshots.write_feeder_snapshot(
            {"id": 42, "name": "Feeder", "settings": {"light_multi_range": [[0, 1]]}},
            {"eat": [], "feed": [], "move": [], "pet": []},
        )

        collection.document.assert_called_once_with("42")
        written = doc.set.await_args.args[0]
        assert written["name"] == "Feeder"
        assert written["records"]["feed"] == []
        assert written["settings"]["light_multi_range"] == ["[0, 1]"]
        assert "updated_at" in written

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, collection):
        snap = MagicMock(exists=False)
        collection.document.return_value.get = AsyncMock(return_value=snap)
        assert await snapshots.get_feeder_snapshot(1) is None

    @pytest.mark.asyncio
    async def test_get_existing(self, collection):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"id": 1}
        collection.document.return_value.get = AsyncMock(return_value=snap)
        assert await snapshots.get_feeder_snapshot(1) == {"id": 1}

    @pytest.mark.asyncio
    async def test_list(self, collection):
        docs = [MagicMock(), MagicMock()]
        docs[0].to_dict.return_value = {"id": 1}
        docs[1].to_dict.return_value = {"id": 2}

        async def stream():
            for d in docs:
                yield d

        collection.stream = stream
        assert await snapshots.list_feeder_snapshots() == [{"id": 1}, {"id": 2}]


class TestFirestoreRules:
    """ADR 0007: browsers never read Firestore, so the rules must deny everything."""

    def test_rules_deny_all_client_access(self):
        import re
        from pathlib import Path

        rules = Path("firestore.rules").read_text()
        code = re.sub(r"//.*", "", rules)  # ignore comments
        allows = re.findall(r"allow\s+[^;]+;", code)
        assert allows, "expected an explicit allow statement"
        assert all(re.search(r"if\s+false\s*;", a) for a in allows), allows
