"""
聊天会话 API — 列表、消息历史、删除
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import Agent, AIModelConfig, Session, SessionMessage, User
from app.modules.agent.token_budget import TokenBudget

router = APIRouter(prefix="/chat/sessions", tags=["会话管理"])
logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128000


def _estimate_tokens(msgs: list[SessionMessage]) -> int:
    """估算消息列表的 token 用量（字符数 // 4 的启发式算法）。"""
    total = 0
    for m in msgs:
        total += len(m.content or "") // 4
        total += len(m.reasoning_content or "") // 4
        if m.tool_calls:
            total += len(str(m.tool_calls)) // 4
    return total


@router.get("")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户的会话列表"""
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id, Session.status == "active")
        .order_by(desc(Session.updated_at))
    )
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "agent_id": s.agent_id,
            "runner_id": s.runner_id,
            "message_count": s.message_count,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in sessions
    ]


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取会话详情（含消息）"""
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    msgs_result = await db.execute(
        select(SessionMessage)
        .where(SessionMessage.session_id == session_id)
        .order_by(SessionMessage.created_at)
    )
    messages = msgs_result.scalars().all()

    # 尝试从会话关联的 Agent 获取模型上下文窗口
    context_window = DEFAULT_CONTEXT_WINDOW
    if session.agent_id:
        agent_res = await db.execute(select(Agent).where(Agent.id == session.agent_id))
        agent = agent_res.scalar_one_or_none()
        if agent and agent.model:
            model_res = await db.execute(
                select(AIModelConfig.context_window)
                .where(AIModelConfig.model_name == agent.model)
                .where(AIModelConfig.is_active)
            )
            cw = model_res.scalar_one_or_none()
            if cw:
                context_window = cw

    # 估算当前会话 token 用量（供前端切换会话时立即显示）
    input_tokens = _estimate_tokens(messages)
    budget = TokenBudget(context_window=context_window)
    context_usage = budget.to_dict(input_tokens)
    logger.info(
        f"[get_session] session={session_id} messages={len(messages)} input_tokens={input_tokens} context_usage={context_usage}"
    )

    return {
        "id": session.id,
        "title": session.title,
        "workspace_path": session.workspace_path,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "context_usage": context_usage,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "reasoning_content": m.reasoning_content,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("")
async def create_session(
    title: str = "新对话",
    agent_id: int | None = None,
    runner_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建新会话"""
    session = Session(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        title=title,
        agent_id=agent_id,
        runner_id=runner_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at.isoformat(),
    }


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除会话"""
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # Soft delete — mark as archived
    session.status = "archived"
    await db.commit()
    return {"message": "会话已删除"}


class SaveMessageBody(BaseModel):
    role: str
    content: str | None = ""
    reasoning_content: str | None = None
    tool_calls: str | None = None


@router.post("/{session_id}/messages")
async def save_message(
    session_id: str,
    body: SaveMessageBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """保存消息到会话"""
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    import json as _json

    def _safe_loads_tool_calls(raw: str, max_depth: int = 5) -> list:
        """安全解析 tool_calls JSON，限制嵌套深度防止恶意 payload。"""
        data = _json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("tool_calls must be a list")

        def _check_depth(obj, depth: int) -> None:
            if depth > max_depth:
                raise ValueError(f"tool_calls nested depth exceeds {max_depth}")
            if isinstance(obj, dict):
                for v in obj.values():
                    _check_depth(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _check_depth(item, depth + 1)

        _check_depth(data, 1)
        return data

    msg = SessionMessage(
        session_id=session_id,
        role=body.role,
        content=body.content or "",
        reasoning_content=body.reasoning_content or None,
        tool_calls=_safe_loads_tool_calls(body.tool_calls) if body.tool_calls else None,
    )
    db.add(msg)
    session.message_count = (session.message_count or 0) + 1

    # Auto-title from first user message
    if body.role == "user" and session.title == "新对话":
        session.title = body.content[:50] + ("..." if len(body.content) > 50 else "")

    await db.commit()
    return {"id": msg.id, "role": msg.role, "created_at": msg.created_at.isoformat()}
