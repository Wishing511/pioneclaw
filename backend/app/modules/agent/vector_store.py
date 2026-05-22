"""
PioneClaw 向量存储模块
基于 SQLite + BGE-small-zh embedding (512 维)
支持语义搜索、混合搜索、批量添加

借鉴: AIE vector_store.py
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

# Vector dimension for bge-small-zh-v1.5
VECTOR_DIMENSION = 512


class VectorEntry:
    """向量条目"""

    def __init__(
        self,
        content: str,
        metadata: dict = None,
        source_type: str = "knowledge",  # knowledge, memory, skill, document
        source_id: str = None,
        entry_id: str = None,
    ):
        self.id = entry_id or str(uuid.uuid4())
        self.content = content
        self.metadata = metadata or {}
        self.source_type = source_type
        self.source_id = source_id
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "metadata": self.metadata,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SearchResult:
    """搜索结果"""

    def __init__(
        self,
        entry_id: str,
        content: str,
        score: float,
        metadata: dict = None,
        source_type: str = None,
        source_id: str = None,
    ):
        self.id = entry_id
        self.content = content
        self.score = score
        self.metadata = metadata or {}
        self.source_type = source_type
        self.source_id = source_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
            "source_type": self.source_type,
            "source_id": self.source_id,
        }


class VectorStore:
    """
    向量存储

    使用 SQLite 存储向量和元数据
    支持 BGE-small-zh embedding (512 维)
    """

    def __init__(
        self,
        db_path: Path = None,
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
    ):
        self.db_path = db_path or Path("data/vector_store.db")
        self.embedding_model_name = embedding_model
        self.embedding_model = None
        self._embedding_deferred = None  # DeferredInit, created lazily
        self._lock = threading.Lock()

        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 创建向量表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                metadata TEXT,
                vector BLOB,
                source_type TEXT DEFAULT 'knowledge',
                source_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_type ON vectors(source_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_id ON vectors(source_id)
        """)

        conn.commit()
        conn.close()

        logger.info(f"VectorStore initialized at {self.db_path}")

    def _get_embedding_model(self):
        """延迟加载 embedding 模型（使用 DeferredInit，支持超时+重试）"""
        if self.embedding_model is not None:
            return self.embedding_model

        if self._embedding_deferred is None:
            from app.core.deferred_init import DeferredInit

            name = self.embedding_model_name
            self._embedding_deferred = DeferredInit(
                factory=lambda: self._load_embedding_model(name),
                name=f"embedding:{name}",
                timeout=120.0,  # 模型下载可能较慢
                max_retries=1,  # 下载失败时重试一次
            )

        self.embedding_model = self._embedding_deferred.get_sync()
        return self.embedding_model

    @staticmethod
    def _load_embedding_model(model_name: str):
        """加载 SentenceTransformer 模型（支持 ModelScope）"""
        import os as _os

        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {model_name}")

        model_path = model_name
        if model_path.startswith("BAAI/") or "/" in model_path:
            try:
                from modelscope import snapshot_download

                cache_dir = snapshot_download(
                    model_path, cache_dir="C:/Users/Yue/modelscope_cache"
                )
                model_path = cache_dir
                logger.info(f"Model downloaded from ModelScope: {cache_dir}")
            except Exception as e:
                logger.warning(f"ModelScope download failed, trying local path: {e}")
                local_path = f"C:/Users/Yue/{model_path.split('/')[-1]}"
                if _os.path.exists(local_path):
                    model_path = local_path
                    logger.info(f"Using local model: {local_path}")

        model = SentenceTransformer(model_path, device="cpu")
        logger.info("Embedding model loaded successfully")
        return model

    def _encode(self, texts: list[str]) -> list:
        """编码文本为向量"""

        model = self._get_embedding_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings

    def _vector_to_blob(self, vector) -> bytes:
        """向量转 blob"""
        import numpy as np

        return np.array(vector, dtype=np.float32).tobytes()

    def _blob_to_vector(self, blob: bytes):
        """blob 转向量"""
        import numpy as np

        return np.frombuffer(blob, dtype=np.float32)

    def _cosine_similarity(self, a, b) -> float:
        """计算余弦相似度"""
        import numpy as np

        return float(np.dot(a, b))

    # ==================== CRUD 操作 ====================

    def add(
        self,
        content: str,
        metadata: dict = None,
        source_type: str = "knowledge",
        source_id: str = None,
        generate_embedding: bool = True,
    ) -> str:
        """添加向量条目"""
        entry_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # 生成向量
        vector_blob = None
        if generate_embedding:
            try:
                embeddings = self._encode([content])
                vector_blob = self._vector_to_blob(embeddings[0])
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO vectors (id, content, metadata, vector, source_type, source_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry_id,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    vector_blob,
                    source_type,
                    source_id,
                    now,
                    now,
                ),
            )

            conn.commit()
            conn.close()

        logger.debug(f"Added vector entry: {entry_id}")
        return entry_id

    def add_batch(
        self,
        entries: list[dict],
        source_type: str = "knowledge",
        source_id: str = None,
    ) -> list[str]:
        """批量添加向量条目"""
        if not entries:
            return []

        # 提取内容
        contents = [e["content"] for e in entries]

        # 批量生成向量
        logger.info(f"Encoding {len(contents)} entries...")
        try:
            embeddings = self._encode(contents)
        except Exception as e:
            logger.warning(f"Failed to generate embeddings: {e}")
            embeddings = [None] * len(contents)

        # 插入数据库
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            entry_ids = []
            now = datetime.now().isoformat()

            for i, entry in enumerate(entries):
                entry_id = str(uuid.uuid4())
                entry_ids.append(entry_id)

                vector_blob = None
                if embeddings[i] is not None:
                    vector_blob = self._vector_to_blob(embeddings[i])

                cursor.execute(
                    """
                    INSERT INTO vectors (id, content, metadata, vector, source_type, source_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        entry_id,
                        entry["content"],
                        json.dumps(entry.get("metadata", {}), ensure_ascii=False),
                        vector_blob,
                        source_type,
                        source_id or entry.get("source_id"),
                        now,
                        now,
                    ),
                )

            conn.commit()
            conn.close()

        logger.info(f"Added {len(entry_ids)} vector entries")
        return entry_ids

    def get(self, entry_id: str) -> dict | None:
        """获取向量条目"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM vectors WHERE id = ?", (entry_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._row_to_dict(row)

    def update(
        self,
        entry_id: str,
        content: str = None,
        metadata: dict = None,
        generate_embedding: bool = True,
    ) -> bool:
        """更新向量条目"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # 检查是否存在
            cursor.execute(
                "SELECT content, metadata FROM vectors WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False

            old_content, old_metadata = row
            update_content = content if content is not None else old_content
            update_metadata = json.dumps(
                metadata if metadata is not None else json.loads(old_metadata or "{}"),
                ensure_ascii=False,
            )

            # 生成新向量
            vector_blob = None
            if generate_embedding and content is not None:
                try:
                    embeddings = self._encode([content])
                    vector_blob = self._vector_to_blob(embeddings[0])
                except Exception as e:
                    logger.warning(f"Failed to generate embedding: {e}")

            now = datetime.now().isoformat()

            if vector_blob:
                cursor.execute(
                    """
                    UPDATE vectors
                    SET content = ?, metadata = ?, vector = ?, updated_at = ?
                    WHERE id = ?
                """,
                    (update_content, update_metadata, vector_blob, now, entry_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE vectors
                    SET content = ?, metadata = ?, updated_at = ?
                    WHERE id = ?
                """,
                    (update_content, update_metadata, now, entry_id),
                )

            conn.commit()
            conn.close()

        logger.debug(f"Updated vector entry: {entry_id}")
        return True

    def delete(self, entry_id: str) -> bool:
        """删除向量条目"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("DELETE FROM vectors WHERE id = ?", (entry_id,))
            deleted = cursor.rowcount > 0

            conn.commit()
            conn.close()

        if deleted:
            logger.debug(f"Deleted vector entry: {entry_id}")
        return deleted

    def delete_by_source(self, source_type: str = None, source_id: str = None) -> int:
        """按来源删除向量条目"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            if source_type and source_id:
                cursor.execute(
                    "DELETE FROM vectors WHERE source_type = ? AND source_id = ?",
                    (source_type, source_id),
                )
            elif source_type:
                cursor.execute(
                    "DELETE FROM vectors WHERE source_type = ?", (source_type,)
                )

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

        logger.info(f"Deleted {deleted} vector entries")
        return deleted

    # ==================== 搜索操作 ====================

    def search(
        self,
        query: str,
        top_k: int = 5,
        source_type: str = None,
        source_id: str = None,
        min_score: float = 0.5,
    ) -> list[SearchResult]:
        """语义搜索"""
        try:
            query_embedding = self._encode([query])[0]
        except Exception as e:
            logger.error(f"Failed to encode query: {e}")
            return []

        # 构建 SQL
        sql = """
            SELECT id, content, metadata, source_type, source_id, vector
            FROM vectors
            WHERE vector IS NOT NULL
        """
        params = []

        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)

        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(sql, params)

        rows = cursor.fetchall()
        conn.close()

        # 计算相似度
        results = []
        for row in rows:
            entry_id, content, metadata, src_type, src_id, vector_blob = row
            if vector_blob:
                vector = self._blob_to_vector(vector_blob)
                score = self._cosine_similarity(query_embedding, vector)

                if score >= min_score:
                    results.append(
                        SearchResult(
                            entry_id=entry_id,
                            content=content,
                            score=score,
                            metadata=json.loads(metadata or "{}"),
                            source_type=src_type,
                            source_id=src_id,
                        )
                    )

        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        source_type: str = None,
        source_id: str = None,
        keyword_weight: float = 0.3,
        min_score: float = 0.3,
    ) -> list[SearchResult]:
        """混合搜索（语义 + 关键词）"""
        # 语义搜索
        vector_results = self.search(
            query=query,
            top_k=top_k * 2,
            source_type=source_type,
            source_id=source_id,
            min_score=min_score,
        )

        # 关键词搜索
        keyword_results = self._keyword_search(
            query=query,
            top_k=top_k * 2,
            source_type=source_type,
            source_id=source_id,
        )

        # 合并结果
        merged = {}

        for r in vector_results:
            merged[r.id] = SearchResult(
                entry_id=r.id,
                content=r.content,
                score=r.score * (1 - keyword_weight),
                metadata=r.metadata,
                source_type=r.source_type,
                source_id=r.source_id,
            )

        for r in keyword_results:
            if r.id in merged:
                # 合并分数
                merged[r.id].score += r.score * keyword_weight
            else:
                merged[r.id] = SearchResult(
                    entry_id=r.id,
                    content=r.content,
                    score=r.score * keyword_weight,
                    metadata=r.metadata,
                    source_type=r.source_type,
                    source_id=r.source_id,
                )

        # 排序
        results = list(merged.values())
        results.sort(key=lambda x: x.score, reverse=True)

        return results[:top_k]

    def _keyword_search(
        self,
        query: str,
        top_k: int = 5,
        source_type: str = None,
        source_id: str = None,
    ) -> list[SearchResult]:
        """关键词搜索"""
        keywords = query.lower().split()

        sql = "SELECT id, content, metadata, source_type, source_id FROM vectors WHERE 1=1"
        params = []

        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)

        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(sql, params)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            entry_id, content, metadata, src_type, src_id = row
            content_lower = content.lower()

            # 计算关键词匹配
            matches = sum(1 for kw in keywords if kw in content_lower)
            if matches > 0:
                score = matches / len(keywords)
                results.append(
                    SearchResult(
                        entry_id=entry_id,
                        content=content,
                        score=score,
                        metadata=json.loads(metadata or "{}"),
                        source_type=src_type,
                        source_id=src_id,
                    )
                )

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    # ==================== 统计操作 ====================

    def count(self, source_type: str = None) -> int:
        """统计条目数"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        if source_type:
            cursor.execute(
                "SELECT COUNT(*) FROM vectors WHERE source_type = ?", (source_type,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM vectors")

        count = cursor.fetchone()[0]
        conn.close()

        return count

    def get_source_ids(self, source_type: str = None) -> list[str]:
        """获取所有来源 ID"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        if source_type:
            cursor.execute(
                "SELECT DISTINCT source_id FROM vectors WHERE source_type = ? AND source_id IS NOT NULL",
                (source_type,),
            )
        else:
            cursor.execute(
                "SELECT DISTINCT source_id FROM vectors WHERE source_id IS NOT NULL"
            )

        rows = cursor.fetchall()
        conn.close()

        return [row[0] for row in rows if row[0]]

    def get_stats(self) -> dict:
        """获取统计信息"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 总数
        cursor.execute("SELECT COUNT(*) FROM vectors")
        total = cursor.fetchone()[0]

        # 有向量的数量
        cursor.execute("SELECT COUNT(*) FROM vectors WHERE vector IS NOT NULL")
        with_vector = cursor.fetchone()[0]

        # 按来源类型统计
        cursor.execute("""
            SELECT source_type, COUNT(*)
            FROM vectors
            GROUP BY source_type
        """)
        by_type = {row[0]: row[1] for row in cursor.fetchall()}

        conn.close()

        return {
            "total": total,
            "with_vector": with_vector,
            "by_source_type": by_type,
        }

    def _row_to_dict(self, row: tuple) -> dict:
        """数据库行转字典"""
        columns = [
            "id",
            "content",
            "metadata",
            "vector",
            "source_type",
            "source_id",
            "created_at",
            "updated_at",
        ]
        result = dict(zip(columns, row, strict=False))

        if result["metadata"]:
            result["metadata"] = json.loads(result["metadata"])

        if result["vector"]:
            result["vector"] = self._blob_to_vector(result["vector"]).tolist()

        return result


# 全局实例
_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """获取全局向量存储实例"""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


def reinit_vector_store(db_path: Path = None):
    """重新初始化向量存储"""
    global _vector_store
    _vector_store = VectorStore(db_path=db_path)
    return _vector_store
