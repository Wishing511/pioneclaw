"""
MCP 工具测试：MCPTool, ListMcpResourcesTool, ReadMcpResourceTool, McpAuthTool
以及 MCPToolRegistry 客户端测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.tools.mcp import (
    ListMcpResourcesTool,
    McpAuthTool,
    MCPTool,
    ReadMcpResourceTool,
)
from app.modules.tools.mcp_client import (
    MCPServerConnection,
    MCPToolRegistry,
    get_mcp_registry,
)

# ── 辅助函数 ──────────────────────────────────────────────────


def make_mock_conn(name="test_server", status="disconnected"):
    """创建模拟的 MCPServerConnection"""
    return MCPServerConnection(
        name=name,
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-test"],
        status=status,
        tools=[
            {"name": "mock_tool_1", "description": "测试工具1"},
            {"name": "mock_tool_2", "description": "测试工具2"},
        ],
        resources=[
            {
                "uri": "file:///test/data.txt",
                "name": "data.txt",
                "description": "测试数据",
            },
        ],
        server_info={"name": "test-server", "version": "1.0.0"},
    )


# ============================================================
# MCPServerConnection 测试
# ============================================================


class TestMCPServerConnection:
    """测试 MCPServerConnection 数据类"""

    def test_default_values(self):
        conn = MCPServerConnection(name="test", transport="stdio")
        assert conn.name == "test"
        assert conn.transport == "stdio"
        assert conn.status == "disconnected"
        assert conn.tools == []
        assert conn.resources == []

    def test_to_dict(self):
        conn = make_mock_conn(status="connected")
        d = conn.to_dict()
        assert d["name"] == "test_server"
        assert d["status"] == "connected"
        assert d["tool_count"] == 2
        assert d["resource_count"] == 1


# ============================================================
# MCPToolRegistry 测试
# ============================================================


class TestMCPToolRegistry:
    """测试 MCPToolRegistry 注册表"""

    @pytest.fixture
    def registry(self):
        reg = MCPToolRegistry()
        reg._servers.clear()
        return reg

    def test_singleton(self):
        r1 = get_mcp_registry()
        r2 = get_mcp_registry()
        assert r1 is r2

    def test_register_server(self, registry):
        conn = registry.register_server("test", transport="stdio", command="npx")
        assert conn.name == "test"
        assert conn.transport == "stdio"
        assert conn.command == "npx"
        assert "test" in registry._servers

    def test_unregister_server(self, registry):
        registry.register_server("test", transport="stdio")
        assert registry.unregister_server("test") is True
        assert registry.unregister_server("nonexistent") is False

    def test_get_server(self, registry):
        registry.register_server("test", transport="stdio")
        conn = registry.get_server("test")
        assert conn is not None
        assert conn.name == "test"
        assert registry.get_server("nonexistent") is None

    def test_list_servers(self, registry):
        registry.register_server("server1", transport="stdio")
        registry.register_server("server2", transport="stdio")
        servers = registry.list_servers()
        assert len(servers) == 2

    def test_list_servers_empty(self, registry):
        assert registry.list_servers() == []


# ============================================================
# McpAuthTool 测试
# ============================================================


class TestMcpAuthTool:
    """测试 McpAuthTool"""

    @pytest.fixture
    def tool(self):
        return McpAuthTool()

    @pytest.mark.asyncio
    async def test_list_all_servers(self, tool):
        """列出所有服务器"""
        conn = make_mock_conn()
        mock_registry = MagicMock()
        mock_registry.list_servers.return_value = [conn]
        mock_registry.get_server.return_value = None

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="")

        assert "test_server" in result
        assert "connected" in result
        assert '"total": 1' in result

    @pytest.mark.asyncio
    async def test_get_single_server(self, tool):
        """查看单个服务器状态"""
        conn = make_mock_conn()
        mock_registry = MagicMock()
        mock_registry.get_server.return_value = conn

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="test_server")

        assert "test_server" in result
        assert "connected" in result

    @pytest.mark.asyncio
    async def test_server_not_found(self, tool):
        """服务器未注册"""
        mock_registry = MagicMock()
        mock_registry.get_server.return_value = None

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="unknown")

        assert '"success": false' in result.lower()


# ============================================================
# ListMcpResourcesTool 测试
# ============================================================


class TestListMcpResourcesTool:
    """测试 ListMcpResourcesTool"""

    @pytest.fixture
    def tool(self):
        return ListMcpResourcesTool()

    @pytest.mark.asyncio
    async def test_list_all_resources(self, tool):
        mock_registry = MagicMock()
        mock_registry.list_resources = AsyncMock(
            return_value={
                "servers": {
                    "server1": [{"uri": "file:///a.txt", "name": "a.txt"}],
                }
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="")

        assert "server1" in result
        assert "a.txt" in result

    @pytest.mark.asyncio
    async def test_list_single_server_resources(self, tool):
        mock_registry = MagicMock()
        mock_registry.list_resources = AsyncMock(
            return_value={
                "server": "test_server",
                "resources": [{"uri": "file:///data.csv", "name": "data.csv"}],
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="test_server")

        assert "test_server" in result
        assert "data.csv" in result


# ============================================================
# ReadMcpResourceTool 测试
# ============================================================


class TestReadMcpResourceTool:
    """测试 ReadMcpResourceTool"""

    @pytest.fixture
    def tool(self):
        return ReadMcpResourceTool()

    @pytest.mark.asyncio
    async def test_read_resource_success(self, tool):
        mock_registry = MagicMock()
        mock_registry.read_resource = AsyncMock(
            return_value={
                "success": True,
                "server": "test_server",
                "uri": "file:///data.txt",
                "contents": [{"uri": "file:///data.txt", "text": "Hello MCP"}],
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="test_server", uri="file:///data.txt")

        assert '"success": true' in result.lower()
        assert "Hello MCP" in result

    @pytest.mark.asyncio
    async def test_read_resource_failure(self, tool):
        mock_registry = MagicMock()
        mock_registry.read_resource = AsyncMock(
            return_value={
                "success": False,
                "error": "资源不存在",
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="bad_server", uri="file:///missing.txt")

        assert '"success": false' in result.lower()


# ============================================================
# MCPTool 测试
# ============================================================


class TestMCPTool:
    """测试 MCPTool"""

    @pytest.fixture
    def tool(self):
        return MCPTool()

    @pytest.mark.asyncio
    async def test_call_tool_success(self, tool):
        mock_registry = MagicMock()
        mock_registry.call_tool = AsyncMock(
            return_value={
                "success": True,
                "server": "test_server",
                "tool": "mock_tool_1",
                "content": [{"type": "text", "text": "工具执行结果"}],
                "isError": False,
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(
                server="test_server",
                tool="mock_tool_1",
                arguments={"key": "value"},
            )

        assert '"success": true' in result.lower()
        assert "工具执行结果" in result
        mock_registry.call_tool.assert_called_once_with(
            "test_server", "mock_tool_1", {"key": "value"}
        )

    @pytest.mark.asyncio
    async def test_call_tool_server_not_found(self, tool):
        mock_registry = MagicMock()
        mock_registry.call_tool = AsyncMock(
            return_value={
                "success": False,
                "error": "MCP 服务器未注册: unknown",
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="unknown", tool="some_tool")

        assert '"success": false' in result.lower()

    @pytest.mark.asyncio
    async def test_call_tool_without_arguments(self, tool):
        mock_registry = MagicMock()
        mock_registry.call_tool = AsyncMock(
            return_value={
                "success": True,
                "content": [],
            }
        )

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="test_server", tool="no_arg_tool")

        assert '"success": true' in result.lower()


# ============================================================
# MCPToolRegistry connect_server 测试
# ============================================================


class TestMCPRegistryConnect:
    """测试 MCPToolRegistry.connect_server()"""

    @pytest.fixture
    def registry(self):
        reg = MCPToolRegistry()
        reg._servers.clear()
        return reg

    @pytest.mark.asyncio
    async def test_connect_unregistered_server(self, registry):
        with pytest.raises(ValueError, match="未注册"):
            await registry.connect_server("nonexistent")

    @pytest.mark.asyncio
    async def test_connect_unsupported_transport(self, registry):
        registry.register_server(
            "bad_server", transport="grpc", url="grpc://localhost:8080"
        )

        result = await registry.connect_server("bad_server")
        assert result.status == "error"
        assert "不支持的传输模式" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_call_tool_not_registered(self, registry):
        result = await registry.call_tool("nonexistent", "tool")
        assert result["success"] is False
        assert "未注册" in str(result["error"])


# ============================================================
# 边界情况测试
# ============================================================


class TestMCPEdgeCases:
    """MCP 工具边界情况"""

    @pytest.mark.asyncio
    async def test_mcp_tool_empty_args(self):
        tool = MCPTool()
        mock_registry = MagicMock()
        mock_registry.call_tool = AsyncMock(return_value={"success": True})

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="s", tool="t")
            assert '"success": true' in result.lower()

    @pytest.mark.asyncio
    async def test_mcp_auth_empty_registry(self):
        tool = McpAuthTool()
        mock_registry = MagicMock()
        mock_registry.list_servers.return_value = []

        with patch(
            "app.modules.tools.mcp_client.get_mcp_registry", return_value=mock_registry
        ):
            result = await tool.execute(server="")

        assert '"total": 0' in result


# ============================================================
# P2: MCP Auth Headers 测试
# ============================================================


class TestMCPAuthHeaders:
    """测试 _build_auth_headers 方法"""

    @pytest.fixture
    def registry(self):
        reg = MCPToolRegistry()
        reg._servers.clear()
        return reg

    def test_build_headers_empty(self, registry):
        conn = MCPServerConnection(
            name="test", transport="sse", url="http://localhost:8080"
        )
        headers = registry._build_auth_headers(conn)
        assert headers == {}

    def test_build_headers_api_key_shorthand(self, registry):
        conn = MCPServerConnection(
            name="test",
            transport="sse",
            url="http://localhost:8080",
            auth_config={"api_key": "sk-12345"},
        )
        headers = registry._build_auth_headers(conn)
        assert headers == {"Authorization": "Bearer sk-12345"}

    def test_build_headers_custom(self, registry):
        conn = MCPServerConnection(
            name="test",
            transport="sse",
            url="http://localhost:8080",
            auth_config={"headers": {"X-API-Key": "my-key", "X-Custom": "value"}},
        )
        headers = registry._build_auth_headers(conn)
        assert headers["X-API-Key"] == "my-key"
        assert headers["X-Custom"] == "value"

    def test_build_headers_explicit_auth_takes_priority(self, registry):
        conn = MCPServerConnection(
            name="test",
            transport="sse",
            url="http://localhost:8080",
            auth_config={
                "headers": {"Authorization": "Bearer explicit-token"},
                "api_key": "sk-ignored",
            },
        )
        headers = registry._build_auth_headers(conn)
        assert headers["Authorization"] == "Bearer explicit-token"


# ============================================================
# P2: MCP 命名空间工具测试
# ============================================================


class TestMCPNamespacedTools:
    """测试命名空间工具注册/注销"""

    def test_create_namespaced_tool(self):
        from app.modules.tools.mcp_client import _create_mcp_namespaced_tool

        tool_info = {
            "name": "search",
            "description": "搜索工具",
            "inputSchema": {
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        }
        tool_cls = _create_mcp_namespaced_tool("my_server", tool_info)
        assert tool_cls.name == "mcp__my_server__search"
        assert "[MCP:my_server]" in tool_cls.description
        assert "query" in tool_cls.parameters
        assert "query" in tool_cls.required

    def test_create_namespaced_tool_no_schema(self):
        from app.modules.tools.mcp_client import _create_mcp_namespaced_tool

        tool_info = {"name": "simple", "description": "简单工具"}
        tool_cls = _create_mcp_namespaced_tool("srv", tool_info)
        assert tool_cls.name == "mcp__srv__simple"
        assert tool_cls.parameters == {}

    def test_register_and_unregister(self):
        from app.modules.tools.mcp_client import (
            _namespace_tools,
            register_mcp_namespace_tools,
            unregister_mcp_namespace_tools,
        )
        from app.modules.tools.registry import get_tool_registry

        registry = get_tool_registry()
        server_name = "test_ns_server"
        tools = [
            {"name": "tool_a", "description": "Tool A"},
            {"name": "tool_b", "description": "Tool B"},
        ]

        # Register
        register_mcp_namespace_tools(server_name, tools)
        assert (server_name, "tool_a") in _namespace_tools
        assert (server_name, "tool_b") in _namespace_tools

        # Verify tools are in registry
        assert registry.get_tool("mcp__test_ns_server__tool_a") is not None
        assert registry.get_tool("mcp__test_ns_server__tool_b") is not None

        # Unregister
        unregister_mcp_namespace_tools(server_name)
        assert (server_name, "tool_a") not in _namespace_tools
        assert (server_name, "tool_b") not in _namespace_tools


# ============================================================
# P2: MCP 自动发现测试
# ============================================================


class TestMCPAutoDiscover:
    """测试 auto_discover_mcp_servers"""

    @pytest.mark.asyncio
    async def test_auto_discover_empty_db(self):
        from app.modules.tools.mcp_client import auto_discover_mcp_servers

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await auto_discover_mcp_servers()
            assert result["connected"] == 0
            assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_auto_discover_db_error(self):
        from app.modules.tools.mcp_client import auto_discover_mcp_servers

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))
        mock_session_maker = MagicMock(return_value=mock_session)

        with patch("app.core.database.async_session_maker", mock_session_maker):
            result = await auto_discover_mcp_servers()
            assert result["connected"] == 0
            assert result["failed"] == 0
