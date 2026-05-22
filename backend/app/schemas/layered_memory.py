"""
分层记忆 Pydantic Schema — L0/L1/L2 三级记忆体系
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ==================== 存储/创建 ====================
class LayeredMemoryStore(BaseModel):
    """存储记忆请求 — 自动生成 L0/L1"""

    content: str = Field(..., min_length=1, description="L2 全文内容")
    name: str = Field(..., min_length=1, max_length=200, description="记忆名称")
    context_type: str = Field(
        default="memory", description="上下文类型: memory/resource/skill"
    )
    uri: str | None = Field(None, description="自定义 URI，不填则自动生成")
    parent_uri: str | None = Field(None, description="父 URI（用于关联）")
    tags: list[str] | None = Field(None, description="标签列表")
    source: str | None = Field(None, description="来源")
    importance: int = Field(default=3, ge=1, le=5, description="重要性 1-5")
    session_id: str | None = Field(None, description="所属会话 ID")
    agent_id: int | None = Field(None, description="关联 Agent ID")


class LayeredMemoryUpdate(BaseModel):
    """更新记忆请求"""

    content: str | None = Field(None, min_length=1)
    name: str | None = Field(None, max_length=200)
    context_type: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    importance: int | None = Field(None, ge=1, le=5)
    is_active: bool | None = None
    regenerate_tiers: bool = Field(default=True, description="是否重新生成 L0/L1")


# ==================== 检索 ====================
class LayeredMemoryRecall(BaseModel):
    """语义检索请求"""

    query: str = Field(..., min_length=1, description="查询文本")
    context_type: str = Field(
        default="all", description="类型筛选: all/memory/resource/skill"
    )
    layers: list[int] | None = Field(default=[2, 1], description="搜索层级，默认 L2+L1")
    top_k: int = Field(default=10, ge=1, le=50, description="返回数量")
    session_id: str | None = Field(None, description="限定会话范围")
    agent_id: int | None = Field(None, description="限定 Agent 范围")


class LayeredMemoryPromote(BaseModel):
    """L1→L2 提升请求"""

    uri: str = Field(..., description="L1 记忆 URI")


class LayeredMemoryEvict(BaseModel):
    """清理 L0 请求"""

    session_id: str = Field(..., description="要清理的会话 ID")


# ==================== 响应 ====================
class LayeredMemoryResponse(BaseModel):
    """单条记忆响应"""

    id: int
    uri: str
    parent_uri: str | None = None
    layer: int
    context_type: str
    name: str
    abstract: str | None = None
    overview: str | None = None
    content: str
    tags: list[str] | None = None
    source: str | None = None
    importance: int = 3
    access_count: int = 0
    session_id: str | None = None
    user_id: int
    agent_id: int | None = None
    vector_id: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LayeredMemoryBrief(BaseModel):
    """记忆简要（列表用，不含全文）"""

    id: int
    uri: str
    layer: int
    context_type: str
    name: str
    abstract: str | None = None
    overview: str | None = None
    importance: int = 3
    access_count: int = 0
    source: str | None = None
    session_id: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LayeredMemoryStats(BaseModel):
    """分层记忆统计"""

    total: int = 0
    l0_count: int = 0
    l1_count: int = 0
    l2_count: int = 0
    by_type: dict = {}
    by_source: dict = {}
    vector_count: int = 0


class LayeredMemoryListResponse(BaseModel):
    """记忆列表响应"""

    items: list[LayeredMemoryBrief]
    total: int


class RecallResultItem(BaseModel):
    """检索结果单项"""

    uri: str
    name: str
    layer: int
    context_type: str
    text: str
    score: float
    abstract: str | None = None
    overview: str | None = None


class LayeredMemoryRecallResponse(BaseModel):
    """检索响应"""

    results: list[RecallResultItem]
    intent: str | None = None
    total: int
