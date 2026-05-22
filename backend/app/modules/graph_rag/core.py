"""
GraphRAG 核心 — LightRAG 封装

提供文档索引和查询功能
"""

from collections.abc import AsyncIterator
from pathlib import Path

from loguru import logger

from app.modules.graph_rag.config import GraphRAGSettings


class GraphRAGClient:
    """知识图谱客户端（LightRAG 封装）"""

    def __init__(
        self,
        config: GraphRAGSettings | None = None,
        llm_caller=None,  # async (prompt: str) -> str
        embedding_func=None,  # async (texts: List[str]) -> List[List[float]]
    ):
        self.config = config or GraphRAGSettings()
        self.llm_caller = llm_caller
        self.embedding_func = embedding_func
        self._rag = None

    def _ensure_rag(self):
        """延迟初始化 LightRAG"""
        if self._rag is not None:
            return self._rag

        try:
            from lightrag import LightRAG
            from lightrag.llm import gpt_4o_mini_complete, openai_embedding

            # 确保工作目录存在
            working_dir = Path(self.config.working_dir)
            working_dir.mkdir(parents=True, exist_ok=True)

            # 创建 LightRAG 实例
            # 如果有自定义 LLM/Embedding，使用自定义函数
            if self.llm_caller or self.embedding_func:
                self._rag = LightRAG(
                    working_dir=str(working_dir),
                    llm_model_func=self._wrap_llm_caller()
                    if self.llm_caller
                    else gpt_4o_mini_complete,
                    embedding_func=self._wrap_embedding_func()
                    if self.embedding_func
                    else openai_embedding,
                )
            else:
                # 使用默认配置
                self._rag = LightRAG(
                    working_dir=str(working_dir),
                )

            logger.info(f"LightRAG initialized at {working_dir}")
            return self._rag

        except ImportError as e:
            logger.error(f"LightRAG not installed: {e}")
            raise RuntimeError("Please install lightrag-hku: pip install lightrag-hku")
        except Exception as e:
            logger.error(f"Failed to initialize LightRAG: {e}")
            raise

    def _wrap_llm_caller(self):
        """包装自定义 LLM 调用器"""

        async def wrapped_llm(prompt: str, **kwargs) -> str:
            if self.llm_caller:
                return await self.llm_caller(prompt)
            return ""

        return wrapped_llm

    def _wrap_embedding_func(self):
        """包装自定义嵌入函数"""

        async def wrapped_embedding(texts: list[str]) -> list[list[float]]:
            if self.embedding_func:
                return await self.embedding_func(texts)
            return []

        return wrapped_embedding

    # ==================== 索引 ====================

    async def index_document(self, content: str, doc_id: str | None = None) -> dict:
        """
        索引文档到知识图谱

        Args:
            content: 文档内容
            doc_id: 可选文档 ID

        Returns:
            {"success": bool, "message": str, "doc_id": str}
        """
        try:
            rag = self._ensure_rag()

            # LightRAG 的 insert 是同步的
            rag.insert(content)

            return {
                "success": True,
                "message": "文档索引成功",
                "doc_id": doc_id or "auto",
            }
        except Exception as e:
            logger.error(f"Failed to index document: {e}")
            return {
                "success": False,
                "message": str(e),
                "doc_id": doc_id or "auto",
            }

    async def index_batch(self, documents: list[str]) -> dict:
        """批量索引文档"""
        try:
            rag = self._ensure_rag()

            for doc in documents:
                rag.insert(doc)

            return {
                "success": True,
                "message": f"成功索引 {len(documents)} 个文档",
                "count": len(documents),
            }
        except Exception as e:
            logger.error(f"Failed to batch index: {e}")
            return {
                "success": False,
                "message": str(e),
                "count": 0,
            }

    # ==================== 查询 ====================

    async def query(
        self,
        query_text: str,
        mode: str = "hybrid",  # local, global, hybrid, naive, mix
    ) -> dict:
        """
        查询知识图谱

        Args:
            query_text: 查询文本
            mode: 查询模式
                - local: 本地查询（实体相关）
                - global: 全局查询（社区相关）
                - hybrid: 混合查询
                - naive: 纯向量查询
                - mix: 全模式查询

        Returns:
            {"result": str, "mode": str}
        """
        try:
            rag = self._ensure_rag()

            # LightRAG 查询模式映射
            from lightrag import QueryMode

            mode_map = {
                "local": QueryMode.Local,
                "global": QueryMode.Global,
                "hybrid": QueryMode.Hybrid,
                "naive": QueryMode.Naive,
                "mix": QueryMode.Mix,
            }

            query_mode = mode_map.get(mode, QueryMode.Hybrid)

            result = rag.query(query_text, mode=query_mode)

            return {
                "result": result,
                "mode": mode,
            }
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return {
                "result": f"查询失败: {str(e)}",
                "mode": mode,
            }

    async def query_stream(
        self,
        query_text: str,
        mode: str = "hybrid",
    ) -> AsyncIterator[str]:
        """流式查询"""
        try:
            rag = self._ensure_rag()

            from lightrag import QueryMode

            mode_map = {
                "local": QueryMode.Local,
                "global": QueryMode.Global,
                "hybrid": QueryMode.Hybrid,
                "naive": QueryMode.Naive,
                "mix": QueryMode.Mix,
            }

            query_mode = mode_map.get(mode, QueryMode.Hybrid)

            # LightRAG 的流式查询
            async for chunk in rag.query(query_text, mode=query_mode, stream=True):
                yield chunk

        except Exception as e:
            logger.error(f"Stream query failed: {e}")
            yield f"查询失败: {str(e)}"

    # ==================== 统计 ====================

    async def stats(self) -> dict:
        """获取知识图谱统计信息"""
        try:
            rag = self._ensure_rag()

            # LightRAG 内部存储信息
            # 尝试获取节点和边的数量
            working_dir = Path(self.config.working_dir)

            # 检查存储文件
            graph_file = working_dir / "graph_chunk_entity_relation.graphml"
            vector_file = working_dir / "vdb_chunks.json"

            stats = {
                "working_dir": str(working_dir),
                "graph_exists": graph_file.exists() if graph_file else False,
                "vector_exists": vector_file.exists() if vector_file else False,
            }

            # 尝试读取更多统计信息
            try:
                if hasattr(rag, "chunk_entity_relation_graph"):
                    g = rag.chunk_entity_relation_graph
                    stats["nodes"] = g.number_of_nodes() if g else 0
                    stats["edges"] = g.number_of_edges() if g else 0
            except Exception:
                stats["nodes"] = "unknown"
                stats["edges"] = "unknown"

            return stats

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"error": str(e)}

    # ==================== 清理 ====================

    async def clear(self) -> dict:
        """清空知识图谱"""
        try:
            working_dir = Path(self.config.working_dir)

            # 删除所有存储文件
            import shutil

            if working_dir.exists():
                shutil.rmtree(working_dir)
                working_dir.mkdir(parents=True, exist_ok=True)

            # 重置实例
            self._rag = None

            return {"success": True, "message": "知识图谱已清空"}
        except Exception as e:
            logger.error(f"Failed to clear: {e}")
            return {"success": False, "message": str(e)}
