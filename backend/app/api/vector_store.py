"""
VectorStore API 端点
向量存储管理接口
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User

from ..modules.agent.vector_store import get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vector-store", tags=["VectorStore"])


def _protected(operation: str):
    """装饰器：捕获 VectorStore 操作中的异常，统一返回 500"""

    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"VectorStore {operation} error: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"向量存储操作失败 ({operation}): {e}",
                )

        return wrapper

    return decorator


# ==================== 请求模型 ====================


class AddEntryRequest(BaseModel):
    """添加条目请求"""

    content: str
    metadata: dict[str, Any] | None = None
    source_type: str = "knowledge"
    source_id: str | None = None
    generate_embedding: bool = True


class AddBatchRequest(BaseModel):
    """批量添加请求"""

    entries: list[dict[str, Any]]
    source_type: str = "knowledge"
    source_id: str | None = None


class UpdateEntryRequest(BaseModel):
    """更新条目请求"""

    content: str | None = None
    metadata: dict[str, Any] | None = None
    generate_embedding: bool = True


class SearchRequest(BaseModel):
    """搜索请求"""

    query: str
    top_k: int = 5
    source_type: str | None = None
    source_id: str | None = None
    min_score: float = 0.5
    hybrid: bool = False
    keyword_weight: float = 0.3


# ==================== API 端点 ====================


@router.get("/stats")
@_protected("get_stats")
async def get_stats(
    current_user: User = Depends(get_current_active_user),
):
    """获取向量存储统计信息"""
    store = get_vector_store()
    return store.get_stats()


@router.get("/count")
@_protected("get_count")
async def get_count(
    source_type: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """获取条目数量"""
    store = get_vector_store()
    return {"count": store.count(source_type)}


@router.get("/source-ids")
@_protected("get_source_ids")
async def get_source_ids(
    source_type: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """获取所有来源 ID"""
    store = get_vector_store()
    return {"source_ids": store.get_source_ids(source_type)}


@router.post("/add")
@_protected("add")
async def add_entry(
    request: AddEntryRequest,
    current_user: User = Depends(get_current_active_user),
):
    """添加向量条目"""
    store = get_vector_store()
    entry_id = store.add(
        content=request.content,
        metadata=request.metadata,
        source_type=request.source_type,
        source_id=request.source_id,
        generate_embedding=request.generate_embedding,
    )
    return {"success": True, "id": entry_id}


@router.post("/add-batch")
@_protected("add_batch")
async def add_batch(
    request: AddBatchRequest,
    current_user: User = Depends(get_current_active_user),
):
    """批量添加向量条目"""
    store = get_vector_store()
    entry_ids = store.add_batch(
        entries=request.entries,
        source_type=request.source_type,
        source_id=request.source_id,
    )
    return {"success": True, "ids": entry_ids, "count": len(entry_ids)}


@router.get("/{entry_id}")
@_protected("get_entry")
async def get_entry(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取向量条目"""
    store = get_vector_store()
    entry = store.get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@router.put("/{entry_id}")
@_protected("update_entry")
async def update_entry(
    entry_id: str,
    request: UpdateEntryRequest,
    current_user: User = Depends(get_current_active_user),
):
    """更新向量条目"""
    store = get_vector_store()
    success = store.update(
        entry_id=entry_id,
        content=request.content,
        metadata=request.metadata,
        generate_embedding=request.generate_embedding,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"success": True}


@router.delete("/{entry_id}")
@_protected("delete_entry")
async def delete_entry(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除向量条目"""
    store = get_vector_store()
    success = store.delete(entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"success": True}


@router.delete("/by-source")
@_protected("delete_by_source")
async def delete_by_source(
    source_type: str | None = None,
    source_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """按来源删除向量条目"""
    store = get_vector_store()
    deleted = store.delete_by_source(source_type, source_id)
    return {"success": True, "deleted": deleted}


@router.post("/search")
@_protected("search")
async def search(
    request: SearchRequest,
    current_user: User = Depends(get_current_active_user),
):
    """搜索向量条目"""
    store = get_vector_store()
    if request.hybrid:
        results = store.search_hybrid(
            query=request.query,
            top_k=request.top_k,
            source_type=request.source_type,
            source_id=request.source_id,
            keyword_weight=request.keyword_weight,
            min_score=request.min_score,
        )
    else:
        results = store.search(
            query=request.query,
            top_k=request.top_k,
            source_type=request.source_type,
            source_id=request.source_id,
            min_score=request.min_score,
        )
    return {
        "success": True,
        "results": [r.to_dict() for r in results],
        "count": len(results),
    }


@router.get("/search/quick")
@_protected("quick_search")
async def quick_search(
    query: str,
    top_k: int = 5,
    source_type: str | None = None,
    min_score: float = 0.5,
    current_user: User = Depends(get_current_active_user),
):
    """快速搜索（GET 请求）"""
    store = get_vector_store()
    results = store.search(
        query=query,
        top_k=top_k,
        source_type=source_type,
        min_score=min_score,
    )
    return {
        "success": True,
        "results": [r.to_dict() for r in results],
        "count": len(results),
    }
