"""
ConfigTool — Agent 读写系统配置

支持：
- list: 列出配置项（可按 category 过滤）
- get: 读取单个配置项
- set: 预览→确认→写入（security 类配置需用户确认）
"""

import json
import logging

from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# 安全类配置：修改时需要用户确认
_SECURITY_CATEGORY = "security"

# 写入黑名单：敏感项不允许 Agent 修改
_WRITE_BLACKLIST = {
    "token_expiry",  # Token 有效期
    "smtp_pass",  # 邮箱密码
    "multi_login",  # 多端登录策略
}

# 读取黑名单：不允许 Agent 读取敏感环境变量
_READ_BLACKLIST = set()  # SystemSetting 中无极度敏感项


class ConfigTool(BaseTool):
    """读写系统配置 — 查看配置、修改配置（安全类需确认）"""

    name = "config"
    description = (
        "读写系统配置。支持三种操作：\n"
        "- list: 列出配置项（可按 category 过滤：general/execution/security/notification）\n"
        "- get: 读取单个配置项的值和描述\n"
        "- set: 修改配置项（security 类需用户确认后才会写入）\n"
        "\n"
        "注意：部分敏感配置项（如 token_expiry、smtp_pass）不允许通过此工具修改。"
    )
    parameters = {
        "action": ToolParameter(
            type="string",
            description="操作类型：'list' 列出配置、'get' 读取单项、'set' 修改配置",
            enum=["list", "get", "set"],
        ),
        "key": ToolParameter(
            type="string",
            description="配置项 key（get/set 时必填）",
            default="",
        ),
        "value": ToolParameter(
            type="string",
            description="新值（set 时必填）",
            default="",
        ),
        "category": ToolParameter(
            type="string",
            description="分类过滤（list 时可选）：general/execution/security/notification",
            default="",
        ),
    }
    required = ["action"]

    async def execute(
        self, action: str, key: str = "", value: str = "", category: str = "", **kwargs
    ) -> str:
        try:
            if action == "list":
                return await self._handle_list(category)
            elif action == "get":
                return await self._handle_get(key)
            elif action == "set":
                return await self._handle_set(key, value)
            else:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"未知操作: '{action}'。支持的操作: list, get, set",
                    },
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.error(f"ConfigTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def _handle_list(self, category: str) -> str:
        """列出配置项"""
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.models.models import SystemSetting

        async with async_session_maker() as session:
            query = select(SystemSetting)
            if category:
                query = query.where(SystemSetting.category == category)
            result = await session.execute(query.order_by(SystemSetting.key))
            settings = result.scalars().all()

        items = []
        for s in settings:
            if s.key in _READ_BLACKLIST:
                continue
            items.append(
                {
                    "key": s.key,
                    "value": s.value,
                    "category": s.category,
                    "description": s.description or "",
                }
            )

        return json.dumps(
            {
                "success": True,
                "settings": items,
                "total": len(items),
                "category_filter": category or "all",
            },
            ensure_ascii=False,
        )

    async def _handle_get(self, key: str) -> str:
        """读取单个配置项"""
        if not key or not key.strip():
            return json.dumps(
                {
                    "success": False,
                    "error": "get 操作需要提供 key 参数",
                },
                ensure_ascii=False,
            )

        if key in _READ_BLACKLIST:
            return json.dumps(
                {
                    "success": False,
                    "error": f"不允许读取配置项: '{key}'",
                },
                ensure_ascii=False,
            )

        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.models.models import SystemSetting

        async with async_session_maker() as session:
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()

        if not setting:
            return json.dumps(
                {
                    "success": False,
                    "error": f"配置项不存在: '{key}'",
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "success": True,
                "key": setting.key,
                "value": setting.value,
                "category": setting.category,
                "description": setting.description or "",
            },
            ensure_ascii=False,
        )

    async def _handle_set(self, key: str, value: str) -> str:
        """修改配置项（security 类需确认）"""
        if not key or not key.strip():
            return json.dumps(
                {
                    "success": False,
                    "error": "set 操作需要提供 key 参数",
                },
                ensure_ascii=False,
            )

        key = key.strip()

        if key in _WRITE_BLACKLIST:
            return json.dumps(
                {
                    "success": False,
                    "error": f"不允许修改敏感配置项: '{key}'",
                },
                ensure_ascii=False,
            )

        # 查询当前值
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.models.models import SystemSetting

        async with async_session_maker() as session:
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()

        current_value = setting.value if setting else None
        current_category = setting.category if setting else "custom"
        current_desc = setting.description if setting else ""

        # 值相同，无需修改
        if current_value == value:
            return json.dumps(
                {
                    "success": True,
                    "message": f"配置项 '{key}' 的值已经是 '{value}'，无需修改",
                    "key": key,
                    "value": value,
                },
                ensure_ascii=False,
            )

        # security 类需要用户确认
        if current_category == _SECURITY_CATEGORY:
            confirmed = await self._confirm_change(
                key, current_value, value, current_desc
            )
            if not confirmed:
                return json.dumps(
                    {
                        "success": False,
                        "error": "用户拒绝了配置变更",
                        "key": key,
                        "old_value": current_value,
                        "rejected_value": value,
                    },
                    ensure_ascii=False,
                )

        # 写入 DB
        async with async_session_maker() as session:
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()

            if setting:
                setting.value = value
            else:
                setting = SystemSetting(
                    key=key,
                    value=value,
                    category="custom",
                    description="Agent 自定义配置",
                )
                session.add(setting)

            await session.commit()

        return json.dumps(
            {
                "success": True,
                "key": key,
                "old_value": current_value,
                "new_value": value,
                "category": current_category,
                "message": f"配置项 '{key}' 已从 '{current_value}' 修改为 '{value}'",
            },
            ensure_ascii=False,
        )

    async def _confirm_change(
        self, key: str, old_value: str | None, new_value: str, description: str
    ) -> bool:
        """通过 InterruptManager 让用户确认安全类配置变更"""
        try:
            from app.modules.agent.interrupt import (
                InterruptOption,
                InterruptReason,
                get_interrupt_manager,
            )

            manager = get_interrupt_manager()

            message = (
                f"### 配置变更确认\n\n"
                f"**配置项**: `{key}`\n"
                f"**描述**: {description or '无'}\n"
                f"**当前值**: `{old_value}`\n"
                f"**新值**: `{new_value}`\n\n"
                f"此配置属于安全类别，请确认是否允许修改。"
            )

            options = [
                InterruptOption(
                    label="确认修改",
                    value="approve",
                    description=f"允许将 '{key}' 从 '{old_value}' 修改为 '{new_value}'",
                    style="primary",
                ),
                InterruptOption(
                    label="拒绝",
                    value="reject",
                    description="保持当前配置不变",
                    style="danger",
                ),
            ]

            interrupt = await manager.create_interrupt(
                reason=InterruptReason.CUSTOM,
                message=message,
                options=options,
                ttl=300,
                details={
                    "config_key": key,
                    "old_value": old_value,
                    "new_value": new_value,
                },
            )

            # 轮询等待用户响应（最多 300 秒）
            import asyncio

            timeout = 300
            elapsed = 0.0
            interval = 2.0

            while elapsed < timeout:
                await asyncio.sleep(interval)
                elapsed += interval

                ip = await manager.get_interrupt(interrupt.id)
                if ip is None:
                    if interrupt.is_resolved():
                        return interrupt.resolution == "approve"
                    return False

                if ip.is_expired():
                    return False

                if ip.is_resolved():
                    return ip.resolution == "approve"

            return False

        except Exception as e:
            logger.error(f"ConfigTool confirm error: {e}")
            return False
