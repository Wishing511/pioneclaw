"""
知识库 API（已废弃）

此模块已被 Wiki 模块合并，所有功能迁移至 /api/wiki 端点。
请使用 Wiki API 替代：

- 知识库列表 → GET /wiki/
- 创建知识库 → POST /wiki/
- 添加文档 → POST /wiki/import 或 POST /wiki/{id}/chunks
- 语义搜索 → POST /wiki/search/semantic
- 图谱索引 → POST /wiki/{id}/graph

此端点将在未来版本中移除。
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(
    prefix="/knowledge-bases", tags=["Knowledge Bases (已废弃)"], deprecated=True
)


@router.api_route(
    "/",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    status_code=status.HTTP_410_GONE,
)
async def deprecated_root():
    """知识库 API 根路径已废弃"""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "message": "知识库 API 已废弃，请使用 Wiki API",
            "migration": {
                "knowledge_bases_list": "GET /api/wiki/",
                "create_knowledge_base": "POST /api/wiki/",
                "add_document": "POST /api/wiki/import",
                "semantic_search": "POST /api/wiki/search/semantic",
                "chunk_management": "POST /api/wiki/{id}/chunks",
                "graph_indexing": "POST /api/wiki/{id}/graph",
            },
        },
    )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    status_code=status.HTTP_410_GONE,
)
async def deprecated_endpoint(path: str = ""):
    """
    知识库 API 已废弃

    请使用 Wiki API 替代：
    - 知识库列表: GET /api/wiki/
    - 创建文档: POST /api/wiki/
    - 导入文档: POST /api/wiki/import
    - 语义搜索: POST /api/wiki/search/semantic
    - 分块管理: POST /api/wiki/{id}/chunks
    - 图谱索引: POST /api/wiki/{id}/graph
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "message": "知识库 API 已废弃，请使用 Wiki API",
            "migration": {
                "knowledge_bases_list": "GET /api/wiki/",
                "create_knowledge_base": "POST /api/wiki/",
                "add_document": "POST /api/wiki/import",
                "semantic_search": "POST /api/wiki/search/semantic",
                "chunk_management": "POST /api/wiki/{id}/chunks",
                "graph_indexing": "POST /api/wiki/{id}/graph",
            },
        },
    )
