import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.core.permissions import PermissionChecker
from app.models.approval import Approval, ApprovalType
from app.models.models import Agent, Task, TaskDependency, TaskTemplate, User
from app.models.task_comment import TaskComment
from app.schemas.schemas import MessageResponse, TaskCreate, TaskResponse, TaskUpdate
from app.schemas.task_comment import (
    TaskCommentCreate,
    TaskCommentDetail,
    TaskCommentInDB,
    TaskCommentListResponse,
)

router = APIRouter(prefix="/tasks", tags=["任务管理"])


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    priority: str | None = None,
    task_type: str | None = None,
    assignee_id: int | None = None,
    creator_id: int | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务列表"""
    query = select(Task)

    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if task_type:
        query = query.where(Task.task_type == task_type)
    if assignee_id:
        query = query.where(Task.assignee_id == assignee_id)
    if creator_id:
        query = query.where(Task.creator_id == creator_id)

    # 权限过滤：非超管只能看自己的任务
    if not current_user.is_super_admin:
        query = query.where(
            (Task.creator_id == current_user.id) | (Task.assignee_id == current_user.id)
        )

    query = query.order_by(Task.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_task_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务统计"""
    stats = {}

    def _apply_user_filter(stmt):
        if not current_user.is_super_admin:
            stmt = stmt.where(
                (Task.creator_id == current_user.id)
                | (Task.assignee_id == current_user.id)
            )
        return stmt

    # 各状态数量
    for s in ["todo", "in_progress", "done", "cancelled"]:
        stmt = _apply_user_filter(select(func.count(Task.id)).where(Task.status == s))
        result = await db.execute(stmt)
        stats[s] = result.scalar() or 0

    # 总数
    stmt = _apply_user_filter(select(func.count(Task.id)))
    result = await db.execute(stmt)
    stats["total"] = result.scalar() or 0

    # 各优先级数量
    for p in ["low", "normal", "high", "urgent"]:
        stmt = _apply_user_filter(select(func.count(Task.id)).where(Task.priority == p))
        result = await db.execute(stmt)
        stats[f"priority_{p}"] = result.scalar() or 0

    # 我的任务
    result = await db.execute(
        select(func.count(Task.id)).where(
            (Task.assignee_id == current_user.id) | (Task.creator_id == current_user.id)
        )
    )
    stats["my_tasks"] = result.scalar() or 0

    return stats


@router.get("/analytics")
async def get_task_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(PermissionChecker("task:read")),
):
    """获取任务分析数据"""
    # 近7天完成趋势
    from sqlalchemy import text

    result = await db.execute(
        text(
            "SELECT DATE(completed_at) as date, COUNT(*) as count "
            "FROM tasks WHERE completed_at IS NOT NULL "
            "AND completed_at >= DATE('now', '-7 days') "
            "GROUP BY DATE(completed_at) ORDER BY date"
        )
    )
    completion_trend = [{"date": str(row[0]), "count": row[1]} for row in result]

    # 各类型任务数
    result = await db.execute(
        select(Task.task_type, func.count(Task.id)).group_by(Task.task_type)
    )
    type_distribution = {row[0]: row[1] for row in result}

    return {
        "completion_trend": completion_trend,
        "type_distribution": type_distribution,
    }


@router.get("/mine", response_model=list[TaskResponse])
async def get_my_tasks(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取我的任务（指派给我或我创建的）"""
    result = await db.execute(
        select(Task)
        .where(
            (Task.assignee_id == current_user.id) | (Task.creator_id == current_user.id)
        )
        .order_by(Task.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("task:create"))],
)
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建任务"""
    task = Task(
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority,
        task_type=task_data.task_type,
        agent_id=task_data.agent_id,
        assignee_id=task_data.assignee_id,
        due_at=task_data.due_at,
        input_data=task_data.input_data,
        creator_id=current_user.id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ============================================================
# 任务模板（固定路径，必须在 /{task_id} 之前）
# ============================================================


@router.get("/templates")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务模板列表"""
    result = await db.execute(
        select(TaskTemplate).order_by(TaskTemplate.usage_count.desc())
    )
    templates = result.scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "task_type": t.task_type,
            "input_data": t.input_data,
            "agent_id": t.agent_id,
            "usage_count": t.usage_count,
        }
        for t in templates
    ]


@router.post("/templates")
async def create_template(
    name: str,
    title: str,
    description: str | None = None,
    priority: str = "normal",
    task_type: str = "manual",
    agent_id: int | None = None,
    input_data: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建任务模板"""
    import json as _json

    template = TaskTemplate(
        name=name,
        title=title,
        description=description,
        priority=priority,
        task_type=task_type,
        agent_id=agent_id,
        input_data=_json.loads(input_data) if input_data else None,
        creator_id=current_user.id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return {"id": template.id, "name": template.name, "title": template.title}


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除任务模板"""
    result = await db.execute(
        select(TaskTemplate).where(TaskTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    await db.delete(template)
    await db.commit()
    return {"message": "模板已删除"}


@router.post(
    "/from-template/{template_id}",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """从模板创建任务"""
    result = await db.execute(
        select(TaskTemplate).where(TaskTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    template.usage_count = (template.usage_count or 0) + 1
    task = Task(
        title=template.title,
        description=template.description,
        priority=template.priority,
        task_type=template.task_type,
        input_data=template.input_data,
        agent_id=template.agent_id,
        creator_id=current_user.id,
        assignee_id=current_user.id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/dependencies/{dep_id}")
async def delete_task_dependency(
    dep_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除任务依赖"""
    result = await db.execute(select(TaskDependency).where(TaskDependency.id == dep_id))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="依赖关系不存在")
    await db.delete(dep)
    await db.commit()
    return {"message": "依赖已删除"}


@router.get("/workload")
async def get_workload(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    active_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.assignee_id == current_user.id,
            Task.status.in_(["todo", "in_progress", "pending_approval"]),
        )
    )
    active = active_result.scalar() or 0
    week_ago = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_ago -= timedelta(days=week_ago.weekday())
    done_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.assignee_id == current_user.id,
            Task.status == "done",
            Task.completed_at >= week_ago,
        )
    )
    done_this_week = done_result.scalar() or 0
    by_p = await db.execute(
        select(Task.priority, func.count(Task.id))
        .where(
            Task.assignee_id == current_user.id,
            Task.status.in_(["todo", "in_progress"]),
        )
        .group_by(Task.priority)
    )
    by_priority = {row[0]: row[1] for row in by_p.all()}
    return {
        "active_tasks": active,
        "done_this_week": done_this_week,
        "by_priority": by_priority,
    }


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务详情"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.put(
    "/{task_id}",
    response_model=TaskResponse,
    dependencies=[Depends(PermissionChecker("task:update"))],
)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task_data.title is not None:
        task.title = task_data.title
    if task_data.description is not None:
        task.description = task_data.description
    if task_data.status is not None:
        task.status = task_data.status
        if task_data.status == "in_progress" and not task.started_at:
            task.started_at = datetime.now(timezone.utc)
        if task_data.status in ["done", "cancelled"]:
            task.completed_at = datetime.now(timezone.utc)
    if task_data.priority is not None:
        task.priority = task_data.priority
    if task_data.assignee_id is not None:
        task.assignee_id = task_data.assignee_id
    if task_data.due_at is not None:
        task.due_at = task_data.due_at
    if task_data.output_data is not None:
        task.output_data = task_data.output_data
    if task_data.error_message is not None:
        task.error_message = task_data.error_message

    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", response_model=MessageResponse)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 权限检查：超管/组织管理员可删，创建者可删自己的，被指派者可删
    if not current_user.is_super_admin:
        if task.creator_id != current_user.id and task.assignee_id != current_user.id:
            if not (current_user.is_org_admin and current_user.organization_id):
                raise HTTPException(
                    status_code=403, detail="权限不足，只能删除自己创建或被指派的任务"
                )

    await db.delete(task)
    await db.commit()
    return MessageResponse(message="任务已删除")


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """开始执行任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "todo":
        raise HTTPException(status_code=400, detail="任务状态不允许启动")

    task.status = "in_progress"
    task.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: int,
    output_data: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """完成任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.status = "done"
    task.completed_at = datetime.now(timezone.utc)
    if output_data:
        task.output_data = output_data

    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: int,
    reason: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    if reason:
        task.error_message = reason

    await db.commit()
    await db.refresh(task)
    return task


# ========== 子任务 ==========


@router.post(
    "/{task_id}/subtasks",
    response_model=TaskResponse,
    dependencies=[Depends(PermissionChecker("task:create"))],
)
async def create_subtask(
    task_id: int,
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建子任务"""
    # 检查父任务存在
    result = await db.execute(select(Task).where(Task.id == task_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="父任务不存在")

    subtask = Task(
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority or parent.priority,
        task_type=task_data.task_type,
        parent_id=task_id,
        agent_id=task_data.agent_id,
        assignee_id=task_data.assignee_id or parent.assignee_id,
        due_at=task_data.due_at,
        input_data=task_data.input_data,
        creator_id=current_user.id,
    )
    db.add(subtask)
    await db.commit()
    await db.refresh(subtask)
    return subtask


@router.get("/{task_id}/subtasks", response_model=list[TaskResponse])
async def get_subtasks(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取子任务列表"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="父任务不存在")

    result = await db.execute(
        select(Task).where(Task.parent_id == task_id).order_by(Task.created_at.asc())
    )
    return result.scalars().all()


# ========== 评论 ==========


@router.get("/{task_id}/comments", response_model=TaskCommentListResponse)
async def get_task_comments(
    task_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务评论"""
    # 检查任务存在
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="任务不存在")

    count_result = await db.execute(
        select(func.count())
        .select_from(TaskComment)
        .where(TaskComment.task_id == task_id, not TaskComment.is_deleted)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(TaskComment)
        .where(TaskComment.task_id == task_id, not TaskComment.is_deleted)
        .order_by(TaskComment.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    comments = result.scalars().all()

    items = []
    for c in comments:
        item = TaskCommentDetail(
            id=c.id,
            task_id=c.task_id,
            user_id=c.user_id,
            content=c.content,
            parent_id=c.parent_id,
            mentions=c.mentions,
            created_at=c.created_at,
            updated_at=c.updated_at,
            is_deleted=c.is_deleted,
            user_name=c.user.display_name if c.user else None,
            user_avatar=c.user.avatar if c.user else None,
            replies=[],
        )
        items.append(item)

    return TaskCommentListResponse(items=items, total=total)


@router.post(
    "/{task_id}/comments",
    response_model=TaskCommentInDB,
    dependencies=[Depends(PermissionChecker("task:comment"))],
)
async def create_task_comment(
    task_id: int,
    data: TaskCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """添加任务评论"""
    # 检查任务存在
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="任务不存在")

    # 检查父评论
    if data.parent_id:
        result = await db.execute(
            select(TaskComment).where(TaskComment.id == data.parent_id)
        )
        parent_comment = result.scalar_one_or_none()
        if not parent_comment:
            raise HTTPException(status_code=400, detail="父评论不存在")
        if parent_comment.task_id != task_id:
            raise HTTPException(status_code=400, detail="父评论不属于该任务")

    comment = TaskComment(
        task_id=task_id,
        user_id=current_user.id,
        content=data.content,
        parent_id=data.parent_id,
        mentions=data.mentions,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


# ========== 批量操作 ==========


@router.post(
    "/batch/assign",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("task:update"))],
)
async def batch_assign_tasks(
    request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """批量分配任务"""
    task_ids = request.get("task_ids", [])
    assignee_id = request.get("assignee_id")

    if not task_ids or assignee_id is None:
        raise HTTPException(status_code=400, detail="参数不完整")

    result = await db.execute(select(Task).where(Task.id.in_(task_ids)))
    tasks = result.scalars().all()

    for task in tasks:
        task.assignee_id = assignee_id

    await db.commit()
    return MessageResponse(message=f"已分配 {len(tasks)} 个任务")


@router.post(
    "/batch/update",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("task:update"))],
)
async def batch_update_tasks(
    request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """批量更新任务"""
    task_ids = request.get("task_ids", [])
    updates = request.get("updates", {})

    if not task_ids or not updates:
        raise HTTPException(status_code=400, detail="参数不完整")

    result = await db.execute(select(Task).where(Task.id.in_(task_ids)))
    tasks = result.scalars().all()

    allowed_fields = ["status", "priority", "assignee_id"]
    for task in tasks:
        for field in allowed_fields:
            if field in updates:
                setattr(task, field, updates[field])

    await db.commit()
    return MessageResponse(message=f"已更新 {len(tasks)} 个任务")


# ========== 附件 ==========

ATTACHMENTS_DIR = Path("uploads/task_attachments")


@router.get("/{task_id}/attachments")
async def list_attachments(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取附件列表"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="任务不存在")

    task_dir = ATTACHMENTS_DIR / str(task_id)
    if not task_dir.exists():
        return {"attachments": []}

    attachments = []
    for f in task_dir.iterdir():
        if f.is_file():
            attachments.append(
                {
                    "id": f.stem,
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "created_at": datetime.fromtimestamp(
                        f.stat().st_ctime, tz=timezone.utc
                    ).isoformat(),
                }
            )

    return {"attachments": attachments}


@router.post("/{task_id}/attachments")
async def upload_attachment(
    task_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """上传附件"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="任务不存在")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 限制文件大小 (10MB)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    task_dir = ATTACHMENTS_DIR / str(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    # 生成唯一文件名
    file_id = str(uuid.uuid4())[:8]
    safe_name = f"{file_id}_{file.filename}"
    file_path = task_dir / safe_name

    with open(file_path, "wb") as f:
        f.write(content)

    return {
        "id": file_id,
        "filename": file.filename,
        "saved_as": safe_name,
        "size": len(content),
        "message": "上传成功",
    }


@router.delete("/{task_id}/attachments/{filename}")
async def delete_attachment(
    task_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除附件"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="任务不存在")

    file_path = ATTACHMENTS_DIR / str(task_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="附件不存在")

    # 安全检查：确保文件在附件目录内
    try:
        file_path.resolve().relative_to(ATTACHMENTS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文件路径")

    file_path.unlink()
    return MessageResponse(message="附件已删除")


# ============================================================
# AI 执行集成
# ============================================================


@router.post("/{task_id}/send-to-ai")
async def send_task_to_ai(
    task_id: int,
    agent_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """将任务发送给 AI Agent 执行"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Determine agent
    agent = None
    if agent_id:
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
    elif task.agent_id:
        agent_result = await db.execute(select(Agent).where(Agent.id == task.agent_id))
        agent = agent_result.scalar_one_or_none()

    if not agent:
        # Use default agent
        agent_result = await db.execute(
            select(Agent).where(Agent.creator_id == current_user.id).limit(1)
        )
        agent = agent_result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=400, detail="没有可用的 Agent，请先创建 Agent 或指定 agent_id"
        )

    task.status = "in_progress"
    task.started_at = datetime.now(tz=timezone.utc)
    task.agent_id = agent.id
    await db.commit()
    await db.refresh(task)

    return {
        "message": "任务已发送给 AI 执行",
        "task_id": task.id,
        "agent_id": agent.id,
        "agent_name": agent.display_name or agent.name,
    }


@router.post("/{task_id}/ai-suggest-assignee")
async def ai_suggest_assignee(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """AI 建议任务指派"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Simple heuristic: find users with fewest tasks
    user_result = await db.execute(select(User).where(User.is_active).limit(10))
    users = user_result.scalars().all()

    suggestions = []
    for u in users[:5]:
        count_result = await db.execute(
            select(func.count(Task.id)).where(
                Task.assignee_id == u.id,
                Task.status.in_(["todo", "in_progress"]),
            )
        )
        count = count_result.scalar() or 0
        suggestions.append(
            {
                "user_id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "current_task_count": count,
            }
        )

    suggestions.sort(key=lambda x: x["current_task_count"])
    return {"task_id": task_id, "suggestions": suggestions}


@router.post("/{task_id}/ai-suggest-split")
async def ai_suggest_split(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """AI 建议拆分任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Simple heuristic: split by keywords in description
    subtasks = []
    if task.description:
        lines = [
            line.strip("- ").strip()
            for line in task.description.split("\n")
            if line.strip().startswith("-")
        ]
        if lines:
            for i, line in enumerate(lines[:5]):
                subtasks.append(
                    {"title": line, "priority": task.priority, "suggested_order": i + 1}
                )

    if not subtasks:
        subtasks = [
            {
                "title": f"{task.title} - 分析阶段",
                "priority": "high",
                "suggested_order": 1,
            },
            {
                "title": f"{task.title} - 执行阶段",
                "priority": task.priority,
                "suggested_order": 2,
            },
            {
                "title": f"{task.title} - 验证阶段",
                "priority": "normal",
                "suggested_order": 3,
            },
        ]

    return {"task_id": task_id, "suggested_subtasks": subtasks}


# ============================================================
# 任务依赖
# ============================================================


@router.get("/{task_id}/dependencies")
async def get_task_dependencies(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务依赖"""
    result = await db.execute(
        select(TaskDependency).where(TaskDependency.task_id == task_id)
    )
    deps = result.scalars().all()

    dep_list = []
    for d in deps:
        dep_task = await db.execute(select(Task).where(Task.id == d.depends_on_id))
        t = dep_task.scalar_one_or_none()
        dep_list.append(
            {
                "id": d.id,
                "task_id": d.task_id,
                "depends_on_id": d.depends_on_id,
                "depends_on_title": t.title if t else "(已删除)",
                "depends_on_status": t.status if t else "unknown",
            }
        )
    return dep_list


@router.post("/{task_id}/dependencies/{depends_on_id}")
async def add_task_dependency(
    task_id: int,
    depends_on_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """添加任务依赖"""
    if task_id == depends_on_id:
        raise HTTPException(status_code=400, detail="任务不能依赖自身")

    # Check both tasks exist
    for tid in [task_id, depends_on_id]:
        r = await db.execute(select(Task).where(Task.id == tid))
        if not r.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"任务 {tid} 不存在")

    # Check duplicate
    existing = await db.execute(
        select(TaskDependency).where(
            TaskDependency.task_id == task_id,
            TaskDependency.depends_on_id == depends_on_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该依赖关系已存在")

    dep = TaskDependency(task_id=task_id, depends_on_id=depends_on_id)
    db.add(dep)
    await db.commit()
    return {"id": dep.id, "task_id": task_id, "depends_on_id": depends_on_id}


@router.get("/{task_id}/dependencies/check")
async def check_task_dependencies(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """检查任务依赖是否满足"""
    result = await db.execute(
        select(TaskDependency).where(TaskDependency.task_id == task_id)
    )
    deps = result.scalars().all()

    blocked = []
    satisfied = []
    for d in deps:
        dep_task = await db.execute(select(Task).where(Task.id == d.depends_on_id))
        t = dep_task.scalar_one_or_none()
        if t and t.status != "done":
            blocked.append(
                {
                    "depends_on_id": d.depends_on_id,
                    "depends_on_title": t.title,
                    "status": t.status,
                }
            )
        else:
            satisfied.append(d.depends_on_id)

    return {
        "can_start": len(blocked) == 0,
        "blocked_by": blocked,
        "satisfied": satisfied,
    }


# ============================================================
# 任务审批
# ============================================================


@router.post("/{task_id}/submit")
async def submit_task_for_approval(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """提交任务审批"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status != "todo":
        raise HTTPException(status_code=400, detail="只有待办状态的任务才能提交审批")

    task.status = "pending_approval"
    approval = Approval(
        approval_type=ApprovalType.TASK_APPROVAL,
        status="pending",
        title=f"任务审批: {task.title}",
        description=task.description or "",
        requester_id=current_user.id,
        resource_type="task",
        resource_id=str(task_id),
        target_scope="org",
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    return {"message": "已提交审批", "approval_id": approval.id, "task_id": task_id}


@router.post("/{task_id}/approve")
async def approve_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """审批通过任务"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status != "pending_approval":
        raise HTTPException(status_code=400, detail="任务不在待审批状态")

    task.status = "todo"
    await db.commit()
    return {"message": "任务已审批通过", "task_id": task_id}


@router.post("/{task_id}/reject")
async def reject_task(
    task_id: int,
    reject_reason: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """驳回任务"""
    if not current_user.is_super_admin and not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status != "pending_approval":
        raise HTTPException(status_code=400, detail="任务不在待审批状态")

    task.status = "todo"
    task.description = (
        task.description or ""
    ) + f"\n[驳回原因]: {reject_reason or '未说明'}"
    await db.commit()
    return {"message": "任务已驳回", "task_id": task_id}


# ============================================================
# 任务拆分、进度、工作量
# ============================================================


@router.post("/{task_id}/split")
async def split_task(
    task_id: int,
    subtasks: list[str],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """拆分任务为子任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    created = []
    for _i, title in enumerate(subtasks[:10]):  # 最多10个子任务
        sub = Task(
            title=title,
            priority=task.priority,
            parent_id=task_id,
            creator_id=current_user.id,
            assignee_id=task.assignee_id,
            task_type=task.task_type,
            agent_id=task.agent_id,
        )
        db.add(sub)
        created.append(title)

    await db.commit()
    return {"message": f"已创建 {len(created)} 个子任务", "subtasks": created}


@router.post("/{task_id}/progress")
async def update_progress(
    task_id: int,
    progress: int,
    comment: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新任务进度（0-100）"""
    if not 0 <= progress <= 100:
        raise HTTPException(status_code=400, detail="进度必须在 0-100 之间")

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Store progress in output_data
    task.output_data = task.output_data or {}
    task.output_data["progress"] = progress
    if comment:
        task.output_data["progress_comment"] = comment
    if progress == 100:
        task.status = "done"
        task.completed_at = datetime.now(tz=timezone.utc)
    elif progress > 0 and task.status == "todo":
        task.status = "in_progress"
        task.started_at = task.started_at or datetime.now(tz=timezone.utc)

    await db.commit()
    return {"task_id": task_id, "progress": progress, "status": task.status}
