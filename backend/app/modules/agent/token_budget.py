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
from typing import Literal

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
