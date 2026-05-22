"""
Wiki 相关的 Pydantic Schema
"""

from datetime import datetime

from pydantic import BaseModel, Field


class WikiBase(BaseModel):
    """Wiki 基础 Schema"""

    title: str = Field(..., min_length=1, max_length=200, description="标题")
    content: str = Field(default="", description="内容")
    path: str = Field(..., min_length=1, max_length=500, description="路径")
    parent_id: str | None = Field(None, description="父Wiki ID")
    tags: list[str] | None = Field(default_factory=list, description="标签")
    status: str = Field(
        default="published", description="状态: draft/published/archived"
    )
    doc_type: str = Field(
        default="markdown", description="文档类型: markdown/text/pdf/url"
    )
    source: str | None = Field(None, description="来源 URL 或文件路径")
    scope: str = Field(default="user", description="权限范围: system/org/user")


class WikiCreate(WikiBase):
    """创建 Wiki Schema"""

    organization_id: str | None = None


class WikiUpdate(BaseModel):
    """更新 Wiki Schema"""

    title: str | None = Field(None, min_length=1, max_length=200)
    content: str | None = None
    path: str | None = Field(None, min_length=1, max_length=500)
    parent_id: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    doc_type: str | None = None
    source: str | None = None
    scope: str | None = None
    change_summary: str | None = Field(None, max_length=500, description="变更摘要")


class WikiInDB(WikiBase):
    """数据库中的 Wiki Schema"""

    id: str
    version: int
    scope: str = "user"
    chunk_count: int = 0
    is_indexed: bool = False
    created_by: int
    organization_id: str | None = None
    meta_data: dict | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WikiDetail(WikiInDB):
    """Wiki 详情（包含作者信息）"""

    author_name: str | None = None
    organization_name: str | None = None


class WikiTree(WikiInDB):
    """Wiki 树 Schema"""

    children: list["WikiTree"] = []


class WikiVersionInDB(BaseModel):
    """Wiki 版本 Schema"""

    id: str
    wiki_id: str
    version: int
    title: str
    content: str
    change_summary: str | None = None
    created_by: int
    created_at: datetime
    author_name: str | None = None

    class Config:
        from_attributes = True


class WikiVersionListResponse(BaseModel):
    """Wiki 版本列表响应"""

    items: list[WikiVersionInDB]
    total: int


class WikiListResponse(BaseModel):
    """Wiki 列表响应"""

    items: list[WikiInDB]
    total: int


class WikiSearchResult(BaseModel):
    """Wiki 搜索结果"""

    id: str
    title: str
    path: str
    highlight: str | None = None  # 高亮摘要
    score: float | None = None  # 搜索得分


class WikiSearchResponse(BaseModel):
    """Wiki 搜索响应"""

    items: list[WikiSearchResult]
    total: int


class WikiSemanticSearchRequest(BaseModel):
    """Wiki 语义搜索请求"""

    query: str = Field(..., min_length=1, description="搜索查询")
    top_k: int = Field(default=10, ge=1, le=100, description="返回数量")
    threshold: float = Field(default=0.5, ge=0, le=1, description="相似度阈值")


class WikiSemanticSearchResult(BaseModel):
    """Wiki 语义搜索结果"""

    id: str
    title: str
    path: str
    content_snippet: str  # 内容片段
    score: float  # 相似度得分
    doc_type: str
    tags: list[str] = []


class WikiSemanticSearchResponse(BaseModel):
    """Wiki 语义搜索响应"""

    items: list[WikiSemanticSearchResult]
    total: int


class WikiImportRequest(BaseModel):
    """Wiki 导入请求"""

    path: str = Field(..., description="目标路径")
    title: str | None = Field(None, description="标题（不指定则从内容提取）")
    content: str = Field(..., description="Markdown 内容")
    tags: list[str] | None = Field(default_factory=list)
    doc_type: str = Field(default="markdown", description="文档类型")
    source: str | None = Field(None, description="来源")
    scope: str = Field(default="user", description="权限范围")


class WikiChunkRequest(BaseModel):
    """Wiki 分块请求"""

    chunk_size: int = Field(
        default=500, ge=100, le=2000, description="分块大小（字符）"
    )
    chunk_overlap: int = Field(default=50, ge=0, le=200, description="分块重叠")


class WikiChunkResponse(BaseModel):
    """Wiki 分块响应"""

    wiki_id: str
    chunk_count: int
    chunks: list[dict]  # [{index, content, start, end}]


# 解决循环引用
WikiTree.model_rebuild()
