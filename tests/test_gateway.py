"""Tests for gateway module."""

import pytest
from pyclaw.gateway import GatewayServer, GatewayConfig
from pyclaw.gateway.runtime import GatewayRuntimeState


class TestGatewayConfig:
    """Test GatewayConfig class."""

    def test_default_config(self):
        """Test default configuration."""
        config = GatewayConfig()
        assert config.port == 12321
        assert config.host == "127.0.0.1"
        assert config.control_ui_enabled is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = GatewayConfig(
            port=8080,
            host="0.0.0.0",
            control_ui_enabled=False,
            provider="tencent",
            model="hunyuan-lite"
        )
        assert config.port == 8080
        assert config.host == "0.0.0.0"
        assert config.control_ui_enabled is False
        assert config.provider == "tencent"
        assert config.model == "hunyuan-lite"


class TestGatewayServer:
    """Test GatewayServer class."""

    def test_server_initialization(self):
        """Test GatewayServer initialization."""
        config = GatewayConfig()
        server = GatewayServer(config)
        assert server.config == config
        assert server.app is not None
        assert server.runtime is not None

    def test_register_handler(self):
        """Test register_handler method."""
        config = GatewayConfig()
        server = GatewayServer(config)

        async def test_handler(params, context):
            return "test result"

        server.register_handler("test.method", test_handler)
        assert "test.method" in server.handlers

    @pytest.mark.asyncio
    async def test_handle_rpc(self):
        """Test handle_rpc method."""
        config = GatewayConfig()
        server = GatewayServer(config)

        async def test_handler(params, context):
            return params.get("test_param", "default")

        server.register_handler("test.method", test_handler)
        result = await server._handle_rpc("test.method", {"test_param": "test_value"})
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_handle_rpc_unknown_method(self):
        """Test handle_rpc method with unknown method."""
        config = GatewayConfig()
        server = GatewayServer(config)

        with pytest.raises(ValueError):
            await server._handle_rpc("unknown.method", {})


class TestGatewayRuntimeState:
    """Test GatewayRuntimeState class."""

    def test_initialization(self):
        """Test GatewayRuntimeState initialization."""
        runtime = GatewayRuntimeState()
        assert runtime.sessions == {}
        assert runtime.clients == {}

    def test_client_connected(self):
        """Test client_connected method."""
        runtime = GatewayRuntimeState()
        client_id = "test_client"
        runtime.client_connected(client_id)
        assert client_id in runtime.clients

    def test_client_disconnected(self):
        """Test client_disconnected method."""
        runtime = GatewayRuntimeState()
        client_id = "test_client"
        runtime.client_connected(client_id)
        assert client_id in runtime.clients
        runtime.client_disconnected(client_id)
        assert client_id not in runtime.clients

    def test_get_or_create_session(self):
        """Test get_or_create_session method."""
        runtime = GatewayRuntimeState()
        session_id = "test_session"
        session = runtime.get_or_create_session(session_id, "default")
        assert session_id in runtime.sessions
        assert session is not None

    def test_get_channel_status(self):
        """Test get_channel_status method."""
        runtime = GatewayRuntimeState()
        status = runtime.get_channel_status()
        assert isinstance(status, dict)

    def test_get_agent_status(self):
        """Test get_agent_status method."""
        runtime = GatewayRuntimeState()
        status = runtime.get_agent_status()
        assert isinstance(status, dict)
