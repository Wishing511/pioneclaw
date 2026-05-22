"""
RerankModule — 5维重排序

维度与权重:
- semantic  (0.50): 语义相似度
- hotness   (0.20): 访问热度 (log-scaled access_count)
- recency   (0.15): 时间衰减 (30天半衰期)
- level     (0.10): 层级偏好 (L1 > L2 > L0)
- type_match(0.05): 上下文类型匹配
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from app.modules.agent.layered_memory.retrieval_engine import RetrievalResult

# 默认权重
DEFAULT_WEIGHTS = {
    "semantic": 0.50,
    "hotness": 0.20,
    "recency": 0.15,
    "level": 0.10,
    "type_match": 0.05,
}


@dataclass
class RerankConfig:
    """重排序配置"""

    weights: dict = None
    recency_half_life_days: float = 30.0  # 半衰期（天）

    def __post_init__(self):
        if self.weights is None:
            self.weights = DEFAULT_WEIGHTS.copy()


class RerankModule:
    """5维重排序模块"""

    def __init__(self, config: RerankConfig = None):
        self.config = config or RerankConfig()
        self.weights = self.config.weights

    def rerank(
        self,
        results: list[RetrievalResult],
        query_context_type: str | None = None,
        query_layer: int | None = None,
    ) -> list[RetrievalResult]:
        """
        对检索结果进行 5 维重排序

        Args:
            results: 检索结果列表
            query_context_type: 查询的上下文类型
            query_layer: 查询的目标层级

        Returns:
            重排序后的结果列表
        """
        if not results:
            return results

        for result in results:
            scores = self._calculate_dimension_scores(
                result, query_context_type, query_layer
            )
            result.score = self._combine_scores(scores)

        # 按综合分数降序排列
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def explain_ranking(
        self,
        result: RetrievalResult,
        query_context_type: str | None = None,
        query_layer: int | None = None,
    ) -> dict:
        """解释排序原因（调试用）"""
        scores = self._calculate_dimension_scores(
            result, query_context_type, query_layer
        )
        explanation = {}
        for dim, score in scores.items():
            weight = self.weights.get(dim, 0)
            explanation[dim] = {
                "raw_score": round(score, 4),
                "weight": weight,
                "contribution": round(score * weight, 4),
            }
        explanation["combined"] = round(self._combine_scores(scores), 4)
        return explanation

    # ==================== 维度分数计算 ====================

    def _calculate_dimension_scores(
        self,
        result: RetrievalResult,
        query_context_type: str | None,
        query_layer: int | None,
    ) -> dict:
        """计算 5 维分数"""
        # semantic: 直接使用已有的 score（语义搜索分数）
        semantic = min(1.0, max(0.0, result.score))

        # hotness: log-scaled access_count
        hotness = self._calc_hotness(result.access_count)

        # recency: 时间衰减
        recency = self._calc_recency(result.updated_at)

        # level: 层级偏好
        level = self._calc_level_score(result.layer, query_layer)

        # type_match: 类型匹配
        type_match = self._calc_type_match(result.context_type, query_context_type)

        return {
            "semantic": semantic,
            "hotness": hotness,
            "recency": recency,
            "level": level,
            "type_match": type_match,
        }

    def _combine_scores(self, scores: dict) -> float:
        """加权平均"""
        total_weight = sum(self.weights.values())
        if total_weight == 0:
            return 0.0
        return (
            sum(scores.get(dim, 0) * w for dim, w in self.weights.items())
            / total_weight
        )

    # ==================== 分数计算函数 ====================

    @staticmethod
    def _calc_hotness(access_count: int) -> float:
        """热度分数: min(1.0, log(1 + count) / 7.0)"""
        return min(1.0, math.log(1 + access_count) / 7.0)

    @staticmethod
    def _calc_recency(updated_at_str: str | None) -> float:
        """时间衰减分数: exp(-days / half_life)"""
        if not updated_at_str:
            return 0.0
        try:
            if isinstance(updated_at_str, datetime):
                updated = updated_at_str
            else:
                updated = datetime.fromisoformat(updated_at_str)

            now = datetime.now(timezone.utc)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            days = (now - updated).total_seconds() / 86400
            return math.exp(-days / 30.0)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _calc_level_score(memory_layer: int, query_layer: int | None) -> float:
        """层级偏好分数"""
        if query_layer is not None:
            # 指定了层级：精确匹配=1.0, 相邻=0.5, 其他=0.0
            if memory_layer == query_layer:
                return 1.0
            elif abs(memory_layer - query_layer) == 1:
                return 0.5
            return 0.0
        else:
            # 未指定层级：L1=1.0, L2=0.8, L0=0.6
            if memory_layer == 1:
                return 1.0
            elif memory_layer == 2:
                return 0.8
            return 0.6

    @staticmethod
    def _calc_type_match(memory_type: str, query_type: str | None) -> float:
        """类型匹配分数"""
        if not query_type or query_type == "all":
            return 0.5  # 不限定类型时给中等分
        if memory_type == query_type:
            return 1.0
        # resource 和 skill 有一定关联
        related_pairs = {("resource", "skill"), ("skill", "resource")}
        if (memory_type, query_type) in related_pairs:
            return 0.3
        return 0.1
