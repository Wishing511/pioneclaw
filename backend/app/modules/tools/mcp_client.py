"""
MCP 客户端抽象层

提供 MCP 服务器连接管理和工具调用能力。
借鉴 Claude Code 的 mcp_tool_bridge.rs 设计。

支持：
- stdio 传输（通过子进程通信）
- SSE 传输（Server-Sent Events）
- HTTP 传输（Streamable HTTP）
- 工具发现和缓存
- 资源列举和读取
- 认证头注入（api_key / custom headers）
- 命名空间工具（mcp__{server}__{tool}）
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConnection:
    """MCP 服务器连接状态"""

    name: str
    transport: str  # "stdio" | "sse" | "http"
    command: str | None = None  # stdio 模式的命令
    args: list[str] | None = None  # stdio 模式的命令行参数
    env: dict[str, str] | None = None  # 环境变量
    url: str | None = None  # sse/http 模式的 URL
    auth_config: dict[str, Any] | None = None  # 认证配置
    status: str = "disconnected"  # disconnected/connecting/connected/error
    tools: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    server_info: dict | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "url": self.url,
            "status": self.status,
            "tool_count": len(self.tools),
            "resource_count": len(self.resources),
            "server_info": self.server_info,
            "error_message": self.error_message,
            "has_auth": bool(self.auth_config),
        }


class MCPToolRegistry:
    """MCP 服务器注册表（线程安全单例）

    管理 MCP 服务器的注册、连接和工具调用。
    """

    _instance: Optional["MCPToolRegistry"] = None

    def __init__(self):
        self._servers: dict[str, MCPServerConnection] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "MCPToolRegistry":
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ==================== 服务器注册 ====================

    def register_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        auth_config: dict[str, Any] | None = None,
    ) -> MCPServerConnection:
        """注册 MCP 服务器配置"""
        conn = MCPServerConnection(
            name=name,
            transport=transport,
            command=command,
            args=args or [],
            env=env or {},
            url=url,
            auth_config=auth_config,
        )
        self._servers[name] = conn
        logger.info(f"[MCPToolRegistry] 注册服务器: {name} (transport={transport})")
        return conn

    def unregister_server(self, name: str) -> bool:
        """注销 MCP 服务器"""
        if name in self._servers:
            # 同时注销命名空间工具
            unregister_mcp_namespace_tools(name)
            del self._servers[name]
            logger.info(f"[MCPToolRegistry] 注销服务器: {name}")
            return True
        return False

    def get_server(self, name: str) -> MCPServerConnection | None:
        """获取服务器连接状态"""
        return self._servers.get(name)

    def list_servers(self) -> list[MCPServerConnection]:
        """列出所有已注册的服务器"""
        return list(self._servers.values())

    # ==================== 认证头构建 ====================

    def _build_auth_headers(self, conn: MCPServerConnection) -> dict:
        """从 auth_config 构建 HTTP 认证头"""
        headers = {}
        if not conn.auth_config:
            return headers
        # 支持 {"headers": {"Authorization": "Bearer xxx", "X-API-Key": "yyy"}}
        if "headers" in conn.auth_config and isinstance(
            conn.auth_config["headers"], dict
        ):
            headers.update(conn.auth_config["headers"])
        # 简写 {"api_key": "xxx"} → Bearer token
        if "api_key" in conn.auth_config and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {conn.auth_config['api_key']}"
        return headers

    # ==================== 传输层抽象 ====================

    @asynccontextmanager
    async def _open_transport(
        self, conn: MCPServerConnection
    ) -> AsyncIterator[tuple[Any, Any]]:
        """打开传输层，yield (read_stream, write_stream)

        按 conn.transport 分发到 stdio / sse / http 客户端。
        """
        if conn.transport == "stdio":
            from mcp import stdio_client

            server_params: dict = {"command": conn.command}
            if conn.args:
                server_params["args"] = conn.args
            if conn.env:
                server_params["env"] = conn.env

            async with stdio_client(server_params) as (read, write):
                yield read, write

        elif conn.transport == "sse":
            from mcp.client.sse import sse_client

            headers = self._build_auth_headers(conn)
            async with sse_client(conn.url, headers=headers or None) as (read, write):
                yield read, write

        elif conn.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            headers = self._build_auth_headers(conn)
            async with streamablehttp_client(conn.url, headers=headers or None) as (
                read,
                write,
                _get_session_id,
            ):
                yield read, write

        else:
            raise ValueError(f"不支持的传输模式: {conn.transport}")

    # ==================== 连接与发现 ====================

    async def connect_server(self, name: str) -> MCPServerConnection:
        """连接到 MCP 服务器并发现工具/资源

        支持 stdio / sse / http 传输模式。
        连接成功后自动获取 tools/list 和 resources/list 并缓存。
        """
        conn = self._servers.get(name)
        if not conn:
            raise ValueError(f"MCP 服务器未注册: {name}")

        conn.status = "connecting"
        conn.error_message = None

        try:
            from mcp import ClientSession

            async with self._open_transport(conn) as (read, write), ClientSession(read, write) as session:
                await session.initialize()

                conn.server_info = {
                    "name": getattr(session, "server_name", "unknown"),
                    "version": getattr(session, "server_version", "0.0.0"),
                }

                # 发现工具
                try:
                    tools_result = await session.list_tools()
                    conn.tools = [
                        {
                            "name": t.name,
                            "description": getattr(t, "description", ""),
                            "inputSchema": getattr(t, "inputSchema", {}),
                        }
                        for t in tools_result.tools
                    ]
                except Exception as e:
                    logger.warning(f"[MCPToolRegistry] 工具发现失败 ({name}): {e}")
                    conn.tools = []

                # 发现资源
                try:
                    resources_result = await session.list_resources()
                    conn.resources = [
                        {
                            "uri": str(r.uri),
                            "name": r.name,
                            "description": getattr(r, "description", ""),
                            "mimeType": getattr(r, "mimeType", ""),
                        }
                        for r in resources_result.resources
                    ]
                except Exception as e:
                    logger.warning(f"[MCPToolRegistry] 资源发现失败 ({name}): {e}")
                    conn.resources = []

            conn.status = "connected"
            logger.info(
                f"[MCPToolRegistry] 服务器 '{name}' 已连接: "
                f"{len(conn.tools)} 工具, {len(conn.resources)} 资源"
            )

            # 注册命名空间工具
            if conn.tools:
                register_mcp_namespace_tools(name, conn.tools)

        except Exception as e:
            conn.status = "error"
            conn.error_message = str(e)
            logger.error(f"[MCPToolRegistry] 连接服务器 '{name}' 失败: {e}")

        return conn

    async def list_tools(self, server_name: str) -> list[dict]:
        """列出 MCP 服务器提供的工具

        如果服务器尚未连接，会先尝试连接。
        """
        conn = self._servers.get(server_name)
        if not conn:
            raise ValueError(f"MCP 服务器未注册: {server_name}")

        if conn.status != "connected":
            await self.connect_server(server_name)

        return conn.tools

    # ==================== 工具调用 ====================

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict | None = None
    ) -> dict:
        """调用 MCP 服务器的工具

        Args:
            server_name: MCP 服务器名称
            tool_name: 要调用的工具名
            arguments: 工具参数

        Returns:
            dict: 包含 success/content/error 的结果
        """
        conn = self._servers.get(server_name)
        if not conn:
            return {"success": False, "error": f"MCP 服务器未注册: {server_name}"}

        if conn.status != "connected":
            try:
                await self.connect_server(server_name)
            except Exception as e:
                return {"success": False, "error": f"连接失败: {e}"}

        if conn.status != "connected":
            return {
                "success": False,
                "error": f"服务器未连接: {conn.error_message or conn.status}",
            }

        try:
            from mcp import ClientSession

            async with self._open_transport(conn) as (read, write), ClientSession(read, write) as session:
                await session.initialize()

                arguments = arguments or {}
                result = await session.call_tool(tool_name, arguments)

                content_parts = []
                for c in result.content:
                    if hasattr(c, "text"):
                        content_parts.append({"type": "text", "text": c.text})
                    elif hasattr(c, "data"):
                        content_parts.append(
                            {
                                "type": getattr(c, "type", "resource"),
                                "data": str(c.data)[:1000],
                            }
                        )
                    else:
                        content_parts.append(
                            {"type": "unknown", "data": str(c)[:1000]}
                        )

                return {
                    "success": True,
                    "server": server_name,
                    "tool": tool_name,
                    "content": content_parts,
                    "isError": getattr(result, "isError", False),
                }

        except Exception as e:
            conn.status = "error"
            conn.error_message = str(e)
            return {"success": False, "error": f"工具调用失败: {e}"}

    # ==================== 资源操作 ====================

    async def list_resources(self, server_name: str | None = None) -> dict:
        """列出 MCP 服务器提供的资源

        Args:
            server_name: 服务器名称，为空则列出所有服务器的资源
        """
        if server_name:
            conn = self._servers.get(server_name)
            if not conn:
                raise ValueError(f"MCP 服务器未注册: {server_name}")

            if conn.status != "connected":
                await self.connect_server(server_name)

            return {
                "server": server_name,
                "resources": conn.resources,
            }
        else:
            all_resources = {}
            for name, conn in self._servers.items():
                if conn.status == "connected":
                    all_resources[name] = conn.resources
                else:
                    all_resources[name] = []
            return {"servers": all_resources}

    async def read_resource(self, server_name: str, uri: str) -> dict:
        """读取 MCP 服务器的资源

        Args:
            server_name: MCP 服务器名称
            uri: 资源 URI
        """
        conn = self._servers.get(server_name)
        if not conn:
            return {"success": False, "error": f"MCP 服务器未注册: {server_name}"}

        if conn.status != "connected":
            try:
                await self.connect_server(server_name)
            except Exception as e:
                return {"success": False, "error": f"连接失败: {e}"}

        try:
            from mcp import ClientSession

            async with self._open_transport(conn) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                result = await session.read_resource(uri)

                contents = []
                for c in result.contents:
                    contents.append(
                        {
                            "uri": str(c.uri) if hasattr(c, "uri") else uri,
                            "mimeType": getattr(c, "mimeType", ""),
                            "text": getattr(c, "text", str(c)[:2000])
                            if hasattr(c, "text")
                            else str(c)[:2000],
                        }
                    )

                return {
                    "success": True,
                    "server": server_name,
                    "uri": uri,
                    "contents": contents,
                }

        except Exception as e:
            return {"success": False, "error": f"资源读取失败: {e}"}


# ==================== 命名空间工具 ====================

_namespace_tools: dict[tuple[str, str], type] = {}


def _create_mcp_namespaced_tool(server_name: str, tool_info: dict) -> type:
    """动态创建 BaseTool 子类，名称 mcp__{server}__{tool}"""
    from app.modules.tools.base import BaseTool, ToolParameter

    tool_name = tool_info["name"]
    tool_desc = tool_info.get("description", "")
    input_schema = tool_info.get("inputSchema", {})

    _params = {}
    properties = input_schema.get("properties", {})
    for param_name, param_spec in properties.items():
        _params[param_name] = ToolParameter(
            type=param_spec.get("type", "string"),
            description=param_spec.get("description", ""),
        )
    _required = input_schema.get("required", [])

    class MCPNamespacedTool(BaseTool):
        name = f"mcp__{server_name}__{tool_name}"
        description = f"[MCP:{server_name}] {tool_desc}"
        parameters = _params
        required = _required

        async def execute(self, **kwargs) -> str:
            import json

            from app.modules.tools.mcp_client import get_mcp_registry

            registry = get_mcp_registry()
            result = await registry.call_tool(server_name, tool_name, kwargs)
            return json.dumps(result, ensure_ascii=False)

    MCPNamespacedTool.__name__ = f"MCPNamespacedTool_{server_name}_{tool_name}"
    MCPNamespacedTool.__qualname__ = MCPNamespacedTool.__name__
    return MCPNamespacedTool


def register_mcp_namespace_tools(server_name: str, tools: list[dict]) -> None:
    """注册 MCP 服务器工具为一级命名空间工具"""
    from app.modules.tools.registry import get_tool_registry

    registry = get_tool_registry()

    for tool_info in tools:
        tool_name = tool_info["name"]
        key = (server_name, tool_name)

        # 重新注册时先注销旧工具
        if key in _namespace_tools:
            registry.unregister(f"mcp__{server_name}__{tool_name}")

        tool_class = _create_mcp_namespaced_tool(server_name, tool_info)
        _namespace_tools[key] = tool_class
        registry.register_class(tool_class)
        logger.debug(f"[MCP] 注册命名空间工具: mcp__{server_name}__{tool_name}")


def unregister_mcp_namespace_tools(server_name: str) -> None:
    """注销 MCP 服务器的所有命名空间工具"""
    from app.modules.tools.registry import get_tool_registry

    registry = get_tool_registry()

    keys_to_remove = [k for k in _namespace_tools if k[0] == server_name]
    for key in keys_to_remove:
        _, tool_name = key
        registry.unregister(f"mcp__{server_name}__{tool_name}")
        del _namespace_tools[key]
        logger.debug(f"[MCP] 注销命名空间工具: mcp__{server_name}__{tool_name}")


# ==================== 自动发现 ====================


async def auto_discover_mcp_servers() -> dict:
    """启动时从 DB 加载 enabled 的 MCP 服务器，注册并连接

    Returns:
        {"connected": int, "failed": int, "errors": list}
    """
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models.models import MCPServerConfig

    registry = get_mcp_registry()
    summary: dict = {"connected": 0, "failed": 0, "errors": []}

    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(MCPServerConfig).where(MCPServerConfig.is_enabled)
            )
            configs = result.scalars().all()
    except Exception as e:
        logger.warning(f"[MCP] 自动发现查询 DB 失败: {e}")
        return summary

    for config in configs:
        registry.register_server(
            name=config.name,
            transport=config.transport,
            command=config.command,
            args=config.args,
            env=config.env,
            url=config.url,
            auth_config=config.auth_config,
        )
        try:
            conn = await registry.connect_server(config.name)
            if conn.status == "connected":
                summary["connected"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append(
                    {"server": config.name, "error": conn.error_message}
                )
        except Exception as e:
            summary["failed"] += 1
            summary["errors"].append({"server": config.name, "error": str(e)})

    logger.info(
        f"[MCP] 自动发现完成: {summary['connected']} 连接成功, {summary['failed']} 失败"
    )
    return summary


# ==================== 全局注册表 ====================

_mcp_registry: MCPToolRegistry | None = None


def get_mcp_registry() -> MCPToolRegistry:
    """获取全局 MCP 注册表"""
    global _mcp_registry
    if _mcp_registry is None:
        _mcp_registry = MCPToolRegistry()
    return _mcp_registry
