"""
共享 LLM Provider — 供 Chat、Agent 执行等模块复用

支持 Anthropic / OpenAI 兼容接口，含工具调用、SSE 流式、快速模式等。
"""

import json
import logging

import httpx

from app.models import AIModelConfig

logger = logging.getLogger(__name__)


class PromptTooLongError(Exception):
    """上下文过长错误 — 触发应急压缩"""

    pass


class SimpleLLMProvider:
    """LLM Provider — 直接使用 AIModelConfig 调用 API"""

    def __init__(self, config: AIModelConfig):
        self.config = config
        self.model = config.model_name
        self.api_key = config.api_key
        self.api_base = config.base_url
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens
        # Token 追踪
        self.last_input_tokens = 0
        self.last_output_tokens = 0

    async def chat_stream(
        self,
        messages: list,
        tools: list = None,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
    ):
        """非流式调用 LLM（一次性返回完整响应）"""
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens

        logger.info(
            f"SimpleLLMProvider.chat_stream: provider={self.config.provider}, tools_count={len(tools) if tools else 0}"
        )

        # 构建请求
        if self.config.provider == "anthropic":
            url = self.api_base or "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if messages and messages[0]["role"] == "system":
                body["system"] = messages.pop(0)["content"]
        else:
            url = self.api_base or "https://api.openai.com/v1/chat/completions"
            if not url.endswith("/chat/completions"):
                url = url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if tools and len(tools) > 0:
                body["tools"] = tools
                body["tool_choice"] = "auto"
                logger.info(f"Tools added to request: {len(tools)} tools")
            else:
                logger.warning("No tools in request!")
            # 快速模式：禁用 DeepSeek 推理/思考
            if getattr(self, "fast_mode", False):
                body["thinking"] = {"type": "disabled"}

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                logger.debug(f"Request to {url}: model={model}")
                try:
                    response = await client.post(url, headers=headers, json=body)
                except Exception as req_error:
                    logger.error(f"Request error: {req_error}", exc_info=True)
                    yield {"error": f"请求错误: {req_error}"}
                    return

                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception as json_error:
                        logger.error(f"JSON parse error: {json_error}")
                        yield {"error": f"JSON 解析错误: {json_error}"}
                        return

                    usage = data.get("usage", {})
                    self.last_input_tokens = usage.get("prompt_tokens", 0)
                    self.last_output_tokens = usage.get("completion_tokens", 0)

                    if self.config.provider == "anthropic":
                        content = data.get("content", [{}])[0].get("text", "")
                        yield {"content": content, "finish_reason": "stop"}
                    else:
                        message = data.get("choices", [{}])[0].get("message", {})
                        content = message.get("content", "") or ""
                        tool_calls = message.get("tool_calls", [])
                        reasoning_content = message.get("reasoning_content", "")

                        if tool_calls:
                            logger.info(f"API returned {len(tool_calls)} tool_calls")
                            for tc in tool_calls:
                                func = tc.get("function", {})
                                tc_id = tc.get("id", "")
                                if isinstance(tc_id, list):
                                    tc_id = str(tc_id[0]) if tc_id else ""
                                elif not isinstance(tc_id, str):
                                    tc_id = str(tc_id) if tc_id else ""
                                yield {
                                    "tool_call": {
                                        "id": tc_id,
                                        "index": tc.get("index", 0),
                                        "name": func.get("name"),
                                        "arguments": func.get("arguments", "{}"),
                                    },
                                    "finish_reason": "tool_calls",
                                    "reasoning_content": reasoning_content,
                                }
                        else:
                            yield {
                                "content": content,
                                "finish_reason": "stop",
                                "reasoning_content": reasoning_content,
                            }
                else:
                    error = response.text
                    try:
                        error_json = response.json()
                        if "error" in error_json:
                            error = error_json["error"].get("message", error)
                    except Exception:
                        pass
                    yield {"error": f"API 错误 ({response.status_code}): {error[:500]}"}

        except Exception as e:
            import traceback

            logger.error(f"chat_stream exception: {e}\n{traceback.format_exc()}")
            yield {"error": str(e)}

    async def chat_stream_sse(
        self,
        messages: list,
        tools: list = None,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
    ):
        """真正的 SSE 流式调用 LLM（用于前端实时展示思考过程）"""
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens

        logger.info(
            f"chat_stream_sse: provider={self.config.provider}, tools={len(tools) if tools else 0}"
        )

        url = self.api_base or "https://api.openai.com/v1/chat/completions"
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
        }
        if tools and len(tools) > 0:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        # 快速模式：禁用 DeepSeek 推理/思考
        if getattr(self, "fast_mode", False):
            body["thinking"] = {"type": "disabled"}

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                request = client.build_request("POST", url, headers=headers, json=body)
                response = await client.send(request, stream=True)

                if response.status_code != 200:
                    error = await response.aread()
                    try:
                        err_json = json.loads(error)
                        msg = err_json.get("error", {}).get("message", str(error))
                    except Exception:
                        msg = error.decode("utf-8", errors="replace")[:500]
                    yield {"error": f"API 错误 ({response.status_code}): {msg}"}
                    return

                tool_call_deltas: dict[int, dict] = {}
                reasoning_buffer = ""

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    usage = data.get("usage", {})
                    if usage:
                        self.last_input_tokens = usage.get("prompt_tokens", 0)
                        self.last_output_tokens = usage.get("completion_tokens", 0)

                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason") or ""

                    # reasoning_content（思考过程实时输出）
                    if delta.get("reasoning_content"):
                        reasoning_buffer += delta["reasoning_content"]
                        yield {
                            "reasoning_content": delta["reasoning_content"],
                            "finish_reason": None,
                        }

                    # tool_calls 增量
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_call_deltas:
                            tool_call_deltas[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.get("id"):
                            tool_call_deltas[idx]["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            tool_call_deltas[idx]["name"] = tc["function"]["name"]
                        if tc.get("function", {}).get("arguments"):
                            tool_call_deltas[idx]["arguments"] += tc["function"][
                                "arguments"
                            ]

                        yield {
                            "tool_call": {
                                "index": idx,
                                "id": tool_call_deltas[idx]["id"],
                                "name": tool_call_deltas[idx]["name"],
                                "arguments": tc["function"].get("arguments", ""),
                            },
                            "finish_reason": None,
                        }

                    # 文本内容（逐 chunk 实时发出）
                    if delta.get("content"):
                        yield {"content": delta["content"], "finish_reason": None}

                    # 结束信号
                    if finish_reason:
                        if tool_call_deltas:
                            for idx in tool_call_deltas:
                                tc_id = tool_call_deltas[idx]["id"]
                                if isinstance(tc_id, list):
                                    tool_call_deltas[idx]["id"] = (
                                        str(tc_id[0]) if tc_id else ""
                                    )
                                elif not isinstance(tc_id, str):
                                    tool_call_deltas[idx]["id"] = (
                                        str(tc_id) if tc_id else ""
                                    )
                            yield {"finish_reason": "tool_calls"}
                        else:
                            yield {"finish_reason": finish_reason}

        except httpx.RequestError as e:
            logger.error(f"chat_stream_sse request error: {e}", exc_info=True)
            yield {"error": f"请求错误: {e}"}
        except Exception as e:
            import traceback

            logger.error(f"chat_stream_sse exception: {e}\n{traceback.format_exc()}")
            yield {"error": str(e)}
