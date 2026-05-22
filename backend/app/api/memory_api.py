"""
Memory API - 记忆管理接口

提供记忆的读写、搜索、统计等功能
"""

import functools
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.agent.memory import (
    MemorySource,
    MemoryStore,
    get_memory_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["记忆管理"])


# ==================== 异常保护装饰器 ====================


def _protected(operation: str):
    """装饰器：捕获 MemoryStore 操作中的异常，统一返回 500"""

    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except OSError as e:
                logger.error(f"Memory {operation} I/O error: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"记忆文件读写失败 ({operation}): {e}",
                )
            except Exception as e:
                logger.error(f"Memory {operation} unexpected error: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"记忆操作失败 ({operation}): {e}",
                )

        return wrapper

    return decorator


# ==================== 请求模型 ====================


class AppendMemoryRequest(BaseModel):
    """追加记忆请求"""

    source: str
    content: str
    date: str | None = None


class BatchAppendRequest(BaseModel):
    """批量追加请求"""

    source: str
    entries: list[str]


class SearchMemoryRequest(BaseModel):
    """搜索记忆请求"""

    keywords: list[str]
    max_results: int = 15
    match_mode: str = "or"  # "or" 或 "and"


class DeleteMemoryRequest(BaseModel):
    """删除记忆请求"""

    line_numbers: list[int]


class ImportMemoryRequest(BaseModel):
    """导入记忆请求"""

    content: str
    source: str = "import"


class UpdateMemoryContentRequest(BaseModel):
    """全量更新记忆内容请求"""

    content: str


# ==================== 依赖 ====================


def get_store() -> MemoryStore:
    """获取记忆存储实例"""
    # 使用工作区下的 memory 目录
    memory_dir = Path.cwd() / "memory"
    return get_memory_store(memory_dir)


# ==================== API 端点 ====================


@router.get("")
@_protected("get_info")
async def get_memory_info(
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """获取记忆概览"""
    stats = store.get_stats()
    return {
        "total_entries": stats.total_entries,
        "sources": stats.sources,
        "date_range": stats.date_range,
        "total_chars": stats.total_chars,
    }


@router.get("/stats")
@_protected("get_stats")
async def get_stats(
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """获取详细统计"""
    stats = store.get_stats()
    return {
        "total_entries": stats.total_entries,
        "sources": stats.sources,
        "date_range": stats.date_range,
        "oldest_date": stats.oldest_date,
        "newest_date": stats.newest_date,
        "total_chars": stats.total_chars,
    }


@router.get("/recent")
@_protected("get_recent")
async def get_recent(
    count: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """获取最近 N 条记忆"""
    entries = store.get_recent_entries(count)
    return {
        "entries": [e.to_dict() for e in entries],
        "total": len(entries),
    }


@router.get("/line/{line_number}")
@_protected("get_line")
async def get_line(
    line_number: int,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """获取单条记忆"""
    entry = store.get_entry(line_number)

    if not entry:
        raise HTTPException(status_code=404, detail="Memory entry not found")

    return entry.to_dict()


@router.get("/lines")
@_protected("get_lines")
async def get_lines(
    start: int = Query(1, ge=1),
    end: int = Query(10, ge=1),
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """获取多条记忆"""
    entries = store.get_entries(start, end)
    return {
        "entries": [e.to_dict() for e in entries],
        "total": len(entries),
    }


@router.post("/search")
@router.post("/search")
@_protected("search")
async def search_memory(
    request: SearchMemoryRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """搜索记忆"""
    if request.match_mode not in ("or", "and"):
        raise HTTPException(status_code=400, detail="match_mode must be 'or' or 'and'")

    entries = store.search_entries(
        request.keywords,
        request.max_results,
        request.match_mode,
    )

    return {
        "entries": [e.to_dict() for e in entries],
        "total": len(entries),
        "keywords": request.keywords,
        "match_mode": request.match_mode,
    }


@router.post("/append")
@_protected("append")
async def append_memory(
    request: AppendMemoryRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """追加一条记忆"""
    line_number = store.append_entry(
        source=request.source,
        content=request.content,
        date=request.date,
    )

    return {
        "success": True,
        "line_number": line_number,
    }


@router.post("/append-batch")
@_protected("append_batch")
async def append_batch(
    request: BatchAppendRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """批量追加记忆"""
    line_numbers = store.append_entries(
        source=request.source,
        entries=request.entries,
    )

    return {
        "success": True,
        "count": len(line_numbers),
        "line_numbers": line_numbers,
    }


@router.delete("/lines")
@_protected("delete_lines")
async def delete_lines(
    request: DeleteMemoryRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """删除指定行号的记忆"""
    deleted = store.delete_lines(request.line_numbers)

    return {
        "success": True,
        "deleted_count": deleted,
    }


@router.delete("/line/{line_number}")
@_protected("delete_line")
async def delete_line(
    line_number: int,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """删除单条记忆"""
    success = store.delete_entry(line_number)

    if not success:
        raise HTTPException(status_code=404, detail="Memory entry not found")

    return {"success": True, "message": f"Line {line_number} deleted"}


@router.delete("/clear")
@_protected("clear")
async def clear_memory(
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """清空所有记忆"""
    count = store.clear()

    return {
        "success": True,
        "cleared_count": count,
    }


@router.get("/export")
@_protected("export")
async def export_memory(
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """导出记忆"""
    content = store.export_to_text()

    return {
        "content": content,
        "line_count": store.get_paragraph_count(),
    }


@router.post("/import")
@_protected("import")
async def import_memory(
    request: ImportMemoryRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """导入记忆"""
    count = store.import_from_text(
        content=request.content,
        source=request.source,
    )

    return {
        "success": True,
        "imported_count": count,
    }


@router.put("/content")
@_protected("update_content")
async def update_memory_content(
    request: UpdateMemoryContentRequest,
    current_user: User = Depends(get_current_active_user),
    store: MemoryStore = Depends(get_store),
):
    """全量更新记忆内容（替换整个 MEMORY.md）"""
    store.write_all(request.content)
    para_count = store.get_paragraph_count()
    return {
        "success": True,
        "line_count": para_count,
    }


@router.get("/sources")
@_protected("get_sources")
async def get_sources(
    current_user: User = Depends(get_current_active_user),
):
    """获取可用的记忆来源列表"""
    return {
        "sources": [{"value": s.value, "label": s.value} for s in MemorySource],
    }
