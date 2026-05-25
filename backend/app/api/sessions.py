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
from app.core.time_utils import format_dt as _format_dt
from app.models import Agent, AIModelConfig, Session, SessionMessage, User
from app.modules.agent.token_budget import TokenBudget

router = APIRouter(prefix="/chat/sessions", tags=["会话管理"])
logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128000


async def _resolve_session_context_window(
    db: AsyncSession, session: Session
) -> int:
    """解析会话对应的 context_window

    优先级：
    1. 有 Agent → Agent.model 匹配 AI 配置的 model_name
    2. 无 Agent 或匹配失败 → 取所有活跃配置中 context_window 最大的
    3. 以上均失败 → 硬编码默认值 128k
    """
    if session.agent_id:
        agent_res = await db.execute(select(Agent).where(Agent.id == session.agent_id))
        agent = agent_res.scalar_one_or_none()
        if agent and agent.model:
            model_res = await db.execute(
                select(AIModelConfig.context_window)
                .where(AIModelConfig.model_name == agent.model)
                .where(AIModelConfig.is_active)
                .limit(1)
            )
            cw = model_res.scalar()
            if cw is not None and cw > 0:
                return cw

    # 无 Agent 或匹配失败：取活跃配置中最大的 context_window（排除 0）
    max_res = await db.execute(
        select(AIModelConfig.context_window)
        .where(AIModelConfig.is_active)
        .where(AIModelConfig.context_window > 0)
        .order_by(AIModelConfig.context_window.desc())
        .limit(1)
    )
    cw = max_res.scalar()
    return cw if cw is not None else DEFAULT_CONTEXT_WINDOW


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
            "created_at": _format_dt(s.created_at),
            "updated_at": _format_dt(s.updated_at),
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

    context_window = await _resolve_session_context_window(db, session)

    # 估算当前会话 token 用量（供前端切换会话时立即显示）
    input_tokens = _estimate_tokens(messages)
    budget = TokenBudget(context_window=context_window)
    context_usage = budget.to_dict(input_tokens)

    # 角色分布和工具结果统计（与 SSE done 事件中的 context_report 格式对齐）
    role_counts: dict[str, int] = {}
    role_tokens: dict[str, int] = {}
    tool_result_count = 0
    tool_result_tokens = 0
    tool_call_count = 0  # assistant 消息中的 tool_calls 数量
    for m in messages:
        role = m.role or "unknown"
        content_len = len(m.content or "") + len(m.reasoning_content or "")
        est = content_len // 4
        role_counts[role] = role_counts.get(role, 0) + 1
        role_tokens[role] = role_tokens.get(role, 0) + est
        if role == "tool":
            tool_result_count += 1
            tool_result_tokens += est
        # 统计 assistant 消息中的 tool_calls（调用请求）
        if role == "assistant" and m.tool_calls:
            tool_call_count += len(m.tool_calls)
    # 工具统计优先展示 tool_calls 次数（更直观），无 tool_calls 时展示 tool 结果消息数
    display_tool_count = tool_call_count if tool_call_count > 0 else tool_result_count
    display_tool_tokens = tool_result_tokens

    context_report = {
        **context_usage,
        "total_messages": len(messages),
        "output_tokens": None,  # get_session 无法获取 output_tokens，与 SSE 事件区分
        "source": "estimated",
        "role_distribution": {
            role: {"count": role_counts.get(role, 0), "tokens": role_tokens.get(role, 0)}
            for role in set(list(role_counts.keys()) + ["system", "user", "assistant", "tool"])
        },
        "tool_results": {
            "count": display_tool_count,
            "tokens": display_tool_tokens,
        },
    }
    logger.info(
        f"[get_session] session={session_id} messages={len(messages)} input_tokens={input_tokens}"
    )

    return {
        "id": session.id,
        "title": session.title,
        "workspace_path": session.workspace_path,
        "created_at": _format_dt(session.created_at),
        "updated_at": _format_dt(session.updated_at),
        "context_report": context_report,
        "context_usage": context_usage,  # 向后兼容
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "reasoning_content": m.reasoning_content,
                "tool_calls": m.tool_calls,
                "created_at": _format_dt(m.created_at),
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
        "created_at": _format_dt(session.created_at),
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

    # 解析 tool_calls，失败时降级为 None 避免整单保存失败
    parsed_tool_calls = None
    if body.tool_calls:
        try:
            parsed_tool_calls = _safe_loads_tool_calls(body.tool_calls)
        except Exception as e:
            logger.warning(f"save_message: failed to parse tool_calls for session={session_id}, error={e}")

    msg = SessionMessage(
        session_id=session_id,
        role=body.role,
        content=body.content or "",
        reasoning_content=body.reasoning_content or None,
        tool_calls=parsed_tool_calls,
    )
    db.add(msg)
    session.message_count = (session.message_count or 0) + 1

    # Auto-title from first user message
    if body.role == "user" and session.title == "新对话":
        session.title = body.content[:50] + ("..." if len(body.content) > 50 else "")

    await db.commit()
    return {"id": msg.id, "role": msg.role, "created_at": _format_dt(msg.created_at)}
