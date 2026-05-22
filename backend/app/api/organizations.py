"""
组织管理 API
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models import Organization, User
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationInDB,
    OrganizationListResponse,
    OrganizationSimple,
    OrganizationTree,
    OrganizationUpdate,
)

router = APIRouter(prefix="/organizations", tags=["组织管理"])


def build_org_tree(
    orgs: list[Organization], parent_id: str = None
) -> list[OrganizationTree]:
    """构建组织树"""
    tree = []
    children = [o for o in orgs if o.parent_id == parent_id]
    for org in children:
        node_dict = {
            "id": org.id,
            "name": org.name,
            "code": org.code,
            "description": org.description,
            "parent_id": org.parent_id,
            "level": org.level,
            "path": org.path,
            "manager_id": org.manager_id,
            "type": org.type,
            "status": org.status,
            "meta_data": org.meta_data,
            "created_at": org.created_at,
            "updated_at": org.updated_at,
        }
        node = OrganizationTree(**node_dict)
        node.children = build_org_tree(orgs, org.id)
        node.user_count = 0
        tree.append(node)
    return tree


@router.get("/", response_model=OrganizationListResponse)
async def list_organizations(
    status_filter: str | None = Query(None, alias="status"),
    type: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取组织列表"""
    query = select(Organization)
    count_query = select(func.count()).select_from(Organization)

    if status_filter:
        query = query.where(Organization.status == status_filter)
        count_query = count_query.where(Organization.status == status_filter)
    if type:
        query = query.where(Organization.type == type)
        count_query = count_query.where(Organization.type == type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.order_by(Organization.level, Organization.name).offset(skip).limit(limit)
    )
    result = await db.execute(query)
    orgs = result.scalars().all()

    return OrganizationListResponse(
        items=[OrganizationInDB.model_validate(o) for o in orgs],
        total=total,
    )


@router.get("/tree", response_model=list[OrganizationTree])
async def get_organization_tree(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取组织树"""
    result = await db.execute(
        select(Organization).order_by(Organization.level, Organization.name)
    )
    orgs = result.scalars().all()
    return build_org_tree(list(orgs))


@router.get("/simple", response_model=list[OrganizationSimple])
async def list_organizations_simple(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取简化组织列表（用于下拉选择）"""
    result = await db.execute(
        select(Organization)
        .where(Organization.status == "active")
        .order_by(Organization.level, Organization.name)
    )
    orgs = result.scalars().all()
    return [OrganizationSimple.model_validate(o) for o in orgs]


@router.post("/", response_model=OrganizationInDB, status_code=status.HTTP_201_CREATED)
async def create_organization(
    data: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建组织"""
    # 权限检查：只有超管和组织管理员可以创建
    if not (current_user.is_super_admin or current_user.is_org_admin):
        raise HTTPException(status_code=403, detail="权限不足，需要管理员权限")

    # 限制：只能有一个 company（level=1 的顶级组织）
    if data.type == "company" or (not data.parent_id and data.type != "department"):
        result = await db.execute(
            select(Organization).where(
                Organization.type == "company", Organization.level == 1
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail="已存在公司级组织，只能有一个 company"
            )

    # 检查 code 唯一性
    result = await db.execute(
        select(Organization).where(Organization.code == data.code)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="组织代码已存在")

    # 确定层级
    level = 1
    parent_path = ""
    if data.parent_id:
        result = await db.execute(
            select(Organization).where(Organization.id == data.parent_id)
        )
        parent = result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="父组织不存在")
        level = parent.level + 1
        parent_path = parent.path or ""

    org = Organization(
        name=data.name,
        code=data.code,
        description=data.description,
        parent_id=data.parent_id,
        type=data.type,
        manager_id=data.manager_id,
        level=level,
        path="",  # 临时为空，flush 后更新
    )
    db.add(org)
    await db.flush()

    # 更新路径
    org.path = f"{parent_path}/{org.id}" if parent_path else org.id
    await db.commit()
    await db.refresh(org)
    return org


@router.get("/{org_id}", response_model=OrganizationInDB)
async def get_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取组织详情"""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")
    return org


async def check_org_admin(user: User):
    """检查用户是否有组织管理权限"""
    if not (user.is_super_admin or user.is_org_admin):
        raise HTTPException(status_code=403, detail="权限不足，需要管理员权限")
    return user


@router.put("/{org_id}", response_model=OrganizationInDB)
async def update_organization(
    org_id: str,
    data: OrganizationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新组织"""
    await check_org_admin(current_user)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(org, key, value)

    await db.commit()
    await db.refresh(org)
    return org


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除组织"""
    await check_org_admin(current_user)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")

    # 不允许删除公司级组织
    if org.type == "company":
        raise HTTPException(status_code=400, detail="公司级组织不可删除")

    # 检查是否有子组织
    result = await db.execute(
        select(Organization).where(Organization.parent_id == org_id).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="存在子组织，不可删除")

    # 检查是否有关联用户
    result = await db.execute(
        select(User).where(User.organization_id == org_id).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="组织下存在用户，不可删除")

    await db.delete(org)
    await db.commit()


@router.get("/{org_id}/users")
async def get_organization_users(
    org_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取组织下的用户"""
    result = await db.execute(
        select(User).where(User.organization_id == org_id).offset(skip).limit(limit)
    )
    users = result.scalars().all()

    count_result = await db.execute(
        select(func.count()).select_from(User).where(User.organization_id == org_id)
    )
    total = count_result.scalar() or 0

    return {"items": users, "total": total}
