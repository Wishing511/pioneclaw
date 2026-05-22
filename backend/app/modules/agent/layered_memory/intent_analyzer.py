"""
IntentAnalyzer — 查询意图分析

通过 LLM 分析查询意图，包括：
- 意图类型（查询/操作/导航等）
- 优化后的查询文本
- 上下文类型偏好
- 搜索范围
- 关键实体
"""

from dataclasses import dataclass, field

from loguru import logger

INTENT_ANALYSIS_PROMPT = """分析以下查询的意图，返回 JSON 格式：

查询: {query}

请返回:
{{
    "intent": "query|action|navigation|comparison",
    "optimized_query": "优化后的查询文本，去除停用词，保留核心概念",
    "context_type": "memory|resource|skill|all",
    "scope": "session|user|agent|global",
    "entities": ["关键实体1", "关键实体2"],
    "confidence": 0.0-1.0
}}

仅返回 JSON，不要其他内容。"""


@dataclass
class IntentResult:
    """意图分析结果"""

    intent: str = "query"  # query/action/navigation/comparison
    optimized_query: str = ""  # 优化后的查询
    context_type: str = "all"  # memory/resource/skill/all
    scope: str = "global"  # session/user/agent/global
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.5


class IntentAnalyzer:
    """查询意图分析器"""

    def __init__(self, llm_caller=None):
        """
        Args:
            llm_caller: LLM 调用函数，签名 async (prompt: str) -> str
                        如果为 None，使用基于规则的分析
        """
        self.llm_caller = llm_caller

    async def analyze(self, query: str, context: str | None = None) -> IntentResult:
        """分析查询意图"""
        if self.llm_caller:
            try:
                prompt = INTENT_ANALYSIS_PROMPT.format(query=query)
                if context:
                    prompt += f"\n\n上下文: {context[:500]}"

                response = await self.llm_caller(prompt)
                return self._parse_response(response, query)
            except Exception as e:
                logger.warning(f"LLM intent analysis failed, using rule-based: {e}")

        # 降级：基于规则的分析
        return self._rule_based_analyze(query)

    def _parse_response(self, response: str, original_query: str) -> IntentResult:
        """解析 LLM 返回的 JSON"""
        import json

        try:
            # 清理 JSON
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])

            data = json.loads(text)
            return IntentResult(
                intent=data.get("intent", "query"),
                optimized_query=data.get("optimized_query", original_query),
                context_type=data.get("context_type", "all"),
                scope=data.get("scope", "global"),
                entities=data.get("entities", []),
                confidence=data.get("confidence", 0.5),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse intent response: {e}")
            return self._rule_based_analyze(original_query)

    def _rule_based_analyze(self, query: str) -> IntentResult:
        """基于规则的意图分析（降级方案）"""
        result = IntentResult(optimized_query=query)

        query_lower = query.lower()

        # 意图判断
        if any(kw in query_lower for kw in ["怎么", "如何", "怎样", "方法", "步骤"]):
            result.intent = "action"
        elif any(kw in query_lower for kw in ["比较", "区别", "对比", "不同"]):
            result.intent = "comparison"
        elif any(kw in query_lower for kw in ["在哪", "哪里", "位置", "路径"]):
            result.intent = "navigation"

        # 上下文类型推断
        if any(kw in query_lower for kw in ["技能", "skill", "工具", "能力"]):
            result.context_type = "skill"
        elif any(kw in query_lower for kw in ["资源", "文档", "资料", "resource"]):
            result.context_type = "resource"
        elif any(kw in query_lower for kw in ["记忆", "回忆", "之前", "上次"]):
            result.context_type = "memory"

        # 简单实体提取（提取引号内或明显的关键词）
        import re

        entities = re.findall(r'["""]([^"""]+)["""]', query)
        if not entities:
            # 提取 2 字以上的中文词
            entities = re.findall(r"[\u4e00-\u9fff]{2,}", query)[:3]
        result.entities = entities

        return result
