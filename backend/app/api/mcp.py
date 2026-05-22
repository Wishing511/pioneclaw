"""
MCP 服务器配置与管理 API

提供 MCP 服务器的 CRUD、连接测试、工具发现和资源列举功能。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models.models import MCPServerConfig, User
from app.schemas.schemas import (
    MCPConnectionStatus,
    MCPServerConfigCreate,
    MCPServerConfigResponse,
    MCPServerConfigUpdate,
    MessageResponse,
)

router = APIRouter(prefix="/mcp", tags=["MCP"])


# ── 辅助 ──────────────────────────────────────────────────────


async def _register_to_tool_registry(config: MCPServerConfig):
    """将数据库配置同步到运行时工具注册表"""
    from app.modules.tools.mcp_client import get_mcp_registry

    registry = get_mcp_registry()

    existing = registry.get_server(config.name)
    if existing:
        registry.unregister_server(config.name)

    registry.register_server(
        name=config.name,
        transport=config.transport,
        command=config.command,
        args=config.args,
        env=config.env,
        url=config.url,
        auth_config=config.auth_config,
    )


def _config_to_response(config: MCPServerConfig) -> MCPServerConfigResponse:
    return MCPServerConfigResponse(
        id=config.id,
        name=config.name,
        transport=config.transport,
        command=config.command,
        args=config.args,
        env=config.env,
        url=config.url,
        auth_config=config.auth_config,
        is_enabled=config.is_enabled,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


# ── 端点 ──────────────────────────────────────────────────────


@router.get("/servers", response_model=list[MCPServerConfigResponse])
async def list_mcp_servers(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出所有已配置的 MCP 服务器"""
    result = await db.execute(
        select(MCPServerConfig)
        .order_by(MCPServerConfig.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    configs = result.scalars().all()
    return [_config_to_response(c) for c in configs]


@router.post(
    "/servers",
    response_model=MCPServerConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp_server(
    config_data: MCPServerConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """添加 MCP 服务器配置"""
    config = MCPServerConfig(
        name=config_data.name,
        transport=config_data.transport,
        command=config_data.command,
        args=config_data.args,
        env=config_data.env,
        url=config_data.url,
        auth_config=config_data.auth_config,
        is_enabled=config_data.is_enabled,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    # 同步到运行时注册表
    if config.is_enabled:
        await _register_to_tool_registry(config)

    return _config_to_response(config)


@router.get("/servers/{server_id}", response_model=MCPServerConfigResponse)
async def get_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 MCP 服务器配置详情"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")
    return _config_to_response(config)


@router.put("/servers/{server_id}", response_model=MCPServerConfigResponse)
async def update_mcp_server(
    server_id: int,
    config_data: MCPServerConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 MCP 服务器配置"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")

    if config_data.name is not None:
        config.name = config_data.name
    if config_data.transport is not None:
        config.transport = config_data.transport
    if config_data.command is not None:
        config.command = config_data.command
    if config_data.args is not None:
        config.args = config_data.args
    if config_data.env is not None:
        config.env = config_data.env
    if config_data.url is not None:
        config.url = config_data.url
    if config_data.auth_config is not None:
        config.auth_config = config_data.auth_config
    if config_data.is_enabled is not None:
        config.is_enabled = config_data.is_enabled

    await db.commit()
    await db.refresh(config)

    # 同步到运行时注册表
    if config.is_enabled:
        await _register_to_tool_registry(config)
    else:
        from app.modules.tools.mcp_client import get_mcp_registry

        get_mcp_registry().unregister_server(config.name)

    return _config_to_response(config)


@router.delete("/servers/{server_id}", response_model=MessageResponse)
async def delete_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除 MCP 服务器配置"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")

    # 从运行时注册表移除
    from app.modules.tools.mcp_client import get_mcp_registry

    get_mcp_registry().unregister_server(config.name)

    await db.delete(config)
    await db.commit()
    return MessageResponse(message="MCP 服务器配置已删除")


@router.post("/servers/{server_id}/connect", response_model=MCPConnectionStatus)
async def connect_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """连接到 MCP 服务器并发现工具/资源"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")

    # 同步到注册表
    await _register_to_tool_registry(config)

    # 连接
    from app.modules.tools.mcp_client import get_mcp_registry

    registry = get_mcp_registry()
    conn = await registry.connect_server(config.name)

    return MCPConnectionStatus(
        server=conn.name,
        status=conn.status,
        tool_count=len(conn.tools),
        resource_count=len(conn.resources),
        server_info=conn.server_info,
        error_message=conn.error_message,
    )


@router.get("/servers/{server_id}/tools")
async def list_mcp_server_tools(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出 MCP 服务器提供的工具"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")

    from app.modules.tools.mcp_client import get_mcp_registry

    registry = get_mcp_registry()
    tools = await registry.list_tools(config.name)
    return {"server": config.name, "tools": tools, "count": len(tools)}


@router.get("/servers/{server_id}/resources")
async def list_mcp_server_resources(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出 MCP 服务器提供的资源"""
    result = await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.id == server_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="MCP 服务器配置不存在")

    from app.modules.tools.mcp_client import get_mcp_registry

    registry = get_mcp_registry()
    resources = await registry.list_resources(config.name)
    return resources


@router.get("/status")
async def mcp_overall_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取所有 MCP 服务器的总体状态"""
    result = await db.execute(select(MCPServerConfig).order_by(MCPServerConfig.name))
    configs = result.scalars().all()

    from app.modules.tools.mcp_client import get_mcp_registry

    registry = get_mcp_registry()

    servers_status = []
    for c in configs:
        conn = registry.get_server(c.name)
        servers_status.append(
            {
                "id": c.id,
                "name": c.name,
                "transport": c.transport,
                "is_enabled": c.is_enabled,
                "connection_status": conn.status if conn else "disconnected",
                "tool_count": len(conn.tools) if conn else 0,
                "resource_count": len(conn.resources) if conn else 0,
            }
        )

    return {
        "total": len(servers_status),
        "connected": sum(
            1 for s in servers_status if s["connection_status"] == "connected"
        ),
        "servers": servers_status,
    }
