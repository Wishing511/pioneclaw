"""
分层记忆 API — L0/L1/L2 三级记忆体系

端点:
- POST /layered-memory/store   存储记忆(含L0/L1自动生成)
- POST /layered-memory/recall  语义检索(含意图分析+重排序)
- GET  /layered-memory/list    列表(支持层级/类型筛选)
- GET  /layered-memory/stats/overview 统计信息
- POST /layered-memory/promote L1→L2 提升
- POST /layered-memory/evict   清理指定会话的L0
- GET  /layered-memory/{uri}   获取指定记忆
- PUT  /layered-memory/{uri}   更新记忆
- DELETE /layered-memory/{uri} 删除记忆(含子层级)
"""

import contextlib

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.modules.agent.layered_memory.memory_orchestrator import MemoryOrchestrator
from app.modules.agent.vector_store import get_vector_store
from app.schemas.layered_memory import (
    LayeredMemoryBrief,
    LayeredMemoryEvict,
    LayeredMemoryListResponse,
    LayeredMemoryPromote,
    LayeredMemoryRecall,
    LayeredMemoryRecallResponse,
    LayeredMemoryResponse,
    LayeredMemoryStats,
    LayeredMemoryStore,
    LayeredMemoryUpdate,
    RecallResultItem,
)

router = APIRouter(prefix="/layered-memory", tags=["分层记忆"])


def _get_orchestrator(db: AsyncSession, user: User) -> MemoryOrchestrator:
    """创建 MemoryOrchestrator 实例"""
    vector_store = None
    with contextlib.suppress(Exception):
        vector_store = get_vector_store()

    return MemoryOrchestrator(
        db_session=db,
        vector_store=vector_store,
    )


# ==================== 固定路径路由（必须在 /{uri:path} 之前）====================


@router.post("/store", response_model=LayeredMemoryResponse, status_code=201)
async def store_memory(
    data: LayeredMemoryStore,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """存储记忆（含 L0/L1 自动生成）"""
    orchestrator = _get_orchestrator(db, current_user)

    l2 = await orchestrator.store(
        content=data.content,
        name=data.name,
        user_id=current_user.id,
        context_type=data.context_type,
        uri=data.uri,
        parent_uri=data.parent_uri,
        tags=data.tags,
        source=data.source,
        importance=data.importance,
        session_id=data.session_id,
        agent_id=data.agent_id,
    )
    await db.commit()
    await db.refresh(l2)
    return l2


@router.post("/recall", response_model=LayeredMemoryRecallResponse)
async def recall_memory(
    data: LayeredMemoryRecall,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """语义检索（含意图分析 + 重排序）"""
    orchestrator = _get_orchestrator(db, current_user)

    result = await orchestrator.recall(
        query=data.query,
        context_type=data.context_type,
        layers=data.layers,
        top_k=data.top_k,
        user_id=current_user.id,
        agent_id=data.agent_id,
        session_id=data.session_id,
    )
    await db.commit()

    return LayeredMemoryRecallResponse(
        results=[
            RecallResultItem(
                uri=r.uri,
                name=r.name,
                layer=r.layer,
                context_type=r.context_type,
                text=r.text,
                score=round(r.score, 4),
                abstract=r.abstract,
                overview=r.overview,
            )
            for r in result["results"]
        ],
        intent=result["intent"].intent if result.get("intent") else None,
        total=result["total"],
    )


@router.get("", response_model=LayeredMemoryListResponse)
async def list_memories(
    layer: int = Query(None, description="层级筛选 0/1/2"),
    context_type: str = Query("all", description="类型筛选"),
    session_id: str = Query(None, description="会话ID"),
    keyword: str = Query(None, description="关键词搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列表查询（分页）"""
    orchestrator = _get_orchestrator(db, current_user)

    result = await orchestrator.list_memories(
        user_id=current_user.id,
        layer=layer,
        context_type=context_type,
        session_id=session_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )

    items = [
        LayeredMemoryBrief(
            id=m.id,
            uri=m.uri,
            layer=m.layer,
            context_type=m.context_type,
            name=m.name,
            abstract=m.abstract,
            overview=m.overview,
            importance=m.importance,
            access_count=m.access_count,
            source=m.source,
            session_id=m.session_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in result["items"]
    ]
    return LayeredMemoryListResponse(items=items, total=result["total"])


@router.get("/stats/overview", response_model=LayeredMemoryStats)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """统计信息"""
    orchestrator = _get_orchestrator(db, current_user)
    return await orchestrator.stats(user_id=current_user.id)


@router.post("/promote", response_model=LayeredMemoryResponse)
async def promote_memory(
    data: LayeredMemoryPromote,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """L1→L2 提升"""
    orchestrator = _get_orchestrator(db, current_user)

    l2 = await orchestrator.promote(data.uri)
    if not l2:
        raise HTTPException(status_code=404, detail="L1 记忆不存在")

    await db.commit()
    await db.refresh(l2)
    return l2


@router.post("/evict")
async def evict_memory(
    data: LayeredMemoryEvict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """清理指定会话的 L0 工作记忆"""
    orchestrator = _get_orchestrator(db, current_user)

    count = await orchestrator.evict(data.session_id)
    await db.commit()

    return {"message": f"已清理 {count} 条 L0 工作记忆", "count": count}


# ==================== 动态路径路由（必须放最后）====================


@router.get("/{uri:path}", response_model=LayeredMemoryResponse)
async def get_memory(
    uri: str,
    layer: int = Query(None, description="层级 0/1/2"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取指定记忆"""
    orchestrator = _get_orchestrator(db, current_user)

    if layer is not None:
        memory = await orchestrator.get(uri, layer)
    else:
        memory = await orchestrator.get_with_context(uri)

    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory


@router.put("/{uri:path}", response_model=LayeredMemoryResponse)
async def update_memory(
    uri: str,
    data: LayeredMemoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新记忆"""
    orchestrator = _get_orchestrator(db, current_user)

    l2 = await orchestrator.update(
        uri=uri,
        content=data.content,
        name=data.name,
        tags=data.tags,
        importance=data.importance,
        is_active=data.is_active,
        regenerate_tiers=data.regenerate_tiers,
    )
    if not l2:
        raise HTTPException(status_code=404, detail="记忆不存在")

    await db.commit()
    await db.refresh(l2)
    return l2


@router.delete("/{uri:path}", status_code=204)
async def delete_memory(
    uri: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除记忆（含 L0/L1/L2 全部层级）"""
    orchestrator = _get_orchestrator(db, current_user)

    # 先检查是否存在
    memory = await orchestrator.get(uri)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")

    await orchestrator.delete(uri)
    await db.commit()
