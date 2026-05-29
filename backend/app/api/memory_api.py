"""
Memory API - 记忆管理接口

基于 fish_memory 模块，提供记忆文件的 CRUD、搜索、统计功能。
所有记忆以 .md 文件形式存储，使用 YAML frontmatter + MEMORY.md 索引。
"""

import functools
import logging
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.memory import (
    ListOptions,
    MemoryEntry,
    MemoryFailure,
    MemoryManage,
    MemoryMetadata,
    MemoryResponse,
    MemoryResult,
    MemoryType,
    SearchOptions,
    create_memory_manager,
    get_current_memory_manager,
)
from app.modules.memory.errors import MemoryPermissionError, SecurityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["记忆管理"])

# 记忆文件存放目录
MEMORY_ROOT = Path(__file__).resolve().parent.parent.parent / "memory"


# ==================== 工具函数 ====================

def _get_mm() -> MemoryManage:
    """获取或初始化 MemoryManage 单例。"""
    mm = get_current_memory_manager()
    if mm is None:
        mm = create_memory_manager(str(MEMORY_ROOT))
    return mm


def _protected(operation: str):
    """装饰器：捕获操作异常，根据异常类型返回适当的 HTTP 状态码。

    所有路由为 def（同步），MemoryManage 内部的同步文件 I/O
    由 FastAPI 自动在线程池中运行，不会阻塞事件循环。
    """

    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except HTTPException:
                raise
            except SecurityError as e:
                raise HTTPException(status_code=403, detail=str(e))
            except MemoryPermissionError as e:
                raise HTTPException(
                    status_code=403,
                    detail=f"权限不足 ({operation}): {e}",
                )
            except PermissionError as e:
                raise HTTPException(
                    status_code=403,
                    detail=f"权限不足 ({operation}): {e}",
                )
            except FileNotFoundError as e:
                raise HTTPException(
                    status_code=404,
                    detail=f"文件不存在 ({operation}): {e}",
                )
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


def _unwrap(response: MemoryResponse):
    """解包 MemoryResponse，成功返回 data，失败抛 HTTPException。"""
    if isinstance(response, MemoryResult) and response.success:
        return response.data
    if isinstance(response, MemoryFailure):
        err = response.error
        raise HTTPException(
            status_code=400,
            detail=f"{err.message}" if err else "记忆操作失败",
        )
    return response


def _entry_to_dict(entry: MemoryEntry) -> dict:
    """将 MemoryEntry 转为前端友好的字典。"""
    return {
        "id": entry.id,
        "filename": entry.filename,
        "name": entry.name,
        "description": entry.description,
        "type": entry.type.value,
        "content": entry.content,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        "freshness": entry.freshness,
        "is_stale": entry.is_stale,
        "tags": entry.tags,
    }


# ==================== 请求模型 ====================

class MemoryCreateRequest(BaseModel):
    """创建记忆请求"""
    content: str
    type: str = "user"  # user / feedback / project / reference
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    upsert: bool = False  # POST 语义是创建，更新请用 PUT /memory/{filename}


class MemoryUpdateRequest(BaseModel):
    """更新记忆请求"""
    content: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class MemorySearchRequest(BaseModel):
    """搜索记忆请求"""
    keyword: str
    type: Optional[str] = None
    limit: int = 15


# ==================== API 端点 ====================


@router.get("")
@_protected("list")
def list_memories(
    type: Optional[str] = Query(None, description="按类型过滤"),
    sort_by: str = Query("updatedAt", description="排序字段: name, createdAt, updatedAt"),
    order: str = Query("desc", description="排序方向: asc, desc"),
    limit: Optional[int] = Query(None, description="返回条数上限"),
    offset: int = Query(0, description="偏移量"),
    current_user: User = Depends(get_current_active_user),
):
    """获取所有记忆列表，支持按类型过滤和排序。"""
    mm = _get_mm()

    mem_type = None
    if type:
        try:
            mem_type = MemoryType(type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的记忆类型: {type}")

    opts = ListOptions(type=mem_type, sort_by=sort_by, order=order, limit=limit, offset=offset)
    response = mm.list(opts)
    entries = _unwrap(response)

    return {
        "entries": [_entry_to_dict(e) for e in entries],
        "total": len(entries),
    }


@router.get("/index")
@_protected("get_index")
def get_index(
    current_user: User = Depends(get_current_active_user),
):
    """获取 MEMORY.md 索引内容和解析后的条目列表。"""
    mm = _get_mm()
    index_entries = mm.index.get_entries()

    # 读取 MEMORY.md 原始内容
    index_path = MEMORY_ROOT / "MEMORY.md"
    content = ""
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")

    return {
        "content": content,
        "entries": [
            {"filename": e.filename, "description": e.description, "path": e.path}
            for e in index_entries
        ],
    }


@router.get("/stats")
@_protected("get_stats")
def get_stats(
    current_user: User = Depends(get_current_active_user),
):
    """获取记忆系统统计数据。"""
    mm = _get_mm()
    entries = mm.store.get_all_files()
    index_entries = mm.index.get_entries()

    by_type = {}
    for e in entries:
        t = e.type.value
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "total_files": len(entries),
        "index_entries": len(index_entries),
        "by_type": by_type,
    }


@router.get("/{filename:path}")
@_protected("get_file")
def get_memory(
    filename: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取单个记忆文件的完整内容和元数据。"""
    mm = _get_mm()
    try:
        entry = mm.store.read_file(filename)
    except Exception:
        raise HTTPException(status_code=404, detail=f"记忆文件不存在: {filename}")

    return _entry_to_dict(entry)


@router.post("")
@_protected("create")
def create_memory(
    request: MemoryCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """创建一条新记忆（自动写入文件 + 更新索引）。"""
    mm = _get_mm()

    try:
        mem_type = MemoryType(request.type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的记忆类型: {request.type}")

    meta = MemoryMetadata(
        name=request.name or "",
        description=request.description or "",
        type=mem_type,
        tags=request.tags or [],
    )

    response = mm.save(request.content, request.type, meta, upsert=request.upsert)

    if isinstance(response, MemoryFailure):
        err = response.error
        raise HTTPException(
            status_code=400,
            detail=f"{err.code}: {err.message}" if err else "创建记忆失败",
        )

    entry = response.data
    return _entry_to_dict(entry)


@router.put("/{filename:path}")
@_protected("update")
def update_memory(
    filename: str,
    request: MemoryUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """更新已有记忆文件的内容和元数据。"""
    mm = _get_mm()

    meta = MemoryMetadata(
        name=request.name or "",
        description=request.description or "",
        type=MemoryType.USER,
        tags=request.tags or [],
    )

    response = mm.update(filename, request.content, meta)

    if isinstance(response, MemoryFailure):
        err = response.error
        raise HTTPException(
            status_code=400,
            detail=f"{err.code}: {err.message}" if err else "更新记忆失败",
        )

    return _entry_to_dict(response.data)


@router.delete("/{filename:path}")
@_protected("delete")
def delete_memory(
    filename: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除记忆文件并移除索引条目。"""
    mm = _get_mm()
    response = mm.delete(filename)

    if isinstance(response, MemoryFailure):
        err = response.error
        raise HTTPException(
            status_code=400,
            detail=f"{err.code}: {err.message}" if err else "删除记忆失败",
        )

    return {"success": True, "filename": filename}


@router.post("/search")
@_protected("search")
def search_memory(
    request: MemorySearchRequest,
    current_user: User = Depends(get_current_active_user),
):
    """全文搜索记忆（按关键词在文件内容中匹配）。"""
    mm = _get_mm()

    mem_type = None
    if request.type:
        try:
            mem_type = MemoryType(request.type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的记忆类型: {request.type}")

    opts = SearchOptions(type=mem_type, limit=request.limit)
    response = mm.search(request.keyword, opts)
    entries = _unwrap(response)

    return {
        "entries": [_entry_to_dict(e) for e in entries],
        "total": len(entries),
        "keyword": request.keyword,
    }
