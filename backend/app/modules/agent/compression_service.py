"""
ContextCompressionService — 唯一压缩入口

职责：
1. auto_prune(): AgentLoop 每轮自动调用（Snip -> MicroCompact -> Compactor）
2. manual_compact(): Web UI / /compact 命令调用（强制 LLM 摘要）
3. emergency_compact(): prompt_too_long 时调用（激进丢弃）
4. estimate_or_read_usage(): 优先真实 token 用量，fallback 字符估算
5. build_compression_report(): 返回压缩统计报告

设计原则：
- 不要让 Web UI、AgentLoop、/compact 各自实现压缩逻辑
- 所有压缩操作走这里，保证行为一致
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.modules.agent.compactor import CompactionResult, Compactor
from app.modules.agent.context_pruner import ContextPruner, estimate_tokens
from app.modules.agent.token_budget import TokenBudget, TokenUsage

logger = logging.getLogger(__name__)


@dataclass
class CompressionReport:
    """压缩报告"""

    summary: str = ""
    removed_messages: int = 0
    kept_messages: int = 0
    saved_tokens: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    strategy: str = ""  # "snip", "microcompact", "compact", "emergency"


class ContextCompressionService:
    """
    上下文压缩服务 — 唯一入口
    """

    def __init__(
        self,
        budget: TokenBudget,
        compactor: Compactor | None = None,
        context_pruner: ContextPruner | None = None,
        file_tracker=None,
    ):
        self.budget = budget
        self.compactor = compactor
        self.context_pruner = context_pruner
        self.file_tracker = file_tracker

    def estimate_or_read_usage(
        self,
        messages: list[dict[str, Any]],
        provider,
    ) -> TokenUsage:
        """
        获取 token 用量。

        策略：
        - API 真实值（provider.last_input_tokens）可能滞后（不包含本轮新追加的工具结果）
        - 字符估算（estimate_tokens）能反映当前 messages 的实时大小，但精度较低
        - 取两者最大值，避免该压缩时不压缩

        source 标注实际采用的来源：
        - "api": 仅用了 API 值（messages 估算 <= API 值）
        - "estimated": 仅用了估算值（API 无值或估算更大）
        - "mixed": 两者都有，取了大值
        """
        # 1. 实时字符估算（反映当前 messages 大小）
        estimated = estimate_tokens(messages)

        # 2. API 真实值（可能滞后）
        real_input = 0
        real_output = 0
        if provider:
            real_input = getattr(provider, "last_input_tokens", 0) or 0
            real_output = getattr(provider, "last_output_tokens", 0) or 0

        # 3. 取最大值，避免遗漏本轮新追加的大工具结果
        if real_input > 0:
            if estimated > real_input:
                return TokenUsage(
                    input_tokens=estimated,
                    output_tokens=real_output,
                    source="estimated",
                )
            return TokenUsage(
                input_tokens=real_input,
                output_tokens=real_output,
                source="api",
            )

        # 无 API 值，完全依赖估算
        return TokenUsage(
            input_tokens=estimated,
            output_tokens=0,
            source="estimated",
        )

    def build_usage_info(
        self,
        messages: list[dict[str, Any]],
        provider,
    ) -> dict[str, Any]:
        """返回前端可用的上下文使用率信息"""
        usage = self.estimate_or_read_usage(messages, provider)
        info = self.budget.to_dict(usage.input_tokens)
        info["output_tokens"] = usage.output_tokens
        info["source"] = usage.source
        return info

    async def auto_prune(
        self,
        messages: list[dict[str, Any]],
        provider,
    ) -> list[dict[str, Any]]:
        """
        AgentLoop 每轮自动调用。

        三层压缩：
        1. Snip（零成本）
        2. MicroCompact（低成本）
        3. Compactor（高成本，阈值触发）
        """
        # 读取 token 用量
        usage = self.estimate_or_read_usage(messages, provider)
        input_tokens = usage.input_tokens

        # 检查是否需要 Compactor
        should_compact = self.budget.should_compact(input_tokens)

        # Layer 1: Snip
        if self.context_pruner:
            snipped, snip_saved = self.context_pruner.snip_prune(messages)
            messages = snipped
            logger.debug(f"Snip: saved ~{snip_saved} chars")

        # Layer 2: MicroCompact
        if self.context_pruner:
            messages, micro_saved = self.context_pruner.micro_compact(messages)
            if micro_saved > 0:
                logger.debug(f"MicroCompact: saved ~{micro_saved} chars")

        # Layer 3: Compactor（阈值触发）
        if should_compact and self.compactor:
            logger.info(
                f"Auto compact triggered: {input_tokens} tokens >= "
                f"threshold {self.budget.compact_threshold}"
            )
            try:
                result = await self.compactor.compact(messages)
                if result.summary and result.removed_messages > 0:
                    messages = self._rebuild_after_compact(messages, result)
                    logger.info(
                        f"Compacted: removed {result.removed_messages} messages, "
                        f"kept {result.kept_messages}, saved ~{result.saved_tokens} tokens"
                    )
                else:
                    logger.info("Compact skipped: no messages to remove")
            except Exception as e:
                logger.error(f"Auto compact failed: {e}, preserving original messages")
                # 失败时不丢弃历史，返回当前（已 Snip + MicroCompact）消息

        return messages

    async def manual_compact(
        self,
        messages: list[dict[str, Any]],
        instruction: str | None = None,
    ) -> tuple[CompressionReport, list[dict[str, Any]]]:
        """
        手动压缩 — Web UI 按钮或 /compact 命令调用。

        强制使用 Compactor，忽略阈值。
        如果提供了 instruction，会注入摘要提示词中作为额外约束。

        Returns:
            Tuple[CompressionReport, List[Dict]]: (压缩报告, 压缩后的消息列表)
        """
        if not self.compactor:
            report = CompressionReport(
                strategy="compact", summary="Compactor not configured"
            )
            return report, messages

        before_tokens = self.estimate_or_read_usage(
            messages, provider=None
        ).input_tokens

        # 注入自定义指令（如果有）
        if instruction:
            pass

        try:
            # 先走 MicroCompact 减少 token 压力
            if self.context_pruner:
                messages, _ = self.context_pruner.micro_compact(messages)

            result = await self.compactor.compact(
                messages, instruction=instruction, force=True
            )

            if result.summary and result.removed_messages > 0:
                messages = self._rebuild_after_compact(messages, result)

            after_tokens = self.estimate_or_read_usage(
                messages, provider=None
            ).input_tokens

            report = CompressionReport(
                summary=result.summary or "",
                removed_messages=result.removed_messages,
                kept_messages=result.kept_messages,
                saved_tokens=result.saved_tokens,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                strategy="compact",
            )
            return report, messages
        except Exception as e:
            logger.error(f"Manual compact failed: {e}")
            report = CompressionReport(
                strategy="compact",
                summary=f"压缩失败: {e}",
                before_tokens=before_tokens,
            )
            return report, messages

    async def emergency_compact(
        self,
        messages: list[dict[str, Any]],
        attempt: int = 1,
    ) -> list[dict[str, Any]]:
        """
        prompt_too_long 时的应急压缩。

        尝试策略：
        1. 强制 MicroCompact 所有可清理工具结果
        2. 剥离旧工具输入/输出，只保留元信息
        3. 丢弃最旧非 system 消息组
        """
        logger.warning(f"Emergency compact attempt {attempt}")

        if attempt == 1:
            # 第 1 次：强制 MicroCompact（保留 0 个，全部清理）
            if self.context_pruner:
                # 临时创建保留 0 个的 MicroCompacter
                from app.modules.agent.context_pruner import MicroCompacter

                emergency_micro = MicroCompacter(keep_recent=0)
                messages, saved = emergency_micro.prune(messages)
                logger.info(
                    f"Emergency L1: cleared all tool results, saved ~{saved} chars"
                )
            return messages

        elif attempt == 2:
            # 第 2 次：截断所有工具结果内容到最小元信息
            for msg in messages:
                if msg.get("role") == "tool":
                    tool_name = msg.get("tool_name", msg.get("name", ""))
                    msg["content"] = (
                        f"[tool_result: {tool_name}, content stripped for emergency]"
                    )
            logger.info("Emergency L2: stripped all tool result content")
            return messages

        elif attempt == 3:
            # 第 3 次：丢弃最旧的非 system 消息组，保留 system + 最近 10 条
            system_msgs = [m for m in messages if m.get("role") == "system"]
            other_msgs = [m for m in messages if m.get("role") != "system"]
            keep_recent = min(10, len(other_msgs))
            kept = other_msgs[-keep_recent:] if keep_recent > 0 else []
            dropped = len(other_msgs) - keep_recent
            logger.info(
                f"Emergency L3: dropped {dropped} old messages, kept {keep_recent}"
            )
            return system_msgs + kept

        return messages

    def _rebuild_after_compact(
        self,
        messages: list[dict[str, Any]],
        result: CompactionResult,
    ) -> list[dict[str, Any]]:
        """压缩后重建消息列表"""
        if not result.summary:
            return messages

        # 保留 system prompt
        system_prompts = [m for m in messages if m.get("role") == "system"]

        # 保留最近消息
        keep_recent = getattr(
            getattr(self.compactor, "config", None),
            "keep_recent_messages",
            8,
        )
        recent = messages[-keep_recent:] if len(messages) > keep_recent else messages

        # 重建：system + 摘要 + 最近消息
        rebuilt = []
        if system_prompts:
            rebuilt.extend(system_prompts)

        rebuilt.append(
            {
                "role": "user",
                "content": f"[Previous conversation summary]\n{result.summary}",
            }
        )
        rebuilt.extend(recent)

        # Phase 6: 恢复关键文件内容（如果 FileTracker 已注入）
        if self.file_tracker and self.file_tracker.record_count > 0:
            restored_files = self.file_tracker.get_recent(
                max_tokens=50_000, max_files=5
            )
            if restored_files:
                restored_lines = ["[Restored files after context compression]"]
                for record in restored_files:
                    restored_lines.append(
                        f"- {record.path} "
                        f"(edited={record.was_edited}, "
                        f"tokens={record.estimated_tokens})"
                    )
                rebuilt.append(
                    {
                        "role": "system",
                        "content": "\n".join(restored_lines),
                    }
                )
                logger.info(
                    f"Restored {len(restored_files)} files after compaction via service: "
                    f"{[r.path for r in restored_files]}"
                )

        return rebuilt
