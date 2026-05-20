"""
安全网关 Pydantic Schemas
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class FilterInputRequest(BaseModel):
    text: str
    context: Optional[Dict[str, Any]] = None


class FilterInputResponse(BaseModel):
    action: str  # allow / block / sanitize / approve
    content: Optional[str] = None
    reason: Optional[str] = None
    risk_level: str = "low"
    matched_rules: Optional[List[dict]] = None
    model_result: Optional[Dict[str, Any]] = None  # 模型引擎原始结果


class CheckToolRequest(BaseModel):
    tool_name: str
    arguments: dict
    context: Optional[Dict[str, Any]] = None


class WordCreate(BaseModel):
    word: str = Field(..., max_length=500)
    word_type: str  # sensitive / risk / allow
    category: Optional[str] = Field(None, max_length=100)
    severity: int = Field(default=1, ge=1, le=5)
    description: Optional[str] = None
    is_active: bool = True
    scope: str = "system"
    organization_id: Optional[str] = None


class WordUpdate(BaseModel):
    word: Optional[str] = Field(None, max_length=500)
    word_type: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    severity: Optional[int] = Field(None, ge=1, le=5)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WordResponse(BaseModel):
    id: int
    word: str
    word_type: str
    category: Optional[str]
    severity: int
    description: Optional[str]
    is_active: bool
    scope: str
    organization_id: Optional[str]
    creator_id: Optional[int]
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AuditLogResponse(BaseModel):
    id: int
    check_point: str
    event_type: str
    risk_level: str
    user_id: Optional[int]
    username: Optional[str]
    session_id: Optional[str]
    agent_id: Optional[str]
    content_preview: Optional[str]
    action: str
    reason: Optional[str]
    matched_rules: Optional[dict]
    request_trace_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    items: List[AuditLogResponse]
    total: int


class SecurityGatewayConfig(BaseModel):
    enable_word_engine: bool = True
    enable_regex_engine: bool = True
    enable_model_engine: bool = False
    word_engine_cache_ttl: int = 60
    fail_open: bool = True
    log_retention_days: int = 180


class DashboardStatsResponse(BaseModel):
    """看板统计响应"""
    risk_trend: List[dict]
    top_words: List[dict]
    top_users: List[dict]
    summary: dict
