"""
SendMessageTool — Agent 间消息传递

包含：
- 收件箱注册表（模块级状态）：register_agent / unregister_agent / send_to_agent / list_agents
- SendMessageTool：Agent 通过工具调用发送消息给其他 Agent
"""

import asyncio
import json
import logging
from datetime import datetime

from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 收件箱注册表
# ═══════════════════════════════════════════════════════════

_agent_inboxes: dict[str, asyncio.Queue] = {}
_agent_metadata: dict[str, dict] = {}


def register_agent(agent_id: str, label: str = "") -> asyncio.Queue:
    """注册一个 Agent，为其创建收件箱队列，返回该队列"""
    queue = asyncio.Queue()
    _agent_inboxes[agent_id] = queue
    _agent_metadata[agent_id] = {
        "agent_id": agent_id,
        "label": label or agent_id,
        "status": "running",
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"Agent '{agent_id}' registered for messaging")
    return queue


def unregister_agent(agent_id: str) -> None:
    """注销 Agent，移除收件箱"""
    if agent_id in _agent_inboxes:
        del _agent_inboxes[agent_id]
    if agent_id in _agent_metadata:
        _agent_metadata[agent_id]["status"] = "completed"
    logger.info(f"Agent '{agent_id}' unregistered from messaging")


def send_to_agent(agent_id: str, message: dict) -> bool:
    """向指定 Agent 发送消息，返回是否成功"""
    queue = _agent_inboxes.get(agent_id)
    if queue is None:
        return False
    queue.put_nowait(message)
    logger.debug(f"Message delivered to agent '{agent_id}'")
    return True


def list_agents() -> list:
    """列出所有已注册的 Agent（含已完成的）"""
    return list(_agent_metadata.values())


# ═══════════════════════════════════════════════════════════
# SendMessageTool
# ═══════════════════════════════════════════════════════════


class SendMessageTool(BaseTool):
    """向其他 Agent 发送消息或列出可通信的 Agent"""

    name = "send_message"
    description = (
        "向其他正在运行的 Agent 发送消息，或列出当前可通信的 Agent 列表。\n"
        "支持三种操作：\n"
        "- list_agents: 列出所有已注册的 Agent（含运行中/已完成）及其状态\n"
        "- send: 向指定 Agent 发送一条消息，消息将在该 Agent 的下一迭代中被处理\n"
        "- send_to_team: 向团队所有成员群发消息"
    )
    parameters = {
        "action": ToolParameter(
            type="string",
            description="操作类型：'send' 发送消息，'list_agents' 列出 Agent，'send_to_team' 群发团队",
            enum=["send", "list_agents", "send_to_team"],
        ),
        "target_agent": ToolParameter(
            type="string",
            description="目标 Agent ID（action='send' 时必填）",
            default="",
        ),
        "message": ToolParameter(
            type="string",
            description="要发送的消息内容（action='send'/'send_to_team' 时必填）",
            default="",
        ),
        "team_id": ToolParameter(
            type="string",
            description="目标团队 ID（action='send_to_team' 时必填）",
            default="",
        ),
    }
    required = ["action"]

    async def execute(
        self,
        action: str,
        target_agent: str = "",
        message: str = "",
        team_id: str = "",
        **kwargs,
    ) -> str:
        try:
            if action == "list_agents":
                agents = list_agents()
                return json.dumps(
                    {
                        "success": True,
                        "agents": agents,
                        "total": len(agents),
                    },
                    ensure_ascii=False,
                )

            elif action == "send":
                if not target_agent or not target_agent.strip():
                    return json.dumps(
                        {
                            "success": False,
                            "error": "send 操作需要提供 target_agent 参数",
                        },
                        ensure_ascii=False,
                    )

                if not message or not message.strip():
                    return json.dumps(
                        {
                            "success": False,
                            "error": "send 操作需要提供 message 参数",
                        },
                        ensure_ascii=False,
                    )

                sender_id = kwargs.get("sender_id", "unknown")
                msg = {
                    "from": sender_id,
                    "message": message.strip(),
                    "timestamp": datetime.now().isoformat(),
                }

                ok = send_to_agent(target_agent.strip(), msg)
                if ok:
                    return json.dumps(
                        {
                            "success": True,
                            "target_agent": target_agent.strip(),
                            "message": f"消息已发送至 Agent '{target_agent.strip()}'",
                        },
                        ensure_ascii=False,
                    )
                else:
                    available = [
                        m["agent_id"]
                        for m in _agent_metadata.values()
                        if m["status"] == "running"
                    ]
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"Agent 不存在或已离线: '{target_agent.strip()}'",
                            "available_agents": available,
                        },
                        ensure_ascii=False,
                    )

            elif action == "send_to_team":
                if not team_id or not team_id.strip():
                    return json.dumps(
                        {
                            "success": False,
                            "error": "send_to_team 操作需要提供 team_id 参数",
                        },
                        ensure_ascii=False,
                    )

                if not message or not message.strip():
                    return json.dumps(
                        {
                            "success": False,
                            "error": "send_to_team 操作需要提供 message 参数",
                        },
                        ensure_ascii=False,
                    )

                from app.modules.tools.team import send_to_team as _send_to_team

                sender_id = kwargs.get("sender_id", "unknown")
                msg = {
                    "from": sender_id,
                    "message": message.strip(),
                    "timestamp": datetime.now().isoformat(),
                }
                result = _send_to_team(team_id.strip(), msg)
                return json.dumps(result, ensure_ascii=False)

            else:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"未知操作: '{action}'。支持的操作: send, list_agents, send_to_team",
                    },
                    ensure_ascii=False,
                )

        except Exception as e:
            logger.error(f"SendMessageTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
