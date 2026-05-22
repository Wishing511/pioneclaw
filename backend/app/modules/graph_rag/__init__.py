"""
知识图谱 RAG 模块 — 基于 LightRAG

提供：
- 文档索引（自动实体/关系抽取）
- 5种查询模式（local/global/hybrid/naive/mix）
- 统计信息
"""

from app.modules.graph_rag.config import GraphRAGSettings
from app.modules.graph_rag.core import GraphRAGClient

__all__ = [
    "GraphRAGClient",
    "GraphRAGSettings",
]
