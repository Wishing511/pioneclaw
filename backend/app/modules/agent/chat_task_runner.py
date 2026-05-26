"""
ChatTaskRunner — 包装 AgentLoop.process_message() 并将输出写入 buffer

设计原则：
- 不修改 AgentLoop 内部逻辑
- 通过包装 async generator 拦截所有 yield 的 chunk
- 将原始 chunk 解析为结构化事件写入 ChatTaskBuffer
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

from app.modules.agent.chat_task_buffer import ChatTaskBuffer
from app.modules.agent.loop import AgentLoop

logger = logging.getLogger(__name__)

# 复用 chat.py 中的正则表达式（零侵入 loop.py）
THINKING_PATTERN = re.compile(r"<!--THINKING:(.*?)-->", re.DOTALL)
TOOL_START_PATTERN = re.compile(r"<!--TOOL_START:(.*?)-->")
TOOL_RESULT_PATTERN = re.compile(r"<!--TOOL_RESULT:(.*?)-->", re.DOTALL)
TOOL_ERROR_PATTERN = re.compile(r"<!--TOOL_ERROR:(.*?)-->", re.DOTALL)


class ChatTaskRunner:
    """包装 AgentLoop 执行并将输出写入 buffer"""

    def __init__(
        self,
        agent_loop: AgentLoop,
        task_id: str,
        buffer: ChatTaskBuffer,
    ):
        self.agent_loop = agent_loop
        self.task_id = task_id
        self.buffer = buffer
        self._thinking_buffer = ""
        self._content_buffer = ""
        self._last_good_content = ""

    async def run(
        self,
        message: str,
        context: list[dict] | None = None,
        system_prompt: str | None = None,
        cancel_token=None,
    ) -> dict[str, Any]:
        """
        执行 AgentLoop 并将所有输出写入 buffer

        Returns:
            执行结果字典（用于持久化到 DB）
        """
        start_time = time.time()

        try:
            async for raw_chunk in self.agent_loop.process_message(
                message=message,
                context=context,
                system_prompt=system_prompt,
                cancel_token=cancel_token,
                yield_intermediate=True,
                use_sse=True,
            ):
                await self._process_chunk(raw_chunk)

            latency_ms = int((time.time() - start_time) * 1000)

            # 构建最终结果
            final_response = self._content_buffer.strip() or self._last_good_content.strip()

            # 如果 LLM 没有文字回复但调用了工具
            if not final_response and self.agent_loop.last_tool_results:
                tool_names = list(self.agent_loop.last_tool_results.keys())
                if tool_names:
                    final_response = (
                        f"已执行 {len(tool_names)} 个工具，请查看上方工具结果。"
                    )

            result = {
                "status": "completed",
                "final_response": final_response,
                "thinking_content": self._thinking_buffer if self._thinking_buffer else None,
                "latency_ms": latency_ms,
                "input_tokens": self.agent_loop.provider.last_input_tokens or 0,
                "output_tokens": self.agent_loop.provider.last_output_tokens or 0,
                "iterations": getattr(self.agent_loop, "_iteration", 0),
            }

            # 标记完成并获取归档数据（done 事件由 consume_from 统一 yield）
            output_chunks = await self.buffer.mark_completed(result)
            result["output_chunks"] = output_chunks

            return result

        except asyncio.CancelledError:
            await self.buffer.mark_failed("任务已取消")
            raise

        except Exception as e:
            logger.error(f"ChatTaskRunner error: {e}", exc_info=True)
            await self.buffer.mark_failed("任务执行失败，请稍后重试")
            raise

    async def _process_chunk(self, raw_chunk: str) -> None:
        """解析原始 chunk 并写入 buffer"""

        # 防御性处理：确保 raw_chunk 是字符串
        if not isinstance(raw_chunk, str):
            if isinstance(raw_chunk, dict):
                try:
                    raw_chunk = json.dumps(raw_chunk, ensure_ascii=False)
                except TypeError:
                    raw_chunk = str(raw_chunk)
            else:
                raw_chunk = str(raw_chunk)

        # 提取思考内容
        for match in THINKING_PATTERN.finditer(raw_chunk):
            thinking = match.group(1)
            self._thinking_buffer += thinking
            await self.buffer.append({
                "type": "thinking",
                "content": thinking,
            })

        # 提取工具事件
        for match in TOOL_START_PATTERN.finditer(raw_chunk):
            try:
                tool_data = json.loads(match.group(1))
                await self.buffer.append({
                    "type": "tool_start",
                    "name": tool_data.get("name", ""),
                    "tool_call_id": tool_data.get("tool_call_id", ""),
                })
            except json.JSONDecodeError:
                pass

        for match in TOOL_RESULT_PATTERN.finditer(raw_chunk):
            try:
                tool_data = json.loads(match.group(1))
                await self.buffer.append({
                    "type": "tool_result",
                    "name": tool_data.get("name", ""),
                    "tool_call_id": tool_data.get("tool_call_id", ""),
                    "result": tool_data.get("result", ""),
                    "duration_ms": tool_data.get("duration_ms"),
                })
            except json.JSONDecodeError:
                pass

        for match in TOOL_ERROR_PATTERN.finditer(raw_chunk):
            try:
                tool_data = json.loads(match.group(1))
                await self.buffer.append({
                    "type": "tool_error",
                    "name": tool_data.get("name", ""),
                    "tool_call_id": tool_data.get("tool_call_id", ""),
                    "error": tool_data.get("error", ""),
                    "duration_ms": tool_data.get("duration_ms"),
                })
            except json.JSONDecodeError:
                pass

        # 提取非标记内容
        clean = THINKING_PATTERN.sub("", raw_chunk)
        clean = TOOL_START_PATTERN.sub("", clean)
        clean = TOOL_RESULT_PATTERN.sub("", clean)
        clean = TOOL_ERROR_PATTERN.sub("", clean)

        # 处理迭代边界
        is_boundary = "[思考中...]" in clean
        if is_boundary:
            if self._content_buffer.strip():
                self._last_good_content = self._content_buffer
            self._content_buffer = ""
            await self.buffer.append({"type": "new_iteration"})

        # 清理标记
        clean = clean.replace("[思考中...]", "")
        clean = re.sub(r"\[达到最大迭代次数 \d+\]", "", clean)
        if is_boundary:
            clean = clean.strip()
        else:
            clean = clean.strip(" \t\r")

        if clean:
            self._content_buffer += clean
            self._last_good_content = ""
            await self.buffer.append({
                "type": "content",
                "content": clean,
            })
