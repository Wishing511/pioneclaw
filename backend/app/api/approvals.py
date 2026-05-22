"""
审批流程 API

用户提交 Skill/文档共享请求，管理员在任务页面审批。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.core.permissions import can_manage_approval
from app.models import Approval, Skill, User
from app.models.approval import ApprovalStatus
from app.schemas import MessageResponse
from app.schemas.approval import (
    ApprovalCreate,
    ApprovalResponse,
    ApprovalReview,
)

router = APIRouter(prefix="/approvals", tags=["审批"])


@router.post("", response_model=ApprovalResponse, status_code=status.HTTP_201_CREATED)
async def create_approval(
    data: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """提交审批请求"""
    # 验证资源存在
    if data.resource_type == "skill":
        result = await db.execute(
            select(Skill).where(Skill.id == int(data.resource_id))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(status_code=404, detail="Skill 不存在")
        # 只有创建者可以提交
        if skill.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="只能提交自己创建的 Skill")
        # 检查当前 scope
        if data.target_scope == "org" and skill.scope != "user":
            raise HTTPException(
                status_code=400, detail="只有用户级 Skill 可以提交为组织级"
            )
        if data.target_scope == "system" and skill.scope not in ("user", "org"):
            raise HTTPException(
                status_code=400, detail="只有用户级或组织级 Skill 可以提交为系统级"
            )

    # 检查是否有重复的待审批请求
    result = await db.execute(
        select(Approval).where(
            Approval.resource_type == data.resource_type,
            Approval.resource_id == data.resource_id,
            Approval.target_scope == data.target_scope,
            Approval.status == ApprovalStatus.PENDING,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="已有待审批的相同请求")

    approval = Approval(
        approval_type=data.approval_type,
        status=ApprovalStatus.PENDING,
        title=data.title,
        description=data.description,
        requester_id=current_user.id,
        requester_org_id=current_user.organization_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        target_scope=data.target_scope,
        target_org_id=data.target_org_id or current_user.organization_id,
        extra_data=data.metadata,
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    return approval


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    status_filter: str | None = None,
    scope: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    获取审批列表

    - 普通用户：看到自己提交的
    - 组织管理员：看到本组织待审批的
    - 超管：看到所有待审批的
    """
    query = select(Approval)

    if status_filter:
        query = query.where(Approval.status == status_filter)
    if scope:
        query = query.where(Approval.target_scope == scope)

    if current_user.is_super_admin:
        pass  # 超管看所有
    elif current_user.is_org_admin:
        # 组织管理员：看本组织的 + 自己提交的
        query = query.where(
            (Approval.target_org_id == current_user.organization_id)
            | (Approval.requester_id == current_user.id)
        )
    else:
        # 普通用户：只看自己提交的
        query = query.where(Approval.requester_id == current_user.id)

    from sqlalchemy.orm import joinedload

    query = (
        query.options(joinedload(Approval.requester))
        .order_by(Approval.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.unique().scalars().all()


@router.get("/pending-count")
async def get_pending_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取待审批数量"""
    query = (
        select(func.count())
        .select_from(Approval)
        .where(Approval.status == ApprovalStatus.PENDING)
    )

    if current_user.is_super_admin:
        pass  # 所有
    elif current_user.is_org_admin:
        query = query.where(Approval.target_org_id == current_user.organization_id)
    else:
        query = query.where(Approval.requester_id == current_user.id)

    result = await db.execute(query)
    count = result.scalar() or 0
    return {"pending_count": count}


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取审批详情"""
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    return approval


@router.post("/{approval_id}/review", response_model=ApprovalResponse)
async def review_approval(
    approval_id: int,
    data: ApprovalReview,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """审批（批准/拒绝）"""
    result = await db.execute(
        select(Approval)
        .where(Approval.id == approval_id)
        .options(joinedload(Approval.requester))
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail="该审批已处理")

    # 权限检查
    if not can_manage_approval(
        current_user, approval.target_scope, approval.target_org_id
    ):
        raise HTTPException(status_code=403, detail="无权审批此请求")

    # 更新状态
    approval.status = (
        ApprovalStatus.APPROVED if data.approved else ApprovalStatus.REJECTED
    )
    approval.reviewer_id = current_user.id
    approval.reviewed_at = datetime.now(tz=timezone.utc)
    approval.review_comment = data.review_comment

    # 如果批准，执行资源 scope 变更
    if data.approved and approval.resource_type == "skill":
        skill_result = await db.execute(
            select(Skill).where(Skill.id == int(approval.resource_id))
        )
        skill = skill_result.scalar_one_or_none()
        if skill:
            if approval.target_scope == "org":
                skill.scope = "org"
                skill.organization_id = approval.target_org_id
            elif approval.target_scope == "system":
                skill.scope = "system"
                skill.is_public = True

    await db.commit()
    await db.refresh(approval)
    return approval


@router.post("/{approval_id}/cancel", response_model=MessageResponse)
async def cancel_approval(
    approval_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消审批"""
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")

    if approval.requester_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能取消自己提交的审批")

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail="只能取消待审批的请求")

    approval.status = ApprovalStatus.CANCELLED
    await db.commit()
    return MessageResponse(message="审批已取消")
