"""
TokenBudget — 统一 token 预算计算

职责：
1. 集中管理所有阈值计算（compact / warning / block）
2. 小上下文窗口保护（避免负阈值）
3. 根据真实 token 用量返回状态（normal / warning / caution / critical）

对标 Claude Code 的阈值策略，但简化为一个类：
- effective_window = context_window - max_output_tokens
- compact_threshold = effective_window - safety_buffer
- warning_threshold = compact_threshold - 20_000
- hard_block_threshold = effective_window - 3_000
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenUsage:
    """Token 用量记录"""

    input_tokens: int
    output_tokens: int
    source: Literal["api", "estimated"]  # api=LLM 返回的真实值, estimated=字符估算


@dataclass
class TokenBudget:
    """Token 预算 — 统一管理上下文窗口和压缩阈值"""

    context_window: int
    max_output_tokens: int = 20_000  # 预留输出空间
    safety_buffer: int = 13_000  # 安全缓冲

    def __post_init__(self):
        # 小上下文窗口保护
        if self.context_window <= self.max_output_tokens + self.safety_buffer:
            effective = max(1, self.context_window // 10)
            logger.warning(
                f"Small context window detected ({self.context_window}), "
                f"auto-scaling buffer to {effective}"
            )
            object.__setattr__(self, "max_output_tokens", effective)
            object.__setattr__(self, "safety_buffer", 0)

    @property
    def effective_window(self) -> int:
        """有效上下文窗口 = 总窗口 - 预留输出"""
        return self.context_window - self.max_output_tokens

    @property
    def compact_threshold(self) -> int:
        """自动压缩阈值 = 有效窗口 - 安全缓冲"""
        return self.effective_window - self.safety_buffer

    @property
    def warning_threshold(self) -> int:
        """警告阈值（提前 20K 提醒）"""
        return self.compact_threshold - 20_000

    @property
    def hard_block_threshold(self) -> int:
        """硬阻塞阈值（接近上限，必须压缩或新开）"""
        return self.effective_window - 3_000

    def usage_percent(self, input_tokens: int) -> float:
        """计算使用率百分比"""
        if self.context_window <= 0:
            return 0.0
        return round(input_tokens / self.context_window * 100, 2)

    def get_status(self, input_tokens: int) -> str:
        """
        根据 token 用量返回状态：
        - normal:   < 70%
        - warning:  70%-80%
        - caution:  80%-90%（提示可压缩）
        - critical: > 90%（即将自动压缩）
        - block:    >= hard_block（必须压缩或新开会话）
        """
        if input_tokens >= self.hard_block_threshold:
            return "block"
        pct = self.usage_percent(input_tokens)
        if pct >= 90:
            return "critical"
        if pct >= 80:
            return "caution"
        if pct >= 70:
            return "warning"
        return "normal"

    def should_compact(self, input_tokens: int) -> bool:
        """判断是否达到自动压缩阈值"""
        return input_tokens >= self.compact_threshold

    def to_dict(self, input_tokens: int) -> dict:
        """返回前端可用的完整信息"""
        return {
            "input_tokens": input_tokens,
            "output_tokens": 0,  # 由调用方填充
            "context_window": self.context_window,
            "effective_window": self.effective_window,
            "usage_percent": self.usage_percent(input_tokens),
            "status": self.get_status(input_tokens),
            "compact_threshold": self.compact_threshold,
            "warning_threshold": self.warning_threshold,
            "hard_block_threshold": self.hard_block_threshold,
        }


# ---------------------------------------------------------------------------
# 模型 context_window 映射
# ---------------------------------------------------------------------------

_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-7-sonnet": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_384,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-coder": 64_000,
    "deepseek-reasoner": 64_000,
    # Qwen
    "qwen-turbo": 128_000,
    "qwen-plus": 128_000,
    "qwen-max": 32_000,
    # 默认
    "default": 128_000,
}


def get_context_window_for_model(model: str | None) -> int:
    """根据模型名称获取上下文窗口大小

    匹配策略：按前缀长度降序匹配，避免短前缀误匹配长模型名
    （例如 \"gpt-4\" 不应匹配 \"gpt-4o-mini\"）
    """
    if not model:
        return _MODEL_CONTEXT_WINDOWS["default"]
    model_lower = model.lower()
    # 按前缀长度降序，确保更精确的匹配优先
    sorted_prefixes = sorted(
        _MODEL_CONTEXT_WINDOWS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for prefix, window in sorted_prefixes:
        if prefix in model_lower:
            return window
    return _MODEL_CONTEXT_WINDOWS["default"]


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

def estimate_tokens(messages: list[dict[str, Any]], tools: list[dict] | None = None) -> int:
    """粗略估算消息列表的 token 数

    策略：
    1. 优先使用消息中的已有估算值
    2. 字符数 / 4 作为粗略估算
    3. 工具定义单独估算

    精度说明：
    - 英文文本：len/4 近似于真实 token 数（大部分 tokenizer 约 4 chars/token）
    - 中文文本：严重低估。真实 token 数约为字符数的 1.5~2 倍（UTF-8 编码下中文
      通常占 3 字节，tokenizer 按字节或子词切分），此处仅按字符数/4 估算，
      实际会远小于真实值，导致压缩触发偏晚
    - 代码：含大量符号和空白，各模型 tokenizer 差异大，估算误差可达 30%~50%
    - 用途：仅用于触发阈值判断（compact/warning），不用于计费或精确预算控制
      真实 token 用量应以 API 返回的 usage 为准
    """
    total = 0

    # 估算消息内容
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            # 多模态内容
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    total += len(item["text"]) // 4

        # tool_calls 和 tool_results
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                total += len(str(tc)) // 4
        if msg.get("tool_call_id"):
            total += len(str(msg.get("content", ""))) // 4

    # 估算工具定义
    if tools:
        for tool in tools:
            total += len(str(tool)) // 6  # JSON schema 更密集

    return max(1, total)

