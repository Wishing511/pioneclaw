"""
AutoDream Pydantic Schemas
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ==================== Config Schemas ====================

class AutoDreamConfigResponse(BaseModel):
    id: int
    enabled: bool
    cron_expression: str
    batch_size: int
    max_consolidated_per_run: int
    archive_after_days: int
    delete_after_days: Optional[int] = None
    enable_dedup: bool
    enable_consolidation: bool
    enable_archival: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AutoDreamConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    cron_expression: Optional[str] = None
    batch_size: Optional[int] = Field(default=None, ge=1, le=500)
    max_consolidated_per_run: Optional[int] = Field(default=None, ge=1, le=100)
    archive_after_days: Optional[int] = Field(default=None, ge=1)
    delete_after_days: Optional[int] = Field(default=None, ge=1)
    enable_dedup: Optional[bool] = None
    enable_consolidation: Optional[bool] = None
    enable_archival: Optional[bool] = None


# ==================== Log Schemas ====================

class AutoDreamLogResponse(BaseModel):
    id: int
    triggered_at: datetime
    triggered_by: str
    status: str
    total_memories: int
    duplicates_found: int
    merged: int
    consolidated: int
    archived: int
    deleted: int
    llm_calls: int
    llm_tokens_in: int
    llm_tokens_out: int
    duration_seconds: float
    error_message: Optional[str] = None
    details: Optional[str] = None

    class Config:
        from_attributes = True


class AutoDreamLogListResponse(BaseModel):
    items: list[AutoDreamLogResponse]
    total: int


# ==================== Status / Trigger Schemas ====================

class AutoDreamStatusResponse(BaseModel):
    is_running: bool
    last_run: Optional[AutoDreamLogResponse] = None
    config: AutoDreamConfigResponse


class AutoDreamTriggerResponse(BaseModel):
    log_id: int
    status: str
    message: str
