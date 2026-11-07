"""Tests for the pieces that differ between local dev and Cloud Run."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.common import VersioningBehavior

from backend import config
from backend.temporal import client as temporal_client_module
from backend.temporal import worker as worker_module


class TestTemporalConnection:
    @pytest.mark.asyncio
    async def test_local_dev_server_without_api_key(self):
        with (
            patch.object(config, "TEMPORAL_ADDRESS", "localhost:7233"),
            patch.object(config, "TEMPORAL_NAMESPACE", "default"),
            patch.object(config, "TEMPORAL_API_KEY", None),
            patch.object(config, "TEMPORAL_CLOUD_RUN_IDENTITY", False),
            patch("backend.temporal.client.Client.connect", new_callable=AsyncMock) as connect,
        ):
            await temporal_client_module.connect_temporal_client()
        args, kwargs = connect.call_args
        assert args[0] == "localhost:7233"
        assert kwargs["namespace"] == "default"
        assert kwargs["api_key"] is None
        assert kwargs["tls"] is False
        assert kwargs["plugins"] == []

    @pytest.mark.asyncio
    async def test_temporal_cloud_with_api_key_uses_tls(self):
        with (
            patch.object(config, "TEMPORAL_ADDRESS", "us-west1.gcp.api.temporal.io:7233"),
            patch.object(config, "TEMPORAL_NAMESPACE", "ns.acct"),
            patch.object(config, "TEMPORAL_API_KEY", "secret"),
            patch.object(config, "TEMPORAL_CLOUD_RUN_IDENTITY", False),
            patch("backend.temporal.client.Client.connect", new_callable=AsyncMock) as connect,
        ):
            await temporal_client_module.connect_temporal_client()
        kwargs = connect.call_args.kwargs
        assert kwargs["api_key"] == "secret"
        assert kwargs["tls"] is True

    def test_cloud_run_identity_plugin_only_when_enabled(self):
        from temporalio.contrib.gcp.cloud_run.id import CloudRunIdPlugin

        with patch.object(config, "TEMPORAL_CLOUD_RUN_IDENTITY", True):
            [plugin] = temporal_client_module._plugins()
        assert isinstance(plugin, CloudRunIdPlugin)
        with patch.object(config, "TEMPORAL_CLOUD_RUN_IDENTITY", False):
            assert temporal_client_module._plugins() == []


class TestConfigParsing:
    def test_temporal_host_still_works_as_address(self, monkeypatch):
        import importlib

        monkeypatch.setenv("ENV_FILE", "/nonexistent")
        monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
        monkeypatch.setenv("TEMPORAL_HOST", "legacy:7233")
        try:
            reloaded = importlib.reload(config)
            assert reloaded.TEMPORAL_ADDRESS == "legacy:7233"
        finally:
            monkeypatch.undo()
            importlib.reload(config)

    @pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("false", False), ("0", False), ("", True)])
    def test_flag_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert config._flag("SOME_FLAG", True) is expected


class TestWorkerVersioning:
    def test_off_by_default(self):
        with (
            patch.object(config, "WORKER_DEPLOYMENT_NAME", None),
            patch.object(config, "WORKER_BUILD_ID", None),
        ):
            assert worker_module.deployment_config() is None

    def test_needs_both_name_and_build_id(self):
        with (
            patch.object(config, "WORKER_DEPLOYMENT_NAME", "petkit"),
            patch.object(config, "WORKER_BUILD_ID", None),
        ):
            assert worker_module.deployment_config() is None

    def test_pinned_when_configured(self):
        with (
            patch.object(config, "WORKER_DEPLOYMENT_NAME", "petkit"),
            patch.object(config, "WORKER_BUILD_ID", "abc123"),
        ):
            cfg = worker_module.deployment_config()
        assert cfg.use_worker_versioning is True
        assert cfg.version.deployment_name == "petkit"
        assert cfg.version.build_id == "abc123"
        assert cfg.default_versioning_behavior == VersioningBehavior.PINNED


async def _client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestApiProcess:
    @pytest.mark.asyncio
    async def test_health(self):
        from backend.main import app

        with (
            patch("backend.main.run_temporal_worker_with_retry", new_callable=AsyncMock),
            patch("backend.main.ensure_refresh_schedule_with_retry", new_callable=AsyncMock),
        ):
            async with await _client_for(app) as http:
                resp = await http.get("/api/health")
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("embedded", [True, False])
    async def test_embedded_worker_flag(self, embedded):
        from backend.main import app, lifespan

        with (
            patch.object(config, "RUN_EMBEDDED_WORKER", embedded),
            patch("backend.main.run_temporal_worker_with_retry", new_callable=AsyncMock) as worker,
            patch("backend.main.ensure_refresh_schedule_with_retry", new_callable=AsyncMock) as ensure,
            patch("backend.client.shutdown", new_callable=AsyncMock),
        ):
            async with lifespan(app):
                await __import__("asyncio").sleep(0)
        ensure.assert_called_once()  # the API always ensures the refresh schedule
        assert worker.called is embedded

    @pytest.mark.asyncio
    async def test_serves_built_frontend_without_shadowing_api(self, tmp_path, monkeypatch):
        import importlib

        (tmp_path / "index.html").write_text("<html>petkit</html>")
        monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path))
        import backend.main as main_module

        try:
            importlib.reload(config)
            reloaded = importlib.reload(main_module)
            with (
                patch("backend.main.run_temporal_worker_with_retry", new_callable=AsyncMock),
                patch("backend.main.ensure_refresh_schedule_with_retry", new_callable=AsyncMock),
            ):
                async with await _client_for(reloaded.app) as http:
                    index = await http.get("/")
                    health = await http.get("/api/health")
            assert "petkit" in index.text
            assert health.json() == {"status": "ok"}
        finally:
            monkeypatch.undo()
            importlib.reload(config)
            importlib.reload(main_module)
