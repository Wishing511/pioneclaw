"""
Approval Schema
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.approval import ApprovalType


class ApprovalCreate(BaseModel):
    """创建审批请求"""

    approval_type: ApprovalType
    title: str
    description: str | None = None
    resource_type: str  # skill / wiki / document
    resource_id: str
    target_scope: str  # org / system
    target_org_id: str | None = None
    metadata: dict[str, Any] | None = None


class ApprovalReview(BaseModel):
    """审批操作"""

    approved: bool
    review_comment: str | None = None


class ApprovalResponse(BaseModel):
    id: int
    approval_type: str
    status: str
    title: str
    description: str | None = None
    requester_id: int
    requester_name: str | None = None
    requester_org_id: str | None = None
    reviewer_id: int | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None
    resource_type: str
    resource_id: str
    target_scope: str
    target_org_id: str | None = None
    extra_data: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ApprovalBrief(BaseModel):
    """审批简要"""

    id: int
    approval_type: str
    status: str
    title: str
    resource_type: str
    target_scope: str
    created_at: datetime
