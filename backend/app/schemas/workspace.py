"""
Workspace Schema
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WorkspaceSettings(BaseModel):
    """工作空间设置"""

    output_language: str = "中文"
    default_model_config_id: int | None = None
    user_name: str = ""
    user_address: str = ""
    ai_name: str = "小助手"
    personality: str = "professional"
    custom_personality: str = ""


class WorkspaceCreate(BaseModel):
    name: str
    path: str = ""
    description: str | None = None
    settings: dict[str, Any] | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    path: str | None = None
    description: str | None = None
    settings: dict[str, Any] | None = None
    is_active: bool | None = None


class WorkspaceSettingsUpdate(BaseModel):
    """工作空间设置更新"""

    output_language: str | None = None
    default_model_config_id: int | None = None
    user_name: str | None = None
    user_address: str | None = None
    ai_name: str | None = None
    personality: str | None = None
    custom_personality: str | None = None


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    path: str
    description: str | None = None
    owner_id: int
    organization_id: str | None = None
    settings: dict[str, Any] | None = None
    is_default: bool
    is_active: bool
    last_active_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkspaceBrief(BaseModel):
    """简要信息（用于下拉选择）"""

    id: int
    name: str
    is_default: bool
    is_active: bool
