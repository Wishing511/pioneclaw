"""
TeamCreateTool + TeamDeleteTool — Agent 团队管理

包含：
- 团队注册表（模块级状态）：运行时团队跟踪 + 群发
- TeamCreateTool：创建团队（DB Organization type="team" + 运行时注册）
- TeamDeleteTool：删除团队
"""

import json
import logging
import uuid
from datetime import datetime

from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 团队注册表（运行时状态）
# ═══════════════════════════════════════════════════════════

_teams: dict[
    str, dict
] = {}  # team_id → {name, member_agent_ids, created_at, description}


def register_team(
    team_id: str, name: str, member_ids: list[str] | None = None, description: str = ""
) -> None:
    """注册团队到运行时注册表"""
    _teams[team_id] = {
        "team_id": team_id,
        "name": name,
        "member_agent_ids": list(member_ids or []),
        "description": description,
        "created_at": datetime.now().isoformat(),
    }
    logger.info(
        f"Team '{name}' ({team_id}) registered with {len(member_ids or [])} members"
    )


def unregister_team(team_id: str) -> bool:
    """从运行时注册表移除团队"""
    if team_id in _teams:
        del _teams[team_id]
        logger.info(f"Team '{team_id}' unregistered")
        return True
    return False


def add_team_member(team_id: str, agent_id: str) -> bool:
    """向团队添加成员"""
    team = _teams.get(team_id)
    if not team:
        return False
    if agent_id not in team["member_agent_ids"]:
        team["member_agent_ids"].append(agent_id)
    return True


def remove_team_member(team_id: str, agent_id: str) -> bool:
    """从团队移除成员"""
    team = _teams.get(team_id)
    if not team:
        return False
    if agent_id in team["member_agent_ids"]:
        team["member_agent_ids"].remove(agent_id)
    return True


def list_teams() -> list:
    """列出所有运行时团队"""
    return list(_teams.values())


def get_team(team_id: str) -> dict | None:
    """获取团队信息"""
    return _teams.get(team_id)


def send_to_team(team_id: str, message: dict) -> dict:
    """向团队所有成员群发消息（复用 send_message 的 send_to_agent）"""
    from app.modules.tools.send_message import send_to_agent

    team = _teams.get(team_id)
    if not team:
        return {"success": False, "error": f"团队不存在: '{team_id}'"}

    results = {}
    for agent_id in team["member_agent_ids"]:
        ok = send_to_agent(agent_id, message)
        results[agent_id] = "delivered" if ok else "failed"

    failed = sum(1 for v in results.values() if v == "failed")
    return {
        "success": failed == 0,
        "team_id": team_id,
        "total_members": len(team["member_agent_ids"]),
        "delivered": len(team["member_agent_ids"]) - failed,
        "failed": failed,
        "details": results,
    }


# ═══════════════════════════════════════════════════════════
# TeamCreateTool
# ═══════════════════════════════════════════════════════════


class TeamCreateTool(BaseTool):
    """创建 Agent 团队 — 多个 Agent 可以在一个团队中协作"""

    name = "team_create"
    description = (
        "创建一个 Agent 团队。团队成员可以通过 send_message 的 send_to_team 操作互相通信。\n"
        "团队会在数据库中创建为 Organization(type='team') 记录，同时在运行时注册。\n"
        "member_agent_ids 应为已在运行时注册的 Agent ID 列表（JSON 数组格式）。"
    )
    parameters = {
        "name": ToolParameter(
            type="string",
            description="团队名称",
        ),
        "description": ToolParameter(
            type="string",
            description="团队描述（可选）",
            default="",
        ),
        "member_agent_ids": ToolParameter(
            type="string",
            description='成员 Agent ID 列表，JSON 数组格式，如 \'["a1","a2"]\'（可选）',
            default="",
        ),
    }
    required = ["name"]

    async def execute(
        self, name: str, description: str = "", member_agent_ids: str = "", **kwargs
    ) -> str:
        try:
            # 解析成员列表
            members = []
            if member_agent_ids and member_agent_ids.strip():
                try:
                    members = json.loads(member_agent_ids)
                    if not isinstance(members, list):
                        return json.dumps(
                            {
                                "success": False,
                                "error": "member_agent_ids 必须是 JSON 数组格式",
                            },
                            ensure_ascii=False,
                        )
                except json.JSONDecodeError as e:
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"member_agent_ids JSON 解析失败: {e}",
                        },
                        ensure_ascii=False,
                    )

            # 在 DB 创建 Organization(type="team")
            from app.core.database import async_session_maker
            from app.models.organization import Organization

            team_id = str(uuid.uuid4())
            async with async_session_maker() as session:
                org = Organization(
                    id=team_id,
                    name=name,
                    code=f"team-{uuid.uuid4().hex[:8]}",
                    type="team",
                    level=3,
                    status="active",
                )
                # 设置描述到 meta_data
                if description:
                    org.meta_data = {"description": description}

                session.add(org)
                await session.commit()

            # 注册到运行时
            register_team(team_id, name, members, description)

            return json.dumps(
                {
                    "success": True,
                    "team_id": team_id,
                    "name": name,
                    "description": description,
                    "members": members,
                    "member_count": len(members),
                    "message": f"团队 '{name}' 创建成功，{len(members)} 名成员",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TeamCreateTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════
# TeamDeleteTool
# ═══════════════════════════════════════════════════════════


class TeamDeleteTool(BaseTool):
    """删除 Agent 团队"""

    name = "team_delete"
    description = "删除一个 Agent 团队。从数据库和运行时注册表中移除。"
    parameters = {
        "team_id": ToolParameter(
            type="string",
            description="要删除的团队 ID",
        ),
    }
    required = ["team_id"]

    async def execute(self, team_id: str, **kwargs) -> str:
        try:
            from sqlalchemy import select

            from app.core.database import async_session_maker
            from app.models.organization import Organization

            # 检查 DB 中是否存在
            db_deleted = False
            async with async_session_maker() as session:
                result = await session.execute(
                    select(Organization).where(Organization.id == team_id)
                )
                org = result.scalar_one_or_none()
                if org:
                    await session.delete(org)
                    await session.commit()
                    db_deleted = True

            # 从运行时注册表移除
            rt_removed = unregister_team(team_id)

            if not db_deleted and not rt_removed:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"团队不存在: '{team_id}'",
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "success": True,
                    "team_id": team_id,
                    "db_deleted": db_deleted,
                    "runtime_removed": rt_removed,
                    "message": f"团队 '{team_id}' 已删除",
                },
                ensure_ascii=False,
            )

        except Exception as e:
            logger.error(f"TeamDeleteTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
