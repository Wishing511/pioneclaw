"""
GraphRAG 配置
"""

from dataclasses import dataclass


@dataclass
class GraphRAGSettings:
    """知识图谱配置"""

    # 存储路径
    working_dir: str = "data/graph_rag"

    # 嵌入配置
    embedding_model: str = "C:/Users/Yue/bge-small-zh-v1.5"

    # LLM 配置（通过回调函数传入）
    llm_model: str = "gpt-4o"

    # 查询配置
    chunk_token_size: int = 1200
    chunk_overlap_token_size: int = 100

    # 图配置
    entity_max_gleaning: int = 1
    max_nodes: int = 1000

    # 存储后端
    vector_db_storage: str = "NanoVectorDB"  # 或 "Chroma", "Faiss"

    # 其他
    enable_llm_cache: bool = True
