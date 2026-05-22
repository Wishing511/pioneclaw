"""
MCP 工具 — Agent 可调用的 MCP 协议工具

提供：
- MCPTool: 调用已注册 MCP 服务器的工具
- ListMcpResourcesTool: 列出 MCP 服务器资源
- ReadMcpResourceTool: 读取 MCP 服务器资源
- McpAuthTool: 查看 MCP 服务器连接状态
"""

import json
import logging
from typing import TYPE_CHECKING

from app.modules.tools.base import BaseTool, ToolParameter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MCPTool(BaseTool):
    """通过 MCP 协议调用外部服务器的工具"""

    name = "mcp"
    description = (
        "调用已配置的 MCP（Model Context Protocol）服务器上的工具。"
        "可以调用外部 MCP 服务器提供的任何工具，包括文件系统操作、数据库查询、API 调用等。"
        "使用 mcp_auth 查看可用的服务器和工具列表。"
    )
    parameters = {
        "server": ToolParameter(
            type="string",
            description="MCP 服务器名称",
        ),
        "tool": ToolParameter(
            type="string",
            description="要调用的工具名称",
        ),
        "arguments": ToolParameter(
            type="object",
            description="工具参数（JSON 对象）",
            default={},
        ),
    }
    required = ["server", "tool"]

    async def execute(
        self, server: str, tool: str, arguments: dict | None = None, **kwargs
    ) -> str:
        try:
            from app.modules.tools.mcp_client import get_mcp_registry

            registry = get_mcp_registry()
            result = await registry.call_tool(server, tool, arguments or {})
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


class ListMcpResourcesTool(BaseTool):
    """列出 MCP 服务器的资源"""

    name = "list_mcp_resources"
    description = (
        "列出 MCP 服务器提供的资源。资源可以是文件、数据库表、API 端点等。"
        "如果不指定服务器名称，则列出所有已连接服务器的资源。"
    )
    parameters = {
        "server": ToolParameter(
            type="string",
            description="MCP 服务器名称（不填则列出所有服务器资源）",
            default="",
        ),
    }
    required = []

    async def execute(self, server: str = "", **kwargs) -> str:
        try:
            from app.modules.tools.mcp_client import get_mcp_registry

            registry = get_mcp_registry()
            result = await registry.list_resources(server or None)
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


class ReadMcpResourceTool(BaseTool):
    """读取 MCP 服务器的资源"""

    name = "read_mcp_resource"
    description = (
        "读取 MCP 服务器上的指定资源。通过资源 URI 读取资源内容，"
        "例如文件内容、数据库记录等。"
    )
    parameters = {
        "server": ToolParameter(
            type="string",
            description="MCP 服务器名称",
        ),
        "uri": ToolParameter(
            type="string",
            description="资源 URI",
        ),
    }
    required = ["server", "uri"]

    async def execute(self, server: str, uri: str, **kwargs) -> str:
        try:
            from app.modules.tools.mcp_client import get_mcp_registry

            registry = get_mcp_registry()
            result = await registry.read_resource(server, uri)
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


class McpAuthTool(BaseTool):
    """查看 MCP 服务器连接状态"""

    name = "mcp_auth"
    description = (
        "查看 MCP 服务器的连接状态和可用工具。显示服务器的连接状态、"
        "可用的工具列表、资源列表等信息。"
    )
    parameters = {
        "server": ToolParameter(
            type="string",
            description="MCP 服务器名称（不填则列出所有服务器）",
            default="",
        ),
    }
    required = []

    async def execute(self, server: str = "", **kwargs) -> str:
        try:
            from app.modules.tools.mcp_client import get_mcp_registry

            registry = get_mcp_registry()

            if server:
                conn = registry.get_server(server)
                if not conn:
                    return json.dumps(
                        {"success": False, "error": f"MCP 服务器未注册: {server}"}
                    )
                return json.dumps(conn.to_dict(), ensure_ascii=False)
            else:
                servers = [s.to_dict() for s in registry.list_servers()]
                return json.dumps(
                    {
                        "servers": servers,
                        "total": len(servers),
                    },
                    ensure_ascii=False,
                )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})
