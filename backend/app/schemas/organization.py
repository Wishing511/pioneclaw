"""
组织相关的 Pydantic Schema
"""

from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationBase(BaseModel):
    """组织基础 Schema"""

    name: str = Field(..., min_length=1, max_length=100, description="组织名称")
    code: str = Field(..., min_length=1, max_length=50, description="组织代码")
    description: str | None = Field(None, max_length=500, description="组织描述")
    type: str = Field(
        default="department", description="组织类型: company/department/team"
    )
    parent_id: str | None = Field(None, description="父组织ID")
    manager_id: int | None = Field(None, description="管理者ID")


class OrganizationCreate(OrganizationBase):
    """创建组织 Schema"""

    pass


class OrganizationUpdate(BaseModel):
    """更新组织 Schema"""

    name: str | None = Field(None, min_length=1, max_length=100)
    code: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=500)
    type: str | None = None
    parent_id: str | None = None
    manager_id: int | None = None
    status: str | None = None
    meta_data: dict | None = None


class OrganizationInDB(OrganizationBase):
    """数据库中的组织 Schema"""

    id: str
    level: int
    path: str
    status: str
    meta_data: dict | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrganizationTree(OrganizationInDB):
    """组织树 Schema"""

    children: list["OrganizationTree"] = []
    user_count: int = 0


class OrganizationSimple(BaseModel):
    """简化组织 Schema（用于下拉选择）"""

    id: str
    name: str
    code: str
    level: int
    parent_id: str | None = None

    class Config:
        from_attributes = True


class OrganizationListResponse(BaseModel):
    """组织列表响应"""

    items: list[OrganizationInDB]
    total: int


# 解决循环引用
OrganizationTree.model_rebuild()
