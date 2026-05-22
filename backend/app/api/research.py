"""
Research + Consolidator API 端点
研究会话和知识整合接口
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User

from ..modules.agent.consolidator import (
    get_consolidator,
)
from ..modules.agent.research import (
    get_research_manager,
)

router = APIRouter(prefix="/research", tags=["Research"])


# ==================== 请求模型 ====================


class CreateSessionRequest(BaseModel):
    """创建会话请求"""

    query: str
    session_type: Literal["research", "chat"] = "chat"


class AddExplorationRequest(BaseModel):
    """添加探索请求"""

    exploration_type: Literal["thinking", "action", "result", "retrieved", "decision"]
    content: str
    metadata: dict[str, Any] | None = None


class AddKnowledgeRequest(BaseModel):
    """添加知识引用请求"""

    source_name: str
    content: str
    score: float | None = None


class CompleteSessionRequest(BaseModel):
    """完成会话请求"""

    solution: str
    success: bool


# ==================== 统计 ====================


@router.get("/stats")
async def get_stats(
    current_user: User = Depends(get_current_active_user),
):
    """获取研究会话统计"""
    manager = get_research_manager()
    return manager.get_stats()


# ==================== 会话管理 ====================


@router.post("/sessions")
async def create_session(
    request: CreateSessionRequest,
    current_user: User = Depends(get_current_active_user),
):
    """创建研究会话"""
    manager = get_research_manager()
    session = manager.create_session(request.query, request.session_type)

    return {"success": True, "session": session.to_dict()}


@router.get("/sessions/recent")
async def get_recent_sessions(
    limit: int = 20,
    current_user: User = Depends(get_current_active_user),
):
    """获取最近会话"""
    manager = get_research_manager()
    sessions = manager.get_recent_sessions(limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取会话详情"""
    manager = get_research_manager()
    session = manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session.to_dict()


@router.post("/sessions/{session_id}/explorations")
async def add_exploration(
    session_id: str,
    request: AddExplorationRequest,
    current_user: User = Depends(get_current_active_user),
):
    """添加探索记录"""
    manager = get_research_manager()

    success = manager.add_exploration(
        session_id=session_id,
        exploration_type=request.exploration_type,
        content=request.content,
        metadata=request.metadata,
    )

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"success": True}


@router.post("/sessions/{session_id}/knowledge")
async def add_knowledge(
    session_id: str,
    request: AddKnowledgeRequest,
    current_user: User = Depends(get_current_active_user),
):
    """添加知识引用"""
    manager = get_research_manager()

    knowledge_ref = {
        "source_name": request.source_name,
        "content": request.content,
        "score": request.score,
    }

    manager.add_knowledge_ref(session_id, knowledge_ref)

    return {"success": True}


@router.post("/sessions/{session_id}/complete")
async def complete_session(
    session_id: str,
    request: CompleteSessionRequest,
    current_user: User = Depends(get_current_active_user),
):
    """完成会话"""
    manager = get_research_manager()
    manager.complete_session(
        session_id=session_id,
        solution=request.solution,
        success=request.success,
    )

    return {"success": True}


# ==================== 整合 ====================


@router.get("/consolidation/stats")
async def get_consolidation_stats(
    current_user: User = Depends(get_current_active_user),
):
    """获取整合统计"""
    consolidator = get_consolidator()
    return consolidator.get_stats()


@router.get("/consolidation/pending")
async def get_pending_consolidations(
    limit: int = 10,
    current_user: User = Depends(get_current_active_user),
):
    """获取待整合的会话"""
    manager = get_research_manager()
    sessions = manager.get_sessions_for_consolidation(limit)

    return {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
    }


@router.post("/consolidate/{session_id}")
async def consolidate_session(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """整合会话为知识文档"""
    manager = get_research_manager()
    consolidator = get_consolidator()

    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = consolidator.consolidate_session(session)

    return {"success": True, "result": result.to_dict()}


# ==================== 解决方案 ====================


@router.get("/solutions")
async def get_solutions(
    limit: int = 20,
    current_user: User = Depends(get_current_active_user),
):
    """获取所有解决方案"""
    consolidator = get_consolidator()
    solutions = consolidator.get_solutions(limit)
    return {"solutions": solutions, "count": len(solutions)}


@router.get("/solutions/{solution_id}")
async def get_solution(
    solution_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取解决方案详情"""
    consolidator = get_consolidator()
    solution = consolidator.get_solution(solution_id)

    if not solution:
        raise HTTPException(status_code=404, detail="Solution not found")

    return solution


@router.get("/solutions/{solution_id}/markdown")
async def get_solution_markdown(
    solution_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取解决方案 Markdown"""
    consolidator = get_consolidator()
    md = consolidator.get_solution_markdown(solution_id)

    if not md:
        raise HTTPException(status_code=404, detail="Solution not found")

    return {"markdown": md}


@router.delete("/solutions/{solution_id}")
async def delete_solution(
    solution_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除解决方案"""
    consolidator = get_consolidator()
    success = consolidator.delete_solution(solution_id)

    if not success:
        raise HTTPException(status_code=404, detail="Solution not found")

    return {"success": True}
