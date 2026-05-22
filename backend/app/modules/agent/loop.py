"""
Agent Loop - 核心 Agent 循环处理逻辑

借鉴自 AIE 项目的 loop.py，实现 ReAct 推理循环。
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
)

# Guardrails 和 Tool Hooks（借鉴 CrewAI + PraisonAI）
from app.modules.agent.guardrails import (
    Guardrail,
    GuardrailExecutor,
    ValidationResult,
)

# 中断与恢复机制（借鉴 LangGraph）
from app.modules.agent.interrupt import (
    Checkpoint,
    InterruptManager,
    InterruptOption,
    InterruptPoint,
    InterruptReason,
    InterruptStatus,
    get_interrupt_manager,
    interrupt_options,
)

# 使用新的 CancellationToken
from app.modules.agent.task_manager import CancellationToken
from app.modules.agent.tool_hooks import (
    HookContext,
    HookEvent,
    ToolHook,
    ToolHookRunner,
)

# 执行追踪（借鉴 LangSmith）
from app.modules.agent.tracing import (
    AgentTracer,
    Span,
    SpanKind,
    SpanStatus,
    TokenUsage,
    Trace,
    get_tracer,
)

if TYPE_CHECKING:
    pass

from app.core.bash_safety import (
    CommandAssessment,
    CommandConfirmationRequired,
    DangerLevel,
)
from app.core.permission_mode import (
    PermissionChecker,
    PermissionMode,
    resolve_permission_mode,
)
from app.core.recovery_recipes import (
    RecoverableToolError,
    RecoveryContext,
    RecoveryExecutor,
    classify_error,
    recipe_for,
)
from app.core.sandbox import SensitiveFileAccessRequired
from app.core.sandbox_policy import resolve_tool_policy
from app.modules.tools.scheduler import partition_tool_calls, run_concurrent_batch

# 新工具系统（ai-agent-toolkit 架构移植）
from app.modules.tools.types import ToolUse as _ToolUse

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    """Agent 执行状态"""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_INTERRUPT = "waiting_interrupt"  # 等待中断响应
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 保留旧的 CancelToken 作为别名，向后兼容
CancelToken = CancellationToken


@dataclass
class ToolCall:
    """工具调用"""

    id: str
    name: str
    arguments: dict[str, Any]
    result: str | None = None
    error: str | None = None


@dataclass
class AgentIteration:
    """单次迭代记录"""

    iteration: int
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    latency_ms: int = 0


@dataclass
class AgentExecutionResult:
    """Agent 执行结果"""

    status: AgentStatus
    content: str
    iterations: list[AgentIteration]
    total_iterations: int
    total_tool_calls: int
    total_tokens: int
    latency_ms: int
    error: str | None = None


class AgentLoop:
    """Agent 主循环类 - 处理消息、调用 LLM、执行工具、生成响应

    ReAct 循环：
    1. 接收用户消息
    2. 调用 LLM 生成响应
    3. 如果有工具调用，执行工具
    4. 将工具结果加入上下文
    5. 重复 2-4 直到 LLM 不再调用工具或达到最大迭代次数

    支持：
    - Key 轮换重试
    - 模型覆盖配置
    - 会话级参数调整
    - 工具调用去重
    - 请求追踪 ID
    - 插件事件回调
    """

    MAX_KEY_ROTATION_RETRIES = 3  # Key 轮换最大重试次数

    def __init__(
        self,
        provider,  # LLM 提供商（调用 chat_stream）
        tools: Any | None = None,  # 工具注册表
        model: str | None = None,
        max_iterations: int = 25,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_keys: list[str] | None = None,  # 多 Key 轮换
        session_id: str | None = None,  # WebSocket 会话 ID
        agent_config: dict[str, Any] | None = None,  # Agent 配置（含 tool_policy）
        permission_mode: str | None = None,  # 权限模式（若不传则按角色解析）
        user_role: Any | None = None,  # UserRole（用于确定权限上限）
        handoffs: list[Any] | None = None,  # Handoff 列表（借鉴 PraisonAI）
        agent_id: str | None = None,  # Agent ID（用于 handoff 追踪）
        agent_name: str | None = None,  # Agent 名称
        guardrails: list[Guardrail] | None = None,  # 输出验证器（借鉴 CrewAI）
        tool_hooks: ToolHookRunner | None = None,  # 工具拦截器（借鉴 PraisonAI）
        interrupt_manager: InterruptManager
        | None = None,  # 中断管理器（借鉴 LangGraph）
        tracer: AgentTracer | None = None,  # 执行追踪器（借鉴 LangSmith）
        inbox_queue: Any | None = None,  # Agent 间消息收件箱（asyncio.Queue）
        system_prompt: str | None = None,  # 系统提示词（覆盖默认）
        # Context 压缩（Phase 1: Context 卫生）
        compactor: Any | None = None,  # Compactor 实例（LLM 级压缩）
        context_pruner: Any | None = None,  # ContextPruner 实例（Microcompact + Snip）
        compression_service: Any | None = None,  # ContextCompressionService 统一入口
        file_tracker: Any | None = None,  # FileTracker 实例（Phase 6: 压缩后文件恢复）
        security_client: Any | None = None,  # 安全网关 HTTP Client
    ):
        """
        初始化 AgentLoop

        Args:
            provider: LLM 提供商，需要有 chat_stream 方法
            tools: 工具注册表
            model: 模型名称
            max_iterations: 最大迭代次数
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            temperature: 温度参数
            max_tokens: 最大 token 数
            api_keys: API Key 列表（用于轮换）
            session_id: WebSocket 会话 ID（用于实时通知）
            agent_config: Agent 配置字典（含 tool_policy 沙箱策略）
            permission_mode: 权限模式（若不传则按 user_role + agent_config 解析）
            user_role: UserRole 枚举（用于确定权限上限）
            handoffs: Handoff 列表，用于委托给其他 Agent（借鉴 PraisonAI）
            agent_id: Agent ID（用于 handoff 追踪）
            agent_name: Agent 名称
            guardrails: 输出验证器列表（借鉴 CrewAI Guardrails）
            tool_hooks: 工具拦截器（借鉴 PraisonAI Tool Hooks）
            interrupt_manager: 中断管理器（借鉴 LangGraph interrupt/resume）
            tracer: 执行追踪器（借鉴 LangSmith tracing）
            inbox_queue: Agent 间消息收件箱（asyncio.Queue），非 None 时每轮迭代前检查消息
            compactor: Compactor 实例（LLM 级对话压缩），None 时禁用
            context_pruner: ContextPruner 实例（Microcompact + Snip），None 时禁用
            compression_service: ContextCompressionService 统一入口，None 时从 compactor + context_pruner 自动构建
        """
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.model = model
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.last_tool_results: dict[
            str, str
        ] = {}  # 记录最后一次工具调用结果（key=tool_call_id）
        self.last_tool_durations: dict[
            str, int
        ] = {}  # 记录最后一次工具调用耗时(ms)（key=tool_call_id）
        self.session_id = session_id  # WebSocket 会话 ID
        self.agent_config = agent_config or {}  # Agent 配置（含 tool_policy）

        # 权限模式与检查器
        self._permission_mode = (
            PermissionMode(permission_mode) if permission_mode else None
        )
        self._user_role = user_role
        self._permission_checker: PermissionChecker | None = None  # 延迟初始化

        # Handoff 支持（借鉴 PraisonAI）
        self._handoffs: list[Any] = handoffs or []
        self._agent_id = agent_id or str(uuid.uuid4())
        self._agent_name = agent_name or "Agent"

        # Guardrails 输出验证（借鉴 CrewAI）
        self._guardrails: list[Guardrail] = guardrails or []
        self._guardrail_executor: GuardrailExecutor | None = None
        if self._guardrails:
            self._guardrail_executor = GuardrailExecutor(self._guardrails)

        # Tool Hooks 拦截（借鉴 PraisonAI）
        self._tool_hooks = tool_hooks or ToolHookRunner()

        # 安全网关 ToolHook 注册（pre_tool_call）
        self._security_client = security_client
        if security_client:
            from app.modules.agent.tool_hooks import (
                HookContext,
                HookEvent,
                HookResult,
                ToolHook,
            )

            async def _security_tool_hook(ctx: HookContext) -> HookResult:
                result = await security_client.check_tool(
                    ctx.tool_name,
                    ctx.tool_args,
                    context={
                        "agent_id": ctx.agent_id,
                        "agent_name": ctx.agent_name,
                        "conversation_id": ctx.conversation_id,
                    },
                )
                if result.action == "block":
                    return HookResult(
                        skip_execution=True,
                        modified_result=f"Error: 安全拦截 - {result.reason}",
                    )
                return HookResult()

            self._tool_hooks.register(
                ToolHook(
                    event=HookEvent.BEFORE_TOOL,
                    callback=_security_tool_hook,
                    priority=0,  # 最高优先级
                )
            )

        # 中断与恢复机制（借鉴 LangGraph）
        self._interrupt_manager = interrupt_manager or get_interrupt_manager()
        self._current_interrupt: InterruptPoint | None = None
        self._status: AgentStatus = AgentStatus.IDLE

        # 执行追踪（借鉴 LangSmith）
        self._tracer = tracer or get_tracer()
        self._current_trace: Trace | None = None

        # Agent 间消息收件箱
        self._inbox_queue = inbox_queue

        # Context 压缩（Phase 1: Context 卫生）
        self._compactor = compactor
        self._context_pruner = context_pruner
        self._compression_service = compression_service

        # Phase 6: 压缩后文件恢复
        if file_tracker is not None:
            self._file_tracker = file_tracker
        else:
            from app.modules.agent.file_tracker import FileTracker

            self._file_tracker = FileTracker(max_files=5, max_tokens=50_000)

        # 工具调用去重
        self.seen_tool_call_ids: set[str] = set()

        # 请求追踪 ID
        self.request_trace_id: str | None = None

        # 插件事件回调
        self.tool_event_handler: Callable | None = None
        self.reasoning_event_handler: Callable | None = None

        # Stage VV: Post-turn 服务（记忆提取、session memory 等）
        self._post_turn_services: list = []

        # Key 轮换
        self._api_keys = api_keys or []
        self._key_rotator = None
        self._key_rotation_count = 0

        if self._api_keys:
            from app.modules.providers import get_key_rotator

            provider_id = getattr(provider, "provider_id", "default")
            self._key_rotator = get_key_rotator(provider_id, self._api_keys)

        logger.debug(
            f"AgentLoop initialized: max_iterations={max_iterations}, "
            f"max_retries={max_retries}, temperature={temperature}, "
            f"api_keys_count={len(self._api_keys)}"
        )

    def _resolve_execution_runtime(
        self,
        model_override: dict[str, Any] | None = None,
    ) -> tuple[Any, str | None, float, int, int]:
        """
        解析当前消息执行应使用的 provider 和模型参数

        Args:
            model_override: 可选的模型覆盖参数，来自会话运行时配置

        Returns:
            tuple: (provider, model, temperature, max_tokens, max_iterations)
        """
        base_provider = self.provider
        base_model = self.model
        base_temperature = self.temperature
        base_max_tokens = self.max_tokens
        base_max_iterations = self.max_iterations

        if not model_override:
            return (
                base_provider,
                base_model,
                base_temperature,
                base_max_tokens,
                base_max_iterations,
            )

        # 使用覆盖参数
        candidate_provider = base_provider
        candidate_model = model_override.get("model", base_model)
        candidate_temperature = model_override.get("temperature", base_temperature)
        candidate_max_tokens = model_override.get("max_tokens", base_max_tokens)
        candidate_max_iterations = model_override.get(
            "max_iterations", base_max_iterations
        )

        # 检查是否需要创建新的 provider（api_key 或 api_base 不同）
        override_api_key = model_override.get("api_key") or None
        override_api_base = model_override.get("api_base") or None

        # 当前 provider 的配置
        current_api_key = getattr(base_provider, "api_key", None) or ""
        current_api_base = getattr(base_provider, "api_base", None) or ""

        # 如果 api_key 或 api_base 不同，需要创建新的 provider
        if (
            override_api_key
            and override_api_key != current_api_key
            or override_api_base
            and override_api_base != current_api_base
        ):
            needs_new_provider = True
        else:
            needs_new_provider = False

        if needs_new_provider:
            # 使用覆盖的配置创建新 provider
            # 这里简化处理，直接修改当前 provider 的属性
            # 实际项目中可能需要创建新的 provider 实例
            logger.info(
                f"Using runtime override: "
                f"model={candidate_model}, api_base={override_api_base}"
            )
            if override_api_key:
                base_provider.api_key = override_api_key
            if override_api_base:
                base_provider.api_base = override_api_base

        return (
            candidate_provider,
            candidate_model,
            candidate_temperature,
            candidate_max_tokens,
            candidate_max_iterations,
        )

    async def _poll_inbox(self, timeout: float = 0.1) -> dict | None:
        """检查收件箱中是否有来自其他 Agent 的消息，非阻塞"""
        import asyncio

        try:
            return await asyncio.wait_for(self._inbox_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ==================== Stage VV: Post-Turn Hooks ====================

    def configure_post_turn(self, services: list) -> None:
        """配置 post-turn 服务列表，每项为 (name, service_instance)"""
        self._post_turn_services = services

    async def _run_post_turn(self, messages: list) -> None:
        """fire-and-forget 执行所有 post-turn 服务"""
        for _name, service in self._post_turn_services:
            try:
                # VV.1: MemoryExtractor (双轨写入)
                if hasattr(service, "extract_and_store"):
                    await service.extract_and_store(messages)
                # VV.2: ConversationSummarizer (Track 1)
                if hasattr(service, "summarize_conversation"):
                    token_count = (
                        sum(len(str(m.get("content", ""))) for m in messages) // 4
                    )
                    tool_call_count = sum(
                        1 for m in messages if m.get("role") == "tool"
                    )
                    if service.should_summarize(token_count, tool_call_count):
                        await service.summarize_conversation(messages)
                # VV.3: MagicDocUpdater
                if hasattr(service, "update_all"):
                    await service.update_all()
            except Exception:
                pass  # 静默失败，不影响主流程

    async def process_message(
        self,
        message: str,
        context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        cancel_token: CancelToken | None = None,
        yield_intermediate: bool = True,
        model_override: dict[str, Any] | None = None,  # 会话级模型覆盖
        use_sse: bool = False,  # 是否使用 SSE 流式（前端实时展示）
    ) -> AsyncIterator[str]:
        """
        处理用户消息并生成流式响应

        Args:
            message: 用户消息
            context: 对话历史
            system_prompt: 系统提示词
            cancel_token: 取消令牌
            yield_intermediate: 是否输出中间迭代内容（Web UI 流式模式）
            model_override: 会话级模型覆盖配置

        Yields:
            str: 流式响应内容
        """
        logger.info(f"AgentLoop processing message: {message[:50]}...")

        start_time = time.time()

        # 生成请求追踪 ID
        self.request_trace_id = str(uuid.uuid4())
        logger.info(f"Request trace ID: {self.request_trace_id}")

        # 清空去重集合
        self.seen_tool_call_ids.clear()

        # 解析执行时配置（支持会话级模型覆盖）
        (
            runtime_provider,
            runtime_model,
            runtime_temperature,
            runtime_max_tokens,
            runtime_max_iterations,
        ) = self._resolve_execution_runtime(model_override)

        # 构建消息列表
        if context is None:
            context = []

        messages = list(context)
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        iteration = 0
        total_tool_calls = 0
        final_content = ""
        iterations_log: list[AgentIteration] = []
        self.last_tool_results = {}  # 清空上次的工具调用结果
        self.last_tool_durations = {}  # 清空上次的工具调用耗时

        try:
            while iteration < runtime_max_iterations:
                iteration += 1

                # 检查是否被取消
                if cancel_token and cancel_token.is_cancelled:
                    logger.info(f"AgentLoop cancelled at iteration {iteration}")
                    yield "\n\n[任务被取消]"
                    return

                logger.debug(f"Agent iteration {iteration}/{runtime_max_iterations}")

                # 检查 Agent 间收件箱
                if self._inbox_queue is not None:
                    inbox_msg = await self._poll_inbox(timeout=0.1)
                    if inbox_msg is not None:
                        sender = inbox_msg.get("from", "unknown")
                        body = inbox_msg.get("message", "")
                        messages.append(
                            {
                                "role": "system",
                                "content": f"[来自 '{sender}' 的 Agent 间消息]: {body}",
                            }
                        )
                        logger.debug(
                            f"Inbox message from '{sender}' injected at iteration {iteration}"
                        )

                # Context 压缩链（Phase 1: Context 卫生）
                # 顺序：Snip (零成本) → MicroCompacter (清除旧工具结果) → Compactor (LLM 级压缩)
                messages = await self._prune_context(messages, iteration)

                # 调用 LLM
                iter_start = time.time()
                content_buffer = ""
                reasoning_content_buffer = (
                    ""  # DeepSeek thinking mode reasoning_content
                )
                tool_calls_buffer: list[dict] = []
                tool_calls_aggregate: dict[int, dict] = {}  # 按聚合增量式 tool_calls
                finish_reason = None

                # 获取工具定义
                tool_definitions = []
                if self.tools:
                    tool_definitions = self.tools.get_definitions()
                    # 工具池过滤：plan mode 或 agent config 中的 tool_pool_mode
                    from app.modules.tools.plan_mode import is_plan_mode_active
                    from app.modules.tools.pool_filter import (
                        ToolPoolMode,
                        get_pool_tool_policy,
                    )

                    if is_plan_mode_active():
                        # plan mode 优先（临时进入的只读模式）
                        policy = get_pool_tool_policy(ToolPoolMode.PLAN)
                    else:
                        pool_mode = self.agent_config.get("tool_pool_mode", "default")
                        policy = get_pool_tool_policy(pool_mode)

                    if policy:
                        tool_definitions = [
                            t
                            for t in tool_definitions
                            if policy.is_allowed(t.get("function", {}).get("name"))[0]
                        ]
                    logger.info(f"Tool definitions count: {len(tool_definitions)}")
                    logger.debug(
                        f"Tool names: {[t.get('function', {}).get('name') for t in tool_definitions]}"
                    )
                else:
                    logger.warning("No tools registry available!")

                # 流式调用 LLM
                if yield_intermediate:
                    yield "\n[思考中...]"
                async for chunk in self._call_llm_stream(
                    messages=messages,
                    tools=tool_definitions,
                    provider=runtime_provider,
                    model=runtime_model,
                    temperature=runtime_temperature,
                    use_sse=use_sse,
                    max_tokens=runtime_max_tokens,
                ):
                    if chunk.get("content"):
                        content_buffer += chunk["content"]
                        if yield_intermediate:
                            yield chunk["content"]

                    if chunk.get("tool_call"):
                        tc = chunk["tool_call"]
                        tc_index = tc.get("index", 0)

                        # 聚合增量式 tool_calls
                        if tc_index not in tool_calls_aggregate:
                            tool_calls_aggregate[tc_index] = {
                                "id": tc.get("id", ""),
                                "name": tc.get("name", ""),
                                "arguments": "",
                            }

                        # 合并字段（id 和 name 只在第一个 chunk 出现）
                        if tc.get("id"):
                            tool_calls_aggregate[tc_index]["id"] = tc["id"]
                        if tc.get("name"):
                            tool_calls_aggregate[tc_index]["name"] = tc["name"]
                        if tc.get("arguments"):
                            tool_calls_aggregate[tc_index]["arguments"] += tc[
                                "arguments"
                            ]

                    if chunk.get("finish_reason"):
                        finish_reason = chunk["finish_reason"]

                    if chunk.get("reasoning_content"):
                        reasoning_content_buffer += chunk["reasoning_content"]
                        if yield_intermediate:
                            yield f"<!--THINKING:{chunk['reasoning_content']}-->"

                    if chunk.get("error"):
                        yield chunk["error"]
                        return

                # 将聚合后的 tool_calls 转换为列表
                tool_calls_buffer = list(tool_calls_aggregate.values())

                # 安全网关：post_llm_call 输出过滤
                if self._security_client and content_buffer:
                    try:
                        sg_context = {
                            "agent_id": self._agent_id,
                            "agent_name": self._agent_name,
                            "session_id": self.session_id,
                            "request_trace_id": self.request_trace_id,
                        }
                        result = await self._security_client.filter_output(
                            content_buffer, sg_context
                        )
                        if result.action == "block":
                            yield f"\n\n[安全拦截] {result.reason or '模型输出被安全策略拦截'}"
                            return
                        elif result.action == "sanitize" and result.content is not None:
                            content_buffer = result.content
                            if yield_intermediate:
                                # 通知前端输出被修改
                                yield "\n<!--SECURITY:输出已脱敏-->\n"
                    except Exception as e:
                        logger.error(f"Security client filter_output failed: {e}")

                iter_latency = int((time.time() - iter_start) * 1000)

                # 记录本次迭代
                iterations_log.append(
                    AgentIteration(
                        iteration=iteration,
                        content=content_buffer,
                        tool_calls=[],  # 稍后填充
                        finish_reason=finish_reason,
                        latency_ms=iter_latency,
                    )
                )

                if content_buffer:
                    final_content = content_buffer

                # 如果没有工具调用，结束循环
                if not tool_calls_buffer:
                    logger.info(
                        f"No tool calls received from LLM at iteration {iteration}, finish_reason={finish_reason}"
                    )
                    # 非流式模式：输出最终内容
                    if not yield_intermediate and content_buffer:
                        yield content_buffer
                    break

                logger.info(
                    f"Tool calls received: {len(tool_calls_buffer)} calls - {[tc.get('name') for tc in tool_calls_buffer]}"
                )

                # 并行执行优化：如果所有工具都是 parallel_safe，并行执行
                # 成功后清空 tool_calls_buffer，下面的 for 循环跳过
                if len(tool_calls_buffer) > 1:
                    original_buffer = list(tool_calls_buffer)
                    # 为缺失 id 的工具调用分配稳定 fallback id，避免并行执行时空串 key 覆盖
                    for tc in tool_calls_buffer:
                        if not tc.get("id"):
                            tc["id"] = self._fallback_tool_call_id(
                                tc.get("name", ""), tc.get("arguments", {})
                            )
                    tool_calls_buffer = await self._try_parallel_execute(
                        tool_calls_buffer,
                        messages,
                        content_buffer,
                        reasoning_content_buffer,
                        cancel_token,
                        yield_intermediate,
                    )

                    # 如果并行执行成功（返回空列表），yield 所有标记
                    # 注意：_try_parallel_execute 已把结果写入 self.last_tool_results
                    if not tool_calls_buffer:
                        for tc in original_buffer:
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("name", "")
                            yield f"<!--TOOL_START:{json.dumps({'name': tc_name, 'tool_call_id': tc_id}, ensure_ascii=False)}-->"
                        for tc in original_buffer:
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("name", "")
                            if tc_id in self.last_tool_results:
                                result = self.last_tool_results[tc_id]
                                duration_ms = self.last_tool_durations.get(tc_id)
                                payload = {
                                    'name': tc_name,
                                    'tool_call_id': tc_id,
                                    'result': result,
                                    'duration_ms': duration_ms,
                                }
                                yield f"<!--TOOL_RESULT:{json.dumps(payload, ensure_ascii=False)}-->"
                        content_buffer = ""  # 与 for 循环结束后的清理保持一致
                        continue  # 跳过 for 循环，进入下一轮迭代

                # 顺序执行工具调用
                for tool_call in tool_calls_buffer:
                    try:
                        if total_tool_calls >= self.max_iterations:
                            logger.warning(
                                f"Reached max tool calls limit ({self.max_iterations}), "
                                f"skipping remaining tool calls"
                            )
                            break

                        # 检查是否被取消
                        if cancel_token and cancel_token.is_cancelled:
                            logger.info("AgentLoop cancelled before tool execution")
                            yield "\n\n[任务被取消]"
                            return

                        total_tool_calls += 1

                        # 解析工具调用
                        tc_id = tool_call.get("id", "")
                        tc_name = tool_call.get("name", "")
                        tc_args = tool_call.get("arguments", {})

                        # 确保 tc_id 是字符串类型（防御性处理）
                        if isinstance(tc_id, list):
                            tc_id = str(tc_id[0]) if tc_id else ""
                        elif not isinstance(tc_id, str):
                            tc_id = str(tc_id) if tc_id else ""

                        # 缺失 id 时生成稳定 fallback key，避免空串 key 导致同名工具覆盖
                        if not tc_id:
                            tc_id = self._fallback_tool_call_id(tc_name, tc_args)
                            tool_call["id"] = tc_id

                        # 工具调用去重
                        if tc_id and tc_id in self.seen_tool_call_ids:
                            logger.warning(f"Skipping duplicate tool call: {tc_id}")
                            continue
                        if tc_id:
                            self.seen_tool_call_ids.add(tc_id)

                        # 解析参数（可能是 JSON 字符串、dict 或 list）
                        if isinstance(tc_args, str):
                            try:
                                tc_args = json.loads(tc_args)
                            except Exception:
                                tc_args = {}
                        elif isinstance(tc_args, list):
                            # 如果 arguments 是 list，转换为 dict
                            logger.warning(
                                f"Tool arguments is a list: {tc_args}, converting to dict"
                            )
                            tc_args = {"args": tc_args}
                        elif not isinstance(tc_args, dict):
                            tc_args = {}

                        logger.info(
                            f"Executing tool {total_tool_calls}/{self.max_iterations}: "
                            f"{tc_name} with args type={type(tc_args).__name__}"
                        )

                        # 检查取消令牌
                        if self._is_cancelled():
                            logger.info(
                                f"Agent cancelled before executing tool {tc_name}"
                            )
                            yield "\n\n[执行已取消]"
                            return

                        # 创建工具通知处理器（企业级 4 状态通知）
                        tool_handler = await self._create_tool_handler(tc_name, tc_id)
                        if tool_handler:
                            await tool_handler.notify_start(tc_args)

                        # SSE 实时通知：工具开始执行
                        yield f"<!--TOOL_START:{json.dumps({'name': tc_name, 'tool_call_id': tc_id}, ensure_ascii=False)}-->"

                        # 插件事件：工具开始
                        await self._emit_plugin_event(
                            "tool_start",
                            {
                                "trace_id": self.request_trace_id,
                                "tool_name": tc_name,
                                "tool_call_id": tc_id,
                                "arguments": tc_args,
                            },
                        )

                        # 执行工具（带重试 + 恢复配方）
                        tool_start_time = time.time()
                        result = None
                        last_error = None
                        recovery_executor = (
                            getattr(self, "_recovery_executor", None)
                            or RecoveryExecutor()
                        )
                        recovery_context = RecoveryContext()

                        for attempt in range(self.max_retries):
                            try:
                                result = await self._execute_tool(tc_name, tc_args)
                                logger.debug(f"Tool {tc_name} executed successfully")
                                # Phase 6: 跟踪文件访问
                                self._track_file_access(tc_name, tc_args, result, tc_id)
                                break
                            except RecoverableToolError as e:
                                last_error = e
                                scenario = classify_error(e)
                                recipe = recipe_for(scenario)
                                logger.warning(
                                    f"Tool {tc_name} raised recoverable error "
                                    f"(attempt {attempt + 1}/{self.max_retries}): "
                                    f"scenario={scenario.value}, error={e}"
                                )
                                if recipe is not None:
                                    recovery_result = (
                                        recovery_executor.attempt_recovery(
                                            scenario, recovery_context, e
                                        )
                                    )
                                    if recovery_result.should_retry:
                                        if recovery_result.auto_fix_command:
                                            # 自动修复（如 rm -f .git/index.lock）
                                            try:
                                                import shlex
                                                import subprocess

                                                subprocess.run(
                                                    shlex.split(
                                                        recovery_result.auto_fix_command
                                                    ),
                                                    shell=False,
                                                    timeout=10,
                                                    capture_output=True,
                                                )
                                                logger.info(
                                                    f"Auto-fix executed: "
                                                    f"{recovery_result.auto_fix_command}"
                                                )
                                            except Exception as fix_err:
                                                logger.warning(
                                                    f"Auto-fix failed: {fix_err}"
                                                )
                                                break
                                        await asyncio.sleep(
                                            recovery_result.wait_ms / 1000
                                        )
                                        continue
                                    elif recovery_result.escalate:
                                        logger.warning(
                                            f"Recovery escalated for {tc_name}: "
                                            f"{recovery_result.detail}"
                                        )
                                        break
                                    else:
                                        # 不可重试也不升级（如 REPORT_TO_LLM）
                                        break
                                elif attempt < self.max_retries - 1:
                                    await asyncio.sleep(self.retry_delay)
                            except Exception as e:
                                last_error = e
                                logger.warning(
                                    f"Tool {tc_name} failed "
                                    f"(attempt {attempt + 1}/{self.max_retries}): {e}"
                                )
                                if attempt < self.max_retries - 1:
                                    await asyncio.sleep(self.retry_delay)

                        # 添加工具结果到消息列表
                        tool_duration_ms = int((time.time() - tool_start_time) * 1000)
                        if result is not None:
                            # 记录工具调用结果（以 tool_call_id 为 key，避免同名工具并行时覆盖）
                            self.last_tool_results[tc_id] = result
                            self.last_tool_durations[tc_id] = tool_duration_ms

                            # SSE 实时通知：工具执行完成
                            payload = {
                                'name': tc_name,
                                'tool_call_id': tc_id,
                                'result': result,
                                'duration_ms': tool_duration_ms,
                            }
                            yield f"<!--TOOL_RESULT:{json.dumps(payload, ensure_ascii=False)}-->"

                            # WebSocket 通知：工具调用成功
                            if tool_handler:
                                await tool_handler.notify_complete(result)

                            # 插件事件：工具完成
                            await self._emit_plugin_event(
                                "tool_complete",
                                {
                                    "trace_id": self.request_trace_id,
                                    "tool_name": tc_name,
                                    "tool_call_id": tc_id,
                                    "result": result[:200] if result else None,
                                },
                            )

                            # 添加 assistant 消息（包含 tool_calls）
                            assistant_msg = {
                                "role": "assistant",
                                "content": content_buffer or "",
                                "tool_calls": [
                                    {
                                        "id": tc_id,
                                        "type": "function",
                                        "function": {
                                            "name": tc_name,
                                            "arguments": json.dumps(tc_args),
                                        },
                                    }
                                ],
                            }
                            if reasoning_content_buffer:
                                assistant_msg["reasoning_content"] = (
                                    reasoning_content_buffer
                                )
                            messages.append(assistant_msg)
                            # 添加 tool 结果消息
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "tool_name": tc_name,
                                    "content": result,
                                }
                            )
                        else:
                            # 工具执行失败
                            error_msg = f"Tool execution failed after {self.max_retries} attempts: {str(last_error)}"
                            logger.error(
                                f"Tool {tc_name} failed permanently: {error_msg}"
                            )

                            # SSE 实时通知：工具执行错误
                            payload = {
                                'name': tc_name,
                                'tool_call_id': tc_id,
                                'error': str(last_error),
                                'duration_ms': tool_duration_ms,
                            }
                            yield f"<!--TOOL_ERROR:{json.dumps(payload, ensure_ascii=False)}-->"

                            # WebSocket 通知：工具错误
                            if tool_handler:
                                await tool_handler.notify_error(str(last_error))

                            # 插件事件：工具失败
                            await self._emit_plugin_event(
                                "tool_error",
                                {
                                    "trace_id": self.request_trace_id,
                                    "tool_name": tc_name,
                                    "tool_call_id": tc_id,
                                    "error": str(last_error),
                                },
                            )

                            assistant_msg = {
                                "role": "assistant",
                                "content": content_buffer or "",
                                "tool_calls": [
                                    {
                                        "id": tc_id,
                                        "type": "function",
                                        "function": {
                                            "name": tc_name,
                                            "arguments": json.dumps(tc_args),
                                        },
                                    }
                                ],
                            }
                            if reasoning_content_buffer:
                                assistant_msg["reasoning_content"] = (
                                    reasoning_content_buffer
                                )
                            messages.append(assistant_msg)
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "tool_name": tc_name,
                                    "content": error_msg,
                                }
                            )
                    except Exception as tool_error:
                        logger.error(
                            f"Tool processing error: {tool_error}", exc_info=True
                        )
                        yield f"\n\n[工具处理错误: {tool_error}]"

            # 检查是否达到限制
            if iteration >= runtime_max_iterations:
                logger.warning(f"Max iterations ({runtime_max_iterations}) reached")
                warning_msg = f"\n\n[达到最大迭代次数 {runtime_max_iterations}]"
                yield warning_msg
                final_content += warning_msg

            # 循环结束
            total_latency = int((time.time() - start_time) * 1000)
            logger.info(
                f"AgentLoop completed: iterations={iteration}, "
                f"tool_calls={total_tool_calls}, latency={total_latency}ms"
            )

            # WebSocket 通知：Agent 完成
            await self._emit_agent_complete(
                content=final_content,
                total_iterations=iteration,
                total_tool_calls=total_tool_calls,
                latency_ms=total_latency,
            )

            # Stage VV: 后台触发 post-turn 服务（fire-and-forget）
            if self._post_turn_services:
                import asyncio as _asyncio

                _asyncio.create_task(self._run_post_turn(messages))

        except Exception as e:
            import traceback

            logger.error(f"AgentLoop error: {e}\n{traceback.format_exc()}")
            yield f"\n\n[错误: {e}]"

    async def _call_llm_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        provider: Any = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_sse: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        调用 LLM 流式接口

        Args:
            messages: 消息列表
            tools: 工具定义列表
            provider: Provider 实例（可选，默认使用 self.provider）
            model: 模型名称（可选）
            temperature: 温度参数（可选）
            max_tokens: 最大 token 数（可选）
            use_sse: 是否使用真正的 SSE 流式（需 provider 支持 chat_stream_sse）

        Yields:
            dict: 流式响应块
        """
        provider = provider or self.provider
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens

        # 调试日志：记录工具定义
        logger.info(
            f"_call_llm_stream: tools_count={len(tools) if tools else 0}, use_sse={use_sse}"
        )
        if tools:
            logger.debug(
                f"Tool names: {[t.get('function', {}).get('name') for t in tools]}"
            )

        # 选择调用方法：SSE 流式 或 普通非流式
        if use_sse and hasattr(provider, "chat_stream_sse"):
            chat_method = provider.chat_stream_sse
            logger.info("Using SSE streaming method")
        elif hasattr(provider, "chat_stream"):
            chat_method = provider.chat_stream
        else:
            chat_method = None

        if chat_method:
            max_retries = 3
            for attempt in range(max_retries + 1):
                try:
                    async for chunk in chat_method(
                        messages=messages,
                        tools=tools,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ):
                        converted = self._convert_stream_chunk(chunk)
                        # 检测 prompt_too_long
                        if converted.get("error") and self._is_prompt_too_long(
                            converted["error"]
                        ):
                            if attempt < max_retries and self._compression_service:
                                logger.warning(
                                    f"Prompt too long detected, emergency compact "
                                    f"attempt {attempt + 1}/{max_retries}"
                                )
                                compacted = (
                                    await self._compression_service.emergency_compact(
                                        messages, attempt=attempt + 1
                                    )
                                )
                                messages[:] = compacted  # 更新调用方的消息列表
                                break  # 跳出 async for，外层循环继续重试
                            else:
                                yield converted
                                return
                        yield converted
                    else:
                        return  # 成功完成，正常退出
                    # 因 prompt_too_long 触发 break，继续外层重试
                    continue
                except Exception as e:
                    import traceback

                    logger.error(
                        f"_call_llm_stream error (attempt {attempt + 1}/{max_retries}): {e}\n{traceback.format_exc()}"
                    )

                    # Key 轮换：如果是认证/限流错误，尝试轮换 Key
                    if self._key_rotator and self._key_rotator.should_rotate_key(e):
                        new_key = self._key_rotator.next_key()
                        if new_key and attempt < max_retries:
                            provider.api_key = new_key
                            self._key_rotation_count += 1
                            delay = 1.0 * attempt  # 渐进延迟
                            logger.info(
                                f"Rotating API key (attempt {attempt + 1}), "
                                f"retrying in {delay}s..."
                            )
                            await asyncio.sleep(delay)
                            continue

                    # 瞬态错误（502/503/504/连接超时）：重试
                    if self._is_transient_error(e) and attempt < max_retries:
                        delay = min(2.0 * (2**attempt), 16.0)
                        logger.info(
                            f"Transient error, retrying in {delay}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue

                    # 无法恢复，返回错误
                    yield {"error": str(e)}
                    return
        else:
            yield {"error": "Provider does not support streaming"}

    def _convert_stream_chunk(self, chunk: Any) -> dict[str, Any]:
        """
        将 StreamChunk 对象转换为 dict 格式

        Args:
            chunk: StreamChunk dataclass 或已转换的 dict

        Returns:
            dict: 统一的响应格式
        """
        # 如果已经是 dict，直接返回
        if isinstance(chunk, dict):
            return chunk

        result: dict[str, Any] = {}

        # 处理 StreamChunk 的 delta 字段
        if hasattr(chunk, "delta") and chunk.delta:
            content = chunk.delta.get("content", "")
            if content:
                result["content"] = content

        # 处理 tool_calls 字段
        if hasattr(chunk, "tool_calls") and chunk.tool_calls:
            for tc in chunk.tool_calls:
                # OpenAI 格式的 tool_calls 是增量式的，需要聚合
                # 每个 chunk 可能只包含部分 arguments
                tc_id = tc.get("id", "")
                tc_index = tc.get("index", 0)
                tc_function = tc.get("function", {})
                tc_name = tc_function.get("name", "")
                tc_args = tc_function.get("arguments", "")

                # 确保 tc_id 是字符串（防御性处理）
                if isinstance(tc_id, list):
                    tc_id = str(tc_id[0]) if tc_id else ""
                elif not isinstance(tc_id, str):
                    tc_id = str(tc_id) if tc_id else ""

                # 返回增量工具调用信息
                result["tool_call"] = {
                    "id": tc_id,
                    "index": tc_index,
                    "name": tc_name,
                    "arguments": tc_args,
                }

        # 处理 finish_reason 字段
        if hasattr(chunk, "finish_reason") and chunk.finish_reason:
            result["finish_reason"] = chunk.finish_reason

        # 处理 thinking 字段（Anthropic Extended Thinking）
        if hasattr(chunk, "thinking") and chunk.thinking:
            result["thinking"] = chunk.thinking

        # 处理 usage 字段
        if hasattr(chunk, "usage") and chunk.usage:
            result["usage"] = chunk.usage

        return result

    def _is_transient_error(self, error: Exception) -> bool:
        """判断是否为可重试的瞬态错误（502/503/504/连接超时）"""
        error_text = f"{type(error).__name__}: {error}".lower()
        transient_hints = (
            "502",
            "503",
            "504",
            "connection",
            "timeout",
            "read error",
            "server disconnected",
            "eof",
        )
        return any(hint in error_text for hint in transient_hints)

    def _is_prompt_too_long(self, error_text: str) -> bool:
        """判断是否为上下文过长错误"""
        text = error_text.lower()
        hints = (
            "prompt_too_long",
            "context length",
            "maximum context",
            "token limit",
            "too many tokens",
            "exceeds maximum",
            "context_length_exceeded",
            "maximum token",
        )
        return any(hint in text for hint in hints)

    async def _try_parallel_execute(
        self,
        tool_calls_buffer: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        content_buffer: str,
        reasoning_content_buffer: str,
        cancel_token: CancelToken | None,
        yield_intermediate: bool,
    ) -> list[dict[str, Any]]:
        """尝试并行执行安全工具（使用新调度器 partition_tool_calls + run_concurrent_batch）

        返回空列表表示已执行（for 循环跳过），否则返回原始 buffer。
        """
        if not self.tools or len(tool_calls_buffer) < 2:
            return tool_calls_buffer

        # 解析参数并构造 ToolUse
        def _normalize_args(tc_args):
            if isinstance(tc_args, str):
                try:
                    return json.loads(tc_args)
                except Exception:
                    return {}
            elif isinstance(tc_args, list):
                return {"args": tc_args}
            elif not isinstance(tc_args, dict):
                return {}
            return tc_args

        def _normalize_id(tc_id):
            if isinstance(tc_id, list):
                return str(tc_id[0]) if tc_id else ""
            return str(tc_id) if tc_id else ""

        tool_uses = []
        for tc in tool_calls_buffer:
            tc_id = _normalize_id(tc.get("id", ""))
            tc_name = tc.get("name", "")
            tc_args = _normalize_args(tc.get("arguments", {}))
            if not tc_id:
                tc_id = self._fallback_tool_call_id(tc_name, tc_args)
                tc["id"] = tc_id
            tool_uses.append(_ToolUse(id=tc_id, tool_id=tc_name, input=tc_args))

        # 按 is_read_only + is_concurrency_safe 分区
        batches = partition_tool_calls(tool_uses, self.tools)

        # 只有全部为单个并发批次时才并行执行
        if len(batches) != 1 or not batches[0].concurrent:
            return tool_calls_buffer

        logger.info(
            f"Parallel executing {len(tool_uses)} safe tools via scheduler: "
            f"{[tc.get('name') for tc in tool_calls_buffer]}"
        )

        async def _execute_one(tool_use: _ToolUse):
            start = time.time()
            try:
                result = await self._execute_tool(tool_use.tool_id, tool_use.input)
                self._track_file_access(
                    tool_use.tool_id, tool_use.input, result, tool_use.id
                )
                return (
                    tool_use.id,
                    tool_use.tool_id,
                    tool_use.input,
                    result,
                    None,
                    int((time.time() - start) * 1000),
                )
            except Exception as e:
                return (
                    tool_use.id,
                    tool_use.tool_id,
                    tool_use.input,
                    None,
                    e,
                    int((time.time() - start) * 1000),
                )

        results = await run_concurrent_batch(batches[0].tools, _execute_one)

        # 处理结果并追加到 messages（保持与原逻辑一致）
        has_failure = False
        for tc_id, tc_name, tc_args, result, error, duration_ms in results:
            if tc_id:
                self.seen_tool_call_ids.add(tc_id)

            if error is None and result is not None:
                self.last_tool_results[tc_id] = result
                self.last_tool_durations[tc_id] = duration_ms
                assistant_msg = {
                    "role": "assistant",
                    "content": content_buffer or "",
                    "tool_calls": [
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": tc_name,
                                "arguments": json.dumps(tc_args),
                            },
                        }
                    ],
                }
                if reasoning_content_buffer:
                    assistant_msg["reasoning_content"] = reasoning_content_buffer
                messages.append(assistant_msg)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "tool_name": tc_name,
                        "content": result,
                    }
                )
            else:
                if has_failure:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content_buffer or "",
                            "tool_calls": [
                                {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "content": "[Skipped: sibling tool failed]",
                        }
                    )
                else:
                    has_failure = True
                    error_msg = f"Tool execution failed: {str(error) if error else 'unknown error'}"
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content_buffer or "",
                            "tool_calls": [
                                {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "content": error_msg,
                        }
                    )

        if yield_intermediate:
            logger.info(f"Parallel tool execution complete for {len(tool_uses)} tools")

        return []  # 返回空列表，for 循环跳过

    async def _prune_context(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
    ) -> list[dict[str, Any]]:
        """Context 压缩链：Snip → MicroCompacter → Compactor

        每轮 LLM 调用前执行，逐层压缩释放 context 空间。
        优先使用 ContextCompressionService 统一入口；未注入时回退到直接调用组件。
        """
        if not messages:
            return messages

        # 优先使用 ContextCompressionService（Phase 1 改造后的统一入口）
        if self._compression_service is not None:
            try:
                return await self._compression_service.auto_prune(
                    messages, self.provider
                )
            except Exception as e:
                logger.warning(
                    f"CompressionService auto_prune failed: {e}, falling back"
                )

        # Fallback：直接调用各组件（向后兼容）
        total_saved = 0

        # Layer 1: Snip — 零成本裁剪
        if self._context_pruner:
            snipped, saved = self._context_pruner.snip_prune(messages)
            messages = snipped
            total_saved += saved

        # Layer 2: MicroCompacter
        if self._context_pruner:
            messages, saved = self._context_pruner.micro_compact(messages)
            total_saved += saved

        # Layer 3: Compactor
        if self._compactor:
            estimated_tokens = self._compactor._estimate_tokens(messages)
            if self._compactor.should_compact(messages, estimated_tokens):
                result = await self._compactor.compact(messages)
                messages = self._rebuild_after_compact(messages, result)
                logger.info(f"Compactor triggered at iteration {iteration}")

        if total_saved > 0:
            logger.debug(
                f"Context prune at iter {iteration}: saved ~{total_saved} chars, "
                f"messages now: {len(messages)}"
            )

        return messages

    def _rebuild_after_compact(
        self,
        messages: list[dict[str, Any]],
        compact_result: Any,
    ) -> list[dict[str, Any]]:
        """Compactor 压缩后重建消息列表：

        system prompt + 压缩摘要（注入为 user 消息）+ 最近 N 条消息
        """
        keep_recent = getattr(
            getattr(self._compactor, "config", None), "keep_recent_messages", 8
        )

        # 提取 system prompt（第一条）
        system_prompt = ""
        start_idx = 0
        if messages and messages[0].get("role") == "system":
            system_prompt = messages[0].get("content", "")
            start_idx = 1

        # 保留最近 keep_recent 条消息
        core = messages[start_idx:]
        recent = core[-keep_recent:] if len(core) > keep_recent else core

        # 获取摘要（支持 CompactionResult 对象和 dict）
        if hasattr(compact_result, "summary"):
            summary = compact_result.summary
        elif isinstance(compact_result, dict):
            summary = compact_result.get("summary", "")
        else:
            summary = str(compact_result)

        if not summary:
            # Fallback: 如果压缩失败，只保留最近消息
            rebuilt = (
                [{"role": "system", "content": system_prompt}] if system_prompt else []
            )
            rebuilt.extend(recent)
            return rebuilt

        # 重建：system + 摘要 + 最近消息
        rebuilt: list[dict[str, Any]] = []
        if system_prompt:
            rebuilt.append({"role": "system", "content": system_prompt})

        rebuilt.append(
            {
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary}",
            }
        )
        rebuilt.extend(recent)

        # Phase 6: 恢复关键文件内容
        if self._file_tracker and self._file_tracker.record_count > 0:
            restored_files = self._file_tracker.get_recent(
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
                    f"Restored {len(restored_files)} files after compaction: "
                    f"{[r.path for r in restored_files]}"
                )

        return rebuilt

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        执行工具（带 Tool Hooks 拦截）

        借鉴 PraisonAI Tool Hooks：
        - BEFORE_TOOL: 参数验证/修改
        - AFTER_TOOL: 结果修改
        - ON_ERROR: 错误处理/重试

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            str: 工具执行结果
        """
        logger.debug(f"Executing tool: {tool_name}")

        # 创建 Hook 上下文
        hook_ctx = HookContext(
            tool_name=tool_name,
            tool_args=arguments,
            agent_id=self._agent_id,
            agent_name=self._agent_name,
            conversation_id=self.session_id,
        )

        # 1. BEFORE_TOOL hooks
        modified_args, skip = await self._tool_hooks.run_before(
            tool_name, arguments, hook_ctx
        )

        if skip:
            logger.info(f"Tool {tool_name} skipped by BEFORE_TOOL hook")
            return hook_ctx.tool_result or ""

        # 2. 优先检查是否是 Handoff 工具
        handoff_result = await self._handle_handoff_tool(tool_name, modified_args)
        if handoff_result is not None:
            # AFTER_TOOL hooks
            final_result = await self._tool_hooks.run_after(
                tool_name, modified_args, handoff_result, hook_ctx
            )
            return str(final_result)

        # 2.5. 权限检查
        allowed, reason = self._check_tool_policy(tool_name)
        if not allowed:
            logger.warning(f"Tool '{tool_name}' denied by policy: {reason}")
            return f"Error: 权限不足 - {reason}"

        # 3. 执行普通工具
        if not self.tools:
            raise ValueError("ToolRegistry not initialized")

        # 直接调用 ToolRegistry.execute，传递 name 和 arguments
        try:
            result = await self.tools.execute(tool_name, modified_args)
            # AFTER_TOOL hooks
            final_result = await self._tool_hooks.run_after(
                tool_name, modified_args, result, hook_ctx
            )
            return str(final_result)
        except CommandConfirmationRequired as e:
            return await self._handle_command_confirmation(
                tool_name, modified_args, e.assessment
            )
        except SensitiveFileAccessRequired as e:
            return await self._handle_sensitive_file_access(tool_name, modified_args, e)
        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name} - {e}")
            raise

    async def _handle_handoff_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        """处理 Handoff 工具调用

        借鉴 PraisonAI Handoff 作为工具暴露给 LLM 的模式
        """
        for handoff in self._handoffs:
            if handoff.tool_name == tool_name:
                prompt = arguments.get("prompt", "")
                if not prompt:
                    return "Error: Handoff requires 'prompt' argument"

                try:
                    result = await handoff.execute(
                        source_agent=self,
                        prompt=prompt,
                        context=self._get_handoff_context(),
                    )

                    if result.error:
                        return f"Handoff failed: {result.error}"

                    return str(result.result)
                except Exception as e:
                    logger.error(f"Handoff execution failed: {e}")
                    return f"Handoff error: {str(e)}"

        return None  # 不是 Handoff 工具

    def _get_handoff_context(self) -> list[dict]:
        """获取用于 Handoff 的上下文"""
        # 从当前对话历史获取上下文
        # 这里简化实现，实际可以从 self._messages 或其他地方获取
        return []

    def get_handoff_tools(self) -> list[dict]:
        """获取所有 Handoff 工具定义（用于 LLM function calling）"""
        return [h.to_tool() for h in self._handoffs]

    def add_handoff(self, handoff: Any) -> None:
        """添加 Handoff"""
        self._handoffs.append(handoff)

    # ==================== Guardrails 方法（借鉴 CrewAI） ====================

    def add_guardrail(self, guardrail: Guardrail) -> None:
        """添加输出验证器"""
        self._guardrails.append(guardrail)
        # 重新创建 executor
        self._guardrail_executor = GuardrailExecutor(self._guardrails)

    def get_guardrails(self) -> list[Guardrail]:
        """获取所有 Guardrail"""
        return list(self._guardrails)

    async def validate_output(self, output: Any) -> ValidationResult:
        """验证输出是否满足 Guardrail 规则"""
        if not self._guardrail_executor:
            return ValidationResult(valid=True, reason="No guardrails configured")

        return await self._guardrail_executor.validate_only(output)

    async def execute_with_validation(
        self,
        func: Callable,
        *args,
        context: str | None = None,
        **kwargs,
    ) -> Any:
        """执行函数并验证输出（失败则重试）"""
        if not self._guardrail_executor:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        return await self._guardrail_executor.execute_with_validation(
            func, *args, context=context, **kwargs
        )

    # ==================== Tool Hooks 方法（借鉴 PraisonAI） ====================

    def add_tool_hook(self, hook: ToolHook) -> None:
        """添加工具拦截器"""
        self._tool_hooks.register(hook)

    def remove_tool_hook(self, hook: ToolHook) -> None:
        """移除工具拦截器"""
        self._tool_hooks.unregister(hook)

    def clear_tool_hooks(self, event: HookEvent | None = None) -> None:
        """清除工具拦截器"""
        self._tool_hooks.clear(event)

    def get_tool_hooks(self) -> list[ToolHook]:
        """获取所有 Tool Hooks"""
        result = []
        for event in HookEvent:
            result.extend(self._tool_hooks._hooks[event])
        return result

    async def process_direct(
        self,
        message: str,
        context: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        model_override: dict[str, Any] | None = None,
    ) -> str:
        """
        直接处理消息（非流式，用于 CLI 或 cron）

        Args:
            message: 消息内容
            context: 对话历史
            system_prompt: 系统提示词
            model_override: 会话级模型覆盖配置

        Returns:
            str: Agent 的完整响应
        """
        response_parts = []

        async for chunk in self.process_message(
            message=message,
            context=context,
            system_prompt=system_prompt,
            model_override=model_override,
        ):
            response_parts.append(chunk)

        return "".join(response_parts)

    # ==================== WebSocket 通知方法 ====================

    def _is_cancelled(self) -> bool:
        """检查会话是否被取消"""
        if not self.session_id:
            return False
        try:
            from app.core.websocket import manager

            token = manager.get_cancel_token(self.session_id)
            return token.is_cancelled
        except Exception:
            return False

    def _fallback_tool_call_id(self, tc_name: str, tc_args: Any) -> str:
        """为缺失 tool_call_id 的工具调用生成调用级唯一的 fallback key"""
        if isinstance(tc_args, str):
            try:
                tc_args = json.loads(tc_args)
            except Exception:
                tc_args = {}
        elif isinstance(tc_args, list):
            tc_args = {"args": tc_args}
        elif not isinstance(tc_args, dict):
            tc_args = {}
        args_hash = (
            hash(json.dumps(tc_args, sort_keys=True, ensure_ascii=False)) & 0xFFFFFFFF
        )
        # 加入 uuid 后缀确保调用级唯一，避免同名同参工具被 seen_tool_call_ids 误判为重复
        return f"__fb_{tc_name}_{args_hash}_{uuid.uuid4().hex[:8]}"

    def _get_permission_checker(self) -> PermissionChecker:
        """获取或延迟初始化权限检查器"""
        if self._permission_checker is None:
            mode = self._permission_mode or resolve_permission_mode(
                user_role=self._user_role,
                agent_config=self.agent_config,
            )
            tool_policy = resolve_tool_policy(self.agent_config)
            self._permission_checker = PermissionChecker(
                mode=mode,
                tool_policy=tool_policy,
            )
            logger.debug(
                f"PermissionChecker initialized: mode={mode.value}, "
                f"tool_policy={tool_policy.to_dict() if tool_policy else 'none'}"
            )
        return self._permission_checker

    def _check_tool_policy(self, tool_name: str) -> tuple:
        """检查工具是否符合权限策略

        组合 PermissionMode + ToolPolicy 检查。

        Returns:
            (allowed: bool, reason: str)
        """
        checker = self._get_permission_checker()
        result = checker.check_tool(tool_name)
        return result.allowed, result.reason

    async def _create_tool_handler(
        self, tool_name: str, tool_call_id: str | None = None
    ):
        """创建工具通知处理器"""
        if not self.session_id:
            return None
        try:
            from app.core.websocket import ToolNotificationHandler

            return ToolNotificationHandler(
                session_id=self.session_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        except Exception as e:
            logger.debug(f"Failed to create tool handler: {e}")
            return None

    async def _emit_tool_start(self, tool_name: str, arguments: dict) -> None:
        """发送工具调用开始通知（向后兼容）"""
        if not self.session_id:
            return
        try:
            from app.core.websocket import emit_tool_call_start

            await emit_tool_call_start(
                session_id=self.session_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        except Exception as e:
            logger.debug(f"Failed to emit tool start: {e}")

    async def _emit_tool_result(
        self, tool_name: str, result: str, latency_ms: int
    ) -> None:
        """发送工具调用结果通知（向后兼容）"""
        if not self.session_id:
            return
        try:
            from app.core.websocket import emit_tool_call_result

            await emit_tool_call_result(
                session_id=self.session_id,
                tool_name=tool_name,
                result=result,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.debug(f"Failed to emit tool result: {e}")

    def _track_file_access(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        tool_call_id: str,
    ) -> None:
        """跟踪文件访问（Phase 6: 压缩后文件恢复）。

        对 read_file/write_file/edit_file 工具，记录文件路径和内容。
        """
        if not self._file_tracker:
            return

        file_tools = {"read_file", "write_file", "edit_file"}
        if tool_name not in file_tools:
            return

        path = arguments.get("path", "")
        if not path:
            return

        was_edited = tool_name in {"write_file", "edit_file"}
        self._file_tracker.record_access(
            path=path,
            content=result,
            was_edited=was_edited,
            tool_call_id=tool_call_id,
        )

    async def _emit_agent_complete(
        self,
        content: str,
        total_iterations: int,
        total_tool_calls: int,
        latency_ms: int,
    ) -> None:
        """发送 Agent 完成通知"""
        if not self.session_id:
            return
        try:
            from app.core.websocket import emit_agent_complete

            await emit_agent_complete(
                session_id=self.session_id,
                content=content,
                total_iterations=total_iterations,
                total_tool_calls=total_tool_calls,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.debug(f"Failed to emit agent complete: {e}")

    async def _emit_plugin_event(self, event_type: str, data: dict) -> None:
        """发送插件事件（同时推送到直接处理器和全局事件总线）"""
        # 1. 直接处理器
        handler = None
        if event_type.startswith("tool"):
            handler = self.tool_event_handler
        elif event_type.startswith("reasoning"):
            handler = self.reasoning_event_handler

        if handler:
            try:
                result = handler(event_type, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug(f"Plugin event handler failed: {e}")

        # 2. 全局事件总线
        try:
            from app.api.plugins import get_event_bus

            bus = get_event_bus()
            await bus.publish(event_type, data)
        except Exception as e:
            logger.debug(f"EventBus publish failed: {e}")

    # ==================== 中断与恢复方法（借鉴 LangGraph） ====================

    @property
    def status(self) -> AgentStatus:
        """获取当前状态"""
        return self._status

    async def interrupt(
        self,
        reason: InterruptReason,
        message: str,
        title: str = "",
        details: dict[str, Any] = None,
        options: list[InterruptOption] = None,
        state: dict[str, Any] = None,
        messages: list[dict] = None,
        ttl: float | None = None,
    ) -> InterruptPoint:
        """创建中断点，暂停执行

        Args:
            reason: 中断原因
            message: 中断消息
            title: 标题
            details: 详细信息
            options: 可选项
            state: 状态快照
            messages: 消息快照
            ttl: 过期时间（秒）

        Returns:
            InterruptPoint: 创建的中断点
        """
        interrupt = await self._interrupt_manager.create_interrupt(
            reason=reason,
            message=message,
            agent_id=self._agent_id,
            agent_name=self._agent_name,
            conversation_id=self.session_id,
            session_id=self.session_id,
            title=title,
            details=details,
            options=options,
            state_snapshot=state or {},
            message_snapshot=messages or [],
            ttl=ttl,
        )

        self._current_interrupt = interrupt
        self._status = AgentStatus.WAITING_INTERRUPT

        logger.info(f"Agent {self._agent_name} interrupted: {reason.value}")
        return interrupt

    async def resume(
        self,
        interrupt_id: str,
        resolution: str,
        resolved_by: int | None = None,
        resolution_note: str = "",
        modified_state: dict[str, Any] = None,
    ) -> InterruptPoint:
        """恢复执行

        Args:
            interrupt_id: 中断 ID
            resolution: 解决方式（approve/reject/modify）
            resolved_by: 解决者用户 ID
            resolution_note: 解决备注
            modified_state: 修改后的状态

        Returns:
            InterruptPoint: 更新后的中断点
        """
        interrupt = await self._interrupt_manager.resolve_interrupt(
            interrupt_id=interrupt_id,
            resolution=resolution,
            resolved_by=resolved_by,
            resolution_note=resolution_note,
            modified_state=modified_state,
        )

        self._current_interrupt = None
        self._status = AgentStatus.RUNNING

        logger.info(f"Agent {self._agent_name} resumed: {resolution}")
        return interrupt

    async def get_pending_interrupts(self) -> list[InterruptPoint]:
        """获取待处理中断列表"""
        return await self._interrupt_manager.get_pending_interrupts(
            agent_id=self._agent_id,
            session_id=self.session_id,
        )

    async def wait_for_interrupt_resolution(
        self,
        interrupt_id: str,
        poll_interval: float = 1.0,
        timeout: float = 3600.0,
    ) -> InterruptPoint:
        """等待中断解决

        Args:
            interrupt_id: 中断 ID
            poll_interval: 轮询间隔（秒）
            timeout: 超时时间（秒）

        Returns:
            InterruptPoint: 解决后的中断点

        Raises:
            TimeoutError: 超时未解决
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            interrupt = await self._interrupt_manager.get_interrupt(interrupt_id)

            if interrupt is None:
                raise ValueError(f"Interrupt {interrupt_id} not found")

            if interrupt.is_resolved():
                return interrupt

            if interrupt.is_expired():
                raise TimeoutError(f"Interrupt {interrupt_id} has expired")

            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Interrupt {interrupt_id} not resolved within {timeout}s")

    # ==================== 检查点方法 ====================

    async def save_checkpoint(
        self,
        name: str = "",
        state: dict[str, Any] = None,
        messages: list[dict] = None,
        iteration: int = 0,
        tool_calls: list[dict] = None,
        metadata: dict[str, Any] = None,
    ) -> Checkpoint:
        """保存检查点

        Args:
            name: 检查点名称
            state: 状态快照
            messages: 消息列表
            iteration: 当前迭代次数
            tool_calls: 工具调用列表
            metadata: 元数据

        Returns:
            Checkpoint: 创建的检查点
        """
        return await self._interrupt_manager.save_checkpoint(
            agent_id=self._agent_id,
            name=name,
            state=state or {},
            messages=messages or [],
            iteration=iteration,
            tool_calls=tool_calls or [],
            metadata=metadata,
        )

    async def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        """恢复到检查点

        Args:
            checkpoint_id: 检查点 ID

        Returns:
            Dict: 包含 state, messages, iteration, tool_calls 的字典
        """
        return await self._interrupt_manager.restore_checkpoint(checkpoint_id)

    async def list_checkpoints(self, limit: int = 10) -> list[Checkpoint]:
        """列出检查点"""
        return await self._interrupt_manager.list_checkpoints(
            agent_id=self._agent_id,
            limit=limit,
        )

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        """删除检查点"""
        await self._interrupt_manager.delete_checkpoint(checkpoint_id)

    # ==================== 敏感操作中断辅助方法 ====================

    async def _handle_command_confirmation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        assessment: CommandAssessment,
    ) -> str:
        """处理需要确认的命令（CAUTION 或 DANGEROUS 级别）

        借鉴 claw-code 的权限确认机制。
        CAUTION → 弹出批准/取消确认框
        DANGEROUS → 要求输入确认短语

        Args:
            tool_name: 工具名称（通常为 "exec"）
            arguments: 原始工具参数
            assessment: 命令安全分析结果

        Returns:
            str: 执行结果或取消消息
        """
        command = arguments.get("command", "")

        # PermissionMode 检查：ALLOW/PROMPT 模式自动审批，跳过确认
        checker = self._get_permission_checker()
        if checker.mode.at_least(PermissionMode.PROMPT):
            logger.info(f"Auto-approving command in {checker.mode.value} mode")
            try:
                result = await self.tools.execute(tool_name, arguments)
                return str(result)
            except CommandConfirmationRequired:
                pass  # 不应发生，fallthrough 到确认流程
            except Exception as e:
                logger.error(f"Auto-approved command execution failed: {e}")
                return f"错误: 命令执行失败 - {e}"

        # ReadOnly 模式：直接拒绝所有 Bash
        if checker.mode == PermissionMode.READ_ONLY:
            return "错误: ReadOnly 权限模式禁止执行 Bash 命令"

        if assessment.level == DangerLevel.DANGEROUS:
            confirm_phrase = assessment.confirmation_phrase or "confirm"
            options = [
                InterruptOption(
                    label="确认执行（高风险）",
                    value="approve",
                    description=f"请输入确认短语以执行: {confirm_phrase}",
                    style="danger",
                    requires_input=True,
                    input_placeholder=f"输入: {confirm_phrase}",
                ),
                InterruptOption(
                    label="取消",
                    value="reject",
                    style="default",
                ),
            ]
        else:
            options = [
                InterruptOption(
                    label="批准执行",
                    value="approve",
                    description="此命令可能需要管理员权限或会产生副作用",
                    style="warning",
                ),
                InterruptOption(
                    label="取消",
                    value="reject",
                    style="default",
                ),
            ]

        interrupt = await self.interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message=(
                f"**命令需要确认**\n\n"
                f"```bash\n{command}\n```\n\n"
                f"**风险**: {assessment.risk_summary}\n"
                + (
                    f"**确认短语**: `{assessment.confirmation_phrase}`\n"
                    if assessment.confirmation_phrase
                    else ""
                )
            ),
            title="命令执行确认",
            details={
                "command": command,
                "level": assessment.level.value,
                "risk": assessment.risk_summary,
                "confirmation_phrase": assessment.confirmation_phrase,
                "matched_categories": list(
                    {r.category for r in assessment.matched_rules}
                ),
            },
            options=options,
        )

        try:
            resolved = await self.wait_for_interrupt_resolution(
                interrupt.id, timeout=300.0
            )
        except TimeoutError:
            return "错误: 命令执行确认超时（300秒），已自动取消"

        if resolved.status != InterruptStatus.APPROVED:
            return f"用户取消了命令执行: {command[:80]}"

        # DANGEROUS 级别：验证确认短语
        if assessment.level == DangerLevel.DANGEROUS:
            user_input = (resolved.resolution_note or "").strip()
            expected = assessment.confirmation_phrase or ""
            if expected and user_input != expected:
                return (
                    f"错误: 确认短语不匹配。"
                    f"需要 `{expected}`, 实际输入 `{user_input}`。"
                    f"命令未执行。"
                )

        # 用户批准，重新执行命令（绕过安全检查）
        # 使用 policy.allow_destructive_commands 跳过二次检查
        try:
            result = await self.tools.execute(tool_name, arguments)
            return str(result)
        except CommandConfirmationRequired:
            return "错误: 命令二次安全检查未通过（不应发生）"
        except Exception as e:
            logger.error(f"Confirmed command execution failed: {e}")
            return f"错误: 命令执行失败 - {e}"

    async def _handle_sensitive_file_access(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        exception: SensitiveFileAccessRequired,
    ) -> str:
        """处理敏感文件访问确认（类似 _handle_command_confirmation）

        当 Agent 尝试：
        - 读取工作区外的文件（reason="outside_workspace"）
        - 访问敏感文件如 .env（reason="sensitive"）

        弹出确认框让用户批准/拒绝。
        """
        path = str(exception.path)
        reason = exception.reason

        if reason == "outside_workspace":
            title = "读取工作区外文件"
            message = (
                f"**Agent 尝试读取工作区外的文件**\n\n"
                f"路径: `{path}`\n\n"
                f"工作区外的文件可能包含敏感信息。是否允许？"
            )
        elif reason == "sensitive":
            title = "访问敏感文件"
            message = (
                f"**Agent 尝试访问敏感文件**\n\n"
                f"文件: `{exception.file_name}`\n"
                f"路径: `{path}`\n"
                f"匹配模式: `{exception.pattern}`\n\n"
                f"此文件可能包含密钥或凭据。是否允许？"
            )
        else:
            title = "文件访问确认"
            message = f"**Agent 尝试访问文件**: `{path}`\n\n是否允许？"

        options = [
            InterruptOption(
                label="允许访问",
                value="approve",
                description="批准此次文件访问",
                style="warning",
            ),
            InterruptOption(
                label="拒绝",
                value="reject",
                style="default",
            ),
        ]

        interrupt = await self.interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message=message,
            title=title,
            details={
                "path": path,
                "file_name": exception.file_name,
                "reason": reason,
                "tool": tool_name,
            },
            options=options,
        )

        try:
            resolved = await self.wait_for_interrupt_resolution(
                interrupt.id, timeout=300.0
            )
        except TimeoutError:
            return "错误: 文件访问确认超时（300秒），已自动取消"

        if resolved.status != InterruptStatus.APPROVED:
            return f"用户拒绝了文件访问: {path}"

        # 用户批准，带上绕过标志重新执行
        try:
            if reason == "outside_workspace":
                arguments["_allow_outside"] = True
            else:
                arguments["_allow_sensitive"] = True
            result = await self.tools.execute(tool_name, arguments)
            return str(result)
        except SensitiveFileAccessRequired:
            return "错误: 文件访问二次安全检查未通过（不应发生）"
        except Exception as e:
            logger.error(f"Confirmed file access failed: {e}")
            return f"错误: 文件访问失败 - {e}"

    async def confirm_sensitive_action(
        self,
        action: str,
        details: dict[str, Any] = None,
    ) -> bool:
        """确认敏感操作

        Args:
            action: 操作描述
            details: 操作详情

        Returns:
            bool: 是否批准
        """
        interrupt = await self.interrupt(
            reason=InterruptReason.SENSITIVE_ACTION,
            message=f"需要确认敏感操作: {action}",
            title="敏感操作确认",
            details=details or {"action": action},
            options=interrupt_options.approve_reject(),
        )

        # 等待解决
        resolved = await self.wait_for_interrupt_resolution(interrupt.id)

        return resolved.status == InterruptStatus.APPROVED

    async def request_human_review(
        self,
        content: str,
        details: dict[str, Any] = None,
    ) -> InterruptPoint:
        """请求人工审核

        Args:
            content: 待审核内容
            details: 审核详情

        Returns:
            InterruptPoint: 中断点（需要等待解决）
        """
        return await self.interrupt(
            reason=InterruptReason.HUMAN_REVIEW,
            message=f"需要人工审核: {content[:100]}...",
            title="人工审核",
            details=details or {"content": content},
            options=interrupt_options.approve_reject_modify(),
        )

    # ==================== 追踪方法（借鉴 LangSmith） ====================

    def start_trace(self, name: str = "") -> Trace:
        """开始追踪

        Args:
            name: 追踪名称

        Returns:
            Trace: 创建的追踪
        """
        trace_name = name or f"{self._agent_name} execution"
        trace = self._tracer.start_trace(
            name=trace_name,
            agent_id=self._agent_id,
            agent_name=self._agent_name,
            session_id=self.session_id,
        )
        self._current_trace = trace
        return trace

    def end_trace(self) -> Trace | None:
        """结束追踪"""
        trace = self._tracer.end_trace()
        self._current_trace = None
        return trace

    def start_span(
        self,
        kind: SpanKind,
        name: str,
        input_data: dict[str, Any] = None,
    ) -> Span:
        """开始跨度

        Args:
            kind: 跨度类型
            name: 跨度名称
            input_data: 输入数据

        Returns:
            Span: 创建的跨度
        """
        return self._tracer.start_span(kind, name, input_data)

    def end_span(
        self,
        status: SpanStatus = SpanStatus.SUCCESS,
        output_data: dict[str, Any] = None,
        error: str = None,
        error_stack: str = None,
        tokens: TokenUsage = None,
    ) -> Span | None:
        """结束跨度

        Args:
            status: 跨度状态
            output_data: 输出数据
            error: 错误信息
            error_stack: 错误堆栈
            tokens: Token 使用量

        Returns:
            Span: 结束的跨度
        """
        return self._tracer.end_span(
            status=status,
            output_data=output_data,
            error=error,
            error_stack=error_stack,
            tokens=tokens,
        )

    def get_current_trace(self) -> Trace | None:
        """获取当前追踪"""
        return self._current_trace

    def get_current_span(self) -> Span | None:
        """获取当前跨度"""
        return self._tracer.current_span

    def get_tracer(self) -> AgentTracer:
        """获取追踪器"""
        return self._tracer

    async def traced_execution(
        self,
        func: Callable,
        kind: SpanKind = SpanKind.AGENT,
        name: str = "",
        input_data: dict[str, Any] = None,
    ) -> Any:
        """带追踪的执行

        Args:
            func: 要执行的函数
            kind: 跨度类型
            name: 跨度名称
            input_data: 输入数据

        Returns:
            Any: 执行结果
        """
        span_name = name or func.__name__ if hasattr(func, "__name__") else "execution"
        self.start_span(kind, span_name, input_data)

        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result

            self.end_span(
                status=SpanStatus.SUCCESS,
                output_data={"result": str(result)[:500]},
            )
            return result

        except Exception as e:
            import traceback

            self.end_span(
                status=SpanStatus.ERROR,
                error=str(e),
                error_stack=traceback.format_exc(),
            )
            raise
