"""
任务评论相关的 Pydantic Schema
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TaskCommentBase(BaseModel):
    """任务评论基础 Schema"""

    content: str = Field(..., min_length=1, description="评论内容")
    parent_id: str | None = Field(None, description="父评论ID（回复）")
    mentions: list[int] | None = Field(
        default_factory=list, description="@提及的用户ID"
    )


class TaskCommentCreate(TaskCommentBase):
    """创建任务评论 Schema"""

    pass


class TaskCommentUpdate(BaseModel):
    """更新任务评论 Schema"""

    content: str | None = Field(None, min_length=1)
    mentions: list[int] | None = None


class TaskCommentInDB(TaskCommentBase):
    """数据库中的评论 Schema"""

    id: str
    task_id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False

    class Config:
        from_attributes = True


class TaskCommentDetail(TaskCommentInDB):
    """评论详情（包含用户信息）"""

    user_name: str | None = None
    user_avatar: str | None = None
    replies: list["TaskCommentDetail"] = []


class TaskCommentListResponse(BaseModel):
    """评论列表响应"""

    items: list[TaskCommentDetail]
    total: int


# 解决循环引用
TaskCommentDetail.model_rebuild()
