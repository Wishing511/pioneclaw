import asyncio
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_active_user
from app.core import get_db
from app.core.database import async_session_maker
from app.core.time_utils import format_dt as _format_dt
from app.models import AIModelConfig, ApiUsage, ChatTask, User
from app.modules.llm import SimpleLLMProvider
from app.modules.memory import get_current_memory_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])


def _ensure_memory_manager():
    """获取 MemoryManage 单例（应在 main.py lifespan 中预初始化）。"""
    mm = get_current_memory_manager()
    if mm is None:
        raise RuntimeError(
            "Memory manager not initialized. "
            "Please ensure create_memory_manager() is called in main.py lifespan."
        )
    return mm


async def _record_api_usage(
    db: AsyncSession,
    user_id: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    is_success: bool,
    error_message: str | None = None,
) -> None:
    """记录 API 用量，失败时回滚并记录日志，不影响主流程。"""
    try:
        usage = ApiUsage(
            user_id=user_id,
            model=model or "unknown",
            call_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            duration_ms=duration_ms,
            is_success=is_success,
            error_message=error_message,
        )
        db.add(usage)
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.warning(
            f"API 用量记录失败: {e} | "
            f"params=(user_id={user_id}, model={model}, is_success={is_success}, "
            f"error_message={error_message})"
        )


async def _record_api_usage_from_provider(
    db: AsyncSession,
    user_id: int,
    model: str,
    provider: SimpleLLMProvider,
    duration_ms: int,
    is_success: bool,
    error_message: str | None = None,
) -> None:
    """基于 provider token 记录 API 用量。"""
    input_tokens = provider.last_input_tokens or 0
    output_tokens = provider.last_output_tokens or 0
    await _record_api_usage(
        db=db,
        user_id=user_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        is_success=is_success,
        error_message=error_message,
    )


class ChatMessage(BaseModel):
    role: str  # user, assistant, system
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model_config_id: int | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    success: bool
    message: str
    response: str | None = None
    model: str | None = None
    usage: dict | None = None
    latency_ms: int | None = None


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """对话补全接口"""
    # 获取模型配置
    if request.model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == request.model_config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="模型配置不存在")
    else:
        # 使用默认配置
        result = await db.execute(
            select(AIModelConfig).where(
                AIModelConfig.is_default, AIModelConfig.is_active
            )
        )
        config = result.scalar_one_or_none()
        if not config:
            # 尝试获取任意一个激活的配置
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_active).limit(1)
            )
            config = result.scalar_one_or_none()

    if not config:
        return ChatResponse(
            success=False,
            message="没有可用的 AI 模型配置，请先在「AI 模型配置」中添加配置",
        )

    if not config.api_key:
        return ChatResponse(
            success=False, message=f"配置「{config.display_name}」未设置 API Key"
        )

    # 构建请求
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    if config.provider == "anthropic":
        url = config.base_url or "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": config.model_name,
            "max_tokens": config.max_tokens,
            "messages": messages,
        }
        if messages and messages[0]["role"] == "system":
            body["system"] = messages.pop(0)["content"]
    else:  # OpenAI 兼容
        url = config.base_url or "https://api.openai.com/v1/chat/completions"
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": config.model_name,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "messages": messages,
        }

    try:
        start_time = time.time()
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=body)
        latency_ms = int((time.time() - start_time) * 1000)

        if response.status_code == 200:
            data = response.json()

            if config.provider == "anthropic":
                content = data.get("content", [{}])[0].get("text", "")
                usage = {
                    "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                }
            else:
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
                usage = {
                    "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                    "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
                }

            # 记录 API 使用情况
            total_tokens = usage["input_tokens"] + usage["output_tokens"]
            api_usage = ApiUsage(
                user_id=current_user.id,
                model=config.model_name,
                call_count=1,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=total_tokens,
                duration_ms=latency_ms,
                is_success=True,
            )
            db.add(api_usage)
            await db.commit()

            return ChatResponse(
                success=True,
                message="成功",
                response=content,
                model=config.model_name,
                usage=usage,
                latency_ms=latency_ms,
            )
        else:
            error_detail = response.text
            try:
                error_json = response.json()
                if "error" in error_json:
                    error_detail = error_json["error"].get("message", error_detail)
            except Exception:
                pass

            # 记录失败的 API 调用
            api_usage = ApiUsage(
                user_id=current_user.id,
                model=config.model_name,
                call_count=1,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                duration_ms=latency_ms,
                is_success=False,
                error_message=error_detail,
            )
            db.add(api_usage)
            await db.commit()

            return ChatResponse(
                success=False,
                message=f"API 错误 ({response.status_code}): {error_detail}",
                latency_ms=latency_ms,
            )
    except httpx.TimeoutException:
        # 记录超时
        api_usage = ApiUsage(
            user_id=current_user.id,
            model=config.model_name,
            call_count=1,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            duration_ms=0,
            is_success=False,
            error_message="请求超时",
        )
        db.add(api_usage)
        await db.commit()
        return ChatResponse(success=False, message="请求超时，请稍后重试")
    except Exception as e:
        # 记录其他错误
        api_usage = ApiUsage(
            user_id=current_user.id,
            model=config.model_name,
            call_count=1,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            duration_ms=0,
            is_success=False,
            error_message=str(e),
        )
        db.add(api_usage)
        await db.commit()
        return ChatResponse(success=False, message=f"请求失败: {str(e)}")


@router.get("/models")
async def list_available_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取可用的模型配置列表"""
    result = await db.execute(
        select(AIModelConfig)
        .where(AIModelConfig.is_active)
        .order_by(AIModelConfig.is_default.desc())
    )
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "display_name": c.display_name,
            "model_name": c.model_name,
            "provider": c.provider,
            "is_default": c.is_default,
        }
        for c in configs
    ]


# ==================== ReAct 对话接口 ====================


class ReActRequest(BaseModel):
    """ReAct 对话请求"""

    message: str
    context: list[ChatMessage] | None = None
    model_config_id: int | None = None
    max_iterations: int = 10
    enable_tools: bool = True
    session_id: str | None = None  # WebSocket 会话 ID
    fast_mode: bool = False  # 快速模式：禁用思考/推理，直接回复


class ReActResponse(BaseModel):
    """ReAct 对话响应"""

    success: bool
    message: str
    response: str | None = None
    thinking_content: str | None = None  # AI 推理/思考内容
    iterations: int = 0
    tool_calls: list[dict] = []
    latency_ms: int | None = None
    messages: list[dict] = []  # 分开的消息列表
    input_tokens: int | None = None
    output_tokens: int | None = None
    approval_id: int | None = None  # 安全网关审批ID
    pending_approval: bool = False  # 是否等待审批


class CompactRequest(BaseModel):
    """手动压缩请求"""

    messages: list[dict]  # 当前会话消息列表
    instruction: str | None = None  # 自定义压缩指令
    model_config_id: int | None = None  # 用于选择 compactor 的 LLM
    session_id: str | None = None  # 若提供，后端会持久化压缩结果到该 session


class CompactResponse(BaseModel):
    """手动压缩响应"""

    success: bool
    summary: str
    removed_messages: int = 0
    kept_messages: int = 0
    saved_tokens: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    message: str = ""
    messages: list[dict] = []  # 压缩后的消息列表，前端用于替换当前会话


# ==================== 任务持久化接口 ====================


class CreateChatTaskRequest(BaseModel):
    """创建聊天任务请求"""

    message: str = Field(..., max_length=10000)
    context: list[ChatMessage] | None = Field(default=None, max_length=30)
    model_config_id: int | None = None
    max_iterations: int = 10
    enable_tools: bool = True
    session_id: str | None = None
    fast_mode: bool = False


class CreateChatTaskResponse(BaseModel):
    """创建聊天任务响应"""

    success: bool
    task_id: str
    status: str  # queued / running
    position: int | None = None
    message: str = ""


class ChatTaskDetail(BaseModel):
    """聊天任务详情"""

    task_id: str
    session_id: str | None
    status: str
    final_response: str | None = None
    thinking_content: str | None = None
    tool_calls: list[dict] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    iterations: int = 0
    error_message: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


# 模块级状态跟踪
task_cancel_tokens: dict[str, Any] = {}
task_handles: dict[str, asyncio.Task] = {}
task_wait_futures: dict[str, asyncio.Future] = {}


@router.post("/react", response_model=ReActResponse)
async def react_chat(
    request: ReActRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    ReAct 推理对话接口

    使用 AgentLoop 进行多轮推理和工具调用
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"ReAct chat request: {request.message[:50]}")

    # 获取模型配置
    if request.model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == request.model_config_id)
        )
        config = result.scalar_one_or_none()
    else:
        result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
        config = result.scalar_one_or_none()

    if not config:
        return ReActResponse(success=False, message="没有可用的 AI 模型配置")

    # 导入 AgentLoop 和工具
    from pathlib import Path

    from app.modules.agent import AgentLoop
    from app.modules.agent.compactor import CompactionConfig, Compactor
    from app.modules.agent.compression_service import ContextCompressionService
    from app.modules.agent.context import ContextBuilder, PersonaConfig
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.tools import ToolRegistry, register_builtin_tools

    # 创建工具注册表
    tool_registry = ToolRegistry()
    if request.enable_tools:
        register_builtin_tools(tool_registry)
        tool_definitions = tool_registry.get_definitions()
        logger.info(f"Tools enabled: {len(tool_definitions)} tools registered")

    # 用 ContextBuilder 构建完整系统提示词
    from sqlalchemy.orm import selectinload

    from app.models import Workspace

    ws_result = await db.execute(
        select(Workspace)
        .options(selectinload(Workspace.organization))
        .where(
            Workspace.owner_id == current_user.id, Workspace.is_default
        )
    )
    workspace = ws_result.scalar_one_or_none()
    persona = PersonaConfig.from_workspace(workspace, current_user)
    mm = _ensure_memory_manager()
    ctx_builder = ContextBuilder(
        persona_config=persona,
        workspace=Path(workspace.path) if workspace and workspace.path else Path.home(),
        memory_manager=mm,
    )
    system_prompt = await ctx_builder.build_system_prompt_async(
        current_message=request.message,
    ) if request.enable_tools else None

    # 创建 LLM Provider
    provider = SimpleLLMProvider(config=config)

    # Context 压缩组件（Phase 1: 统一入口）
    # TokenBudget 由 AgentLoop 统一创建注入，此处不再重复创建
    from app.modules.agent.file_tracker import FileTracker

    context_pruner = ContextPruner()
    file_tracker = FileTracker(max_files=5, max_tokens=50_000)
    compactor = Compactor(
        config=CompactionConfig(),
        llm_client=provider,
        user_id=current_user.id,
        session_id=request.session_id,
    )
    compression_service = ContextCompressionService(
        budget=None,  # 由 AgentLoop 注入统一预算
        compactor=compactor,
        context_pruner=context_pruner,
        file_tracker=file_tracker,
    )

    # 安全网关：pre_input_call 输入过滤
    from app.core.security_client import apply_input_filter, security_client

    filtered_text, error = await apply_input_filter(
        security_client,
        request.message,
        context={
            "user_id": current_user.id,
            "username": current_user.username,
            "session_id": request.session_id,
        },
    )
    if error:
        # 安全网关审批：创建审批记录
        if error.get("action") == "approve":
            from app.models.approval import Approval, ApprovalStatus, ApprovalType

            approval = Approval(
                approval_type=ApprovalType.SECURITY_GATEWAY,
                status=ApprovalStatus.PENDING,
                title=f"安全网关审批: {request.message[:50]}...",
                description=error.get("reason", "安全检测触发审批流程"),
                requester_id=current_user.id,
                requester_org_id=current_user.organization_id,
                resource_type="security_check",
                resource_id=request.session_id or str(current_user.id),
                target_scope="org",
                target_org_id=current_user.organization_id,
                extra_data={
                    "risk_level": error.get("risk_level"),
                    "session_id": request.session_id,
                    "content_preview": request.message[:200],
                },
            )
            db.add(approval)
            await db.commit()
            await db.refresh(approval)
            return ReActResponse(
                success=False,
                message=error["message"],
                latency_ms=error["latency_ms"],
                approval_id=approval.id,
                pending_approval=True,
            )
        return ReActResponse(
            success=error["success"],
            message=error["message"],
            latency_ms=error["latency_ms"],
        )
    if filtered_text != request.message:
        request.message = filtered_text

    # 创建 AgentLoop
    agent_loop = AgentLoop(
        provider=provider,
        tools=tool_registry,
        system_prompt=system_prompt,
        model=config.model_name,
        context_window=config.context_window,
        max_iterations=request.max_iterations,
        file_tracker=file_tracker,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        session_id=request.session_id,
        user_role=current_user.role,
        context_pruner=context_pruner,
        compactor=compactor,
        compression_service=compression_service,
        security_client=security_client,
    )

    # 执行
    start_time = time.time()
    try:
        # 构建上下文
        context = None
        if request.context:
            context = [{"role": m.role, "content": m.content} for m in request.context]

        try:
            response_text = await agent_loop.process_direct(
                message=request.message,
                context=context,
                system_prompt=system_prompt,
            )
        except TypeError as type_error:
            logger.error(f"TypeError in process_direct: {type_error}", exc_info=True)
            return ReActResponse(
                success=False,
                message=f"类型错误: {type_error}",
                latency_ms=int((time.time() - start_time) * 1000),
            )

        latency_ms = int((time.time() - start_time) * 1000)

        # 提取思考内容（<!--THINKING:...--> 标记）
        import re

        thinking_parts = []
        clean_response = response_text or ""
        thinking_pattern = re.compile(r"<!--THINKING:(.*?)-->", re.DOTALL)
        for match in thinking_pattern.finditer(clean_response):
            thinking_parts.append(match.group(1))
        thinking_content = "".join(thinking_parts) if thinking_parts else None
        # 从响应中移除 thinking 标记和系统提示
        clean_response = thinking_pattern.sub("", clean_response).strip()
        clean_response = clean_response.replace("[思考中...]", "").strip()
        import re as re_mod

        clean_response = re_mod.sub(
            r"\[达到最大迭代次数 \d+\]", "", clean_response
        ).strip()

        # 构建分开的消息列表
        messages = []

        # 如果有工具调用，添加工具消息
        if agent_loop.last_tool_results:
            logger.info(f"Tool results: {agent_loop.last_tool_results}")
            for name, result in agent_loop.last_tool_results.items():
                messages.append({"type": "tool_call", "name": name, "result": result})

        # 添加 AI 回复消息
        if clean_response and clean_response.strip():
            messages.append({"type": "assistant", "content": clean_response.strip()})

        logger.info(
            f"ReAct completed in {latency_ms}ms, messages: {len(messages)}, thinking: {len(thinking_content) if thinking_content else 0} chars"
        )

        # 记录 API 用量
        await _record_api_usage_from_provider(
            db=db,
            user_id=current_user.id,
            model=config.model_name or "unknown",
            provider=provider,
            duration_ms=latency_ms,
            is_success=True,
        )

        input_tokens = provider.last_input_tokens or 0
        output_tokens = provider.last_output_tokens or 0

        return ReActResponse(
            success=True,
            message="执行成功",
            response=clean_response if clean_response else None,
            thinking_content=thinking_content,
            iterations=agent_loop.max_iterations,
            tool_calls=[],
            latency_ms=latency_ms,
            messages=messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception as e:
        import traceback

        latency_ms = int((time.time() - start_time) * 1000)
        logger.error(f"ReAct failed: {e}\n{traceback.format_exc()}")

        # 记录失败的 API 调用（仅记录异常类型，避免泄露敏感信息）
        await _record_api_usage(
            db=db,
            user_id=current_user.id,
            model=config.model_name or "unknown",
            input_tokens=0,
            output_tokens=0,
            duration_ms=latency_ms,
            is_success=False,
            error_message=type(e).__name__,
        )

        return ReActResponse(
            success=False,
            message=f"执行失败: {str(e)}",
            latency_ms=latency_ms,
        )


@router.post("/react/stream")
async def react_chat_stream(
    request: ReActRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    ReAct 推理流式对话接口（SSE）

    实时流式返回思考过程、工具调用和最终回复
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    # 获取模型配置（同 react endpoint）
    if request.model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == request.model_config_id)
        )
        config = result.scalar_one_or_none()
    else:
        result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
        config = result.scalar_one_or_none()

    if not config:

        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '没有可用的 AI 模型配置'})}\n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    from pathlib import Path

    from app.modules.agent import AgentLoop
    from app.modules.agent.context import ContextBuilder, PersonaConfig
    from app.modules.tools import ToolRegistry, register_builtin_tools

    # 创建工具注册表
    tool_registry = ToolRegistry()
    if request.enable_tools:
        register_builtin_tools(tool_registry)

    # 用 ContextBuilder 构建完整系统提示词
    from sqlalchemy.orm import selectinload

    from app.models import Workspace
    from app.modules.agent.compactor import CompactionConfig as CC
    from app.modules.agent.compactor import Compactor as C
    from app.modules.agent.compression_service import ContextCompressionService
    from app.modules.agent.context_pruner import ContextPruner as CP

    ws_result = await db.execute(
        select(Workspace)
        .options(selectinload(Workspace.organization))
        .where(
            Workspace.owner_id == current_user.id, Workspace.is_default
        )
    )
    workspace = ws_result.scalar_one_or_none()
    persona = PersonaConfig.from_workspace(workspace, current_user)
    mm = _ensure_memory_manager()
    ctx_builder = ContextBuilder(
        persona_config=persona,
        workspace=Path(workspace.path) if workspace and workspace.path else Path.home(),
        memory_manager=mm,
    )
    system_prompt = await ctx_builder.build_system_prompt_async(
        current_message=request.message,
    ) if request.enable_tools else None

    provider = SimpleLLMProvider(config=config)
    provider.fast_mode = request.fast_mode

    # Context 压缩组件（Phase 1: 统一入口）
    # TokenBudget 由 AgentLoop 统一创建注入，此处不再重复创建
    context_pruner = CP()
    from app.modules.agent.file_tracker import FileTracker

    file_tracker = FileTracker(max_files=5, max_tokens=50_000)
    compactor = C(
        config=CC(),
        llm_client=provider,
        user_id=current_user.id,
        session_id=request.session_id,
    )
    compression_service = ContextCompressionService(
        budget=None,  # 由 AgentLoop 注入统一预算
        compactor=compactor,
        context_pruner=context_pruner,
        file_tracker=file_tracker,
    )

    agent_loop = AgentLoop(
        provider=provider,
        tools=tool_registry,
        system_prompt=system_prompt,
        model=config.model_name,
        context_window=config.context_window,
        max_iterations=request.max_iterations,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        session_id=request.session_id,
        user_role=current_user.role,
        context_pruner=context_pruner,
        compactor=compactor,
        compression_service=compression_service,
        file_tracker=file_tracker,
    )

    thinking_pattern = re.compile(r"<!--THINKING:(.*?)-->", re.DOTALL)
    tool_start_pattern = re.compile(r"<!--TOOL_START:(.*?)-->")
    tool_result_pattern = re.compile(r"<!--TOOL_RESULT:(.*?)-->", re.DOTALL)
    tool_error_pattern = re.compile(r"<!--TOOL_ERROR:(.*?)-->", re.DOTALL)

    # 设执行上下文（Runner 工具需要知道当前用户）
    from app.core.execution_context import current_user_id

    current_user_id.set(current_user.id)

    async def generate():
        start_time = time.time()
        content_buffer = ""
        last_good_content = ""  # 保留上一轮有效内容，防止最后迭代为空
        thinking_buffer = ""

        # 并发控制
        import uuid as _uuid

        from app.core.concurrency import concurrency_manager

        task_id = str(_uuid.uuid4())[:8]
        result = await concurrency_manager.acquire(
            current_user.id, task_id, len(request.message)
        )

        if result.rejected:
            yield f"data: {json.dumps({'type': 'error', 'message': '当前排队人数过多，请稍后重试'})}\n\n"
            return

        if result.queued:
            yield f"data: {json.dumps({'type': 'queued', 'position': result.position, 'wait_ms': result.estimated_wait_ms})}\n\n"
            try:
                await asyncio.wait_for(
                    result.wait_future,
                    timeout=concurrency_manager.queue_timeout_seconds,
                )
            except asyncio.TimeoutError:
                concurrency_manager.cancel_wait(current_user.id, task_id)
                yield f"data: {json.dumps({'type': 'error', 'message': '排队超时，请稍后重试'})}\n\n"
                return
            if not result.wait_future.result():
                yield f"data: {json.dumps({'type': 'error', 'message': '排队已取消'})}\n\n"
                return
            yield f"data: {json.dumps({'type': 'queued', 'position': 0, 'wait_ms': 0})}\n\n"

        try:
            context = None
            if request.context:
                context = [
                    {"role": m.role, "content": m.content} for m in request.context
                ]

            async for chunk in agent_loop.process_message(
                message=request.message,
                context=context,
                system_prompt=system_prompt,
                yield_intermediate=True,
                use_sse=True,
            ):
                # 提取思考内容（LLM 的 reasoning_content）
                for match in thinking_pattern.finditer(chunk):
                    thinking_content = match.group(1)
                    thinking_buffer += thinking_content
                    yield f"data: {json.dumps({'type': 'thinking', 'content': thinking_content}, ensure_ascii=False)}\n\n"

                # 提取工具事件（实时通知前端工具执行状态）
                for match in tool_start_pattern.finditer(chunk):
                    try:
                        tool_data = json.loads(match.group(1))
                        payload = {
                            'type': 'tool_start',
                            'name': tool_data.get('name', ''),
                            'tool_call_id': tool_data.get('tool_call_id', ''),
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    except json.JSONDecodeError:
                        pass
                for match in tool_result_pattern.finditer(chunk):
                    try:
                        tool_data = json.loads(match.group(1))
                        payload = {
                            'type': 'tool_result',
                            'name': tool_data.get('name', ''),
                            'tool_call_id': tool_data.get('tool_call_id', ''),
                            'result': tool_data.get('result', ''),
                            'duration_ms': tool_data.get('duration_ms'),
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    except json.JSONDecodeError:
                        pass
                for match in tool_error_pattern.finditer(chunk):
                    try:
                        tool_data = json.loads(match.group(1))
                        payload = {
                            'type': 'tool_error',
                            'name': tool_data.get('name', ''),
                            'tool_call_id': tool_data.get('tool_call_id', ''),
                            'error': tool_data.get('error', ''),
                            'duration_ms': tool_data.get('duration_ms'),
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    except json.JSONDecodeError:
                        pass

                # 提取非思考内容
                clean = thinking_pattern.sub("", chunk)
                clean = tool_start_pattern.sub("", clean)
                clean = tool_result_pattern.sub("", clean)
                clean = tool_error_pattern.sub("", clean)

                # 检测迭代边界：[思考中...] 表示新一轮迭代开始
                is_boundary = "[思考中...]" in clean
                if is_boundary:
                    if content_buffer.strip():
                        last_good_content = content_buffer  # 保存后备
                    content_buffer = ""
                    yield f"data: {json.dumps({'type': 'new_iteration'}, ensure_ascii=False)}\n\n"

                # 移除标记
                clean = clean.replace("[思考中...]", "")
                clean = re.sub(r"\[达到最大迭代次数 \d+\]", "", clean)
                if is_boundary:
                    clean = clean.strip()  # 边界残余（\n）彻底丢弃
                else:
                    clean = clean.strip(" \t\r")  # 保留 \n 用于 markdown 换行
                if clean:
                    content_buffer += clean
                    last_good_content = ""  # 本轮已有内容，后备已过期
                    yield f"data: {json.dumps({'type': 'content', 'content': clean}, ensure_ascii=False)}\n\n"

            latency_ms = int((time.time() - start_time) * 1000)

            # 记录 API 用量
            await _record_api_usage_from_provider(
                db=db,
                user_id=current_user.id,
                model=config.model_name or "unknown",
                provider=provider,
                duration_ms=latency_ms,
                is_success=True,
            )

            # 确定最终响应
            final_response = content_buffer.strip() or last_good_content.strip()

            # 如果 LLM 没有生成文字回复但调用了工具，提示用户查看工具结果
            if not final_response and agent_loop.last_tool_results:
                tool_names = list(agent_loop.last_tool_results.keys())
                if tool_names:
                    final_response = (
                        f"已执行 {len(tool_names)} 个工具，请查看上方工具结果。"
                    )

            # 发送完成事件
            input_tokens = provider.last_input_tokens or 0
            # 构建上下文可视化报告（改进项 6）
            # 重建 messages（与 AgentLoop 内部一致）用于报告
            report_messages = list(context) if context else []
            if system_prompt:
                report_messages.insert(0, {"role": "system", "content": system_prompt})
            report_messages.append({"role": "user", "content": request.message})
            context_report = (
                compression_service.build_context_report(
                    report_messages, provider
                )
                if compression_service
                else {}
            )
            # 向后兼容：保留 context_usage 字段
            backward_compatible_usage = {
                "input_tokens": input_tokens,
                "output_tokens": provider.last_output_tokens,
                "context_window": context_report.get("context_window", 0),
                "usage_percent": context_report.get("usage_percent", 0),
                "status": context_report.get("status", "unknown"),
            }
            done_event = {
                "type": "done",
                "thinking_content": thinking_buffer if thinking_buffer else None,
                "response": final_response or "(未获取到有效回复)",
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": provider.last_output_tokens,
                "context_report": context_report,
                "context_usage": backward_compatible_usage,  # 向后兼容
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

        except Exception as e:
            import traceback

            logger.error(f"Stream error: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            concurrency_manager.release(current_user.id)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/compact", response_model=CompactResponse)
async def compact_context(
    request: CompactRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    手动压缩上下文

    接收完整消息列表，返回压缩后的摘要和统计信息。
    支持自定义压缩指令（如"重点保留 API 设计"）。
    """
    import logging

    logger = logging.getLogger(__name__)

    # 获取模型配置
    config = None
    if request.model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == request.model_config_id)
        )
        config = result.scalar_one_or_none()

    if not config:
        result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
        config = result.scalar_one_or_none()

    if not config:
        return CompactResponse(
            success=False,
            message="没有可用的 AI 模型配置",
            summary="",
        )

    from app.modules.agent.compactor import CompactionConfig, Compactor
    from app.modules.agent.compression_service import ContextCompressionService
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.llm import SimpleLLMProvider

    provider = SimpleLLMProvider(config=config)

    # TokenBudget 由 AgentLoop 统一创建注入，manual_compact 不依赖阈值
    context_pruner = ContextPruner()
    compactor = Compactor(
        config=CompactionConfig(),
        llm_client=provider,
        user_id=current_user.id,
    )
    compression_service = ContextCompressionService(
        budget=None,  # manual_compact 忽略阈值
        compactor=compactor,
        context_pruner=context_pruner,
    )

    try:
        report, compacted_messages = await compression_service.manual_compact(
            messages=request.messages,
            instruction=request.instruction,
        )

        # P2: 若提供了 session_id，将压缩结果持久化到后端
        if request.session_id:
            await _persist_compacted_session(
                db, request.session_id, current_user.id, compacted_messages
            )

        return CompactResponse(
            success=True,
            summary=report.summary,
            removed_messages=report.removed_messages,
            kept_messages=report.kept_messages,
            saved_tokens=report.saved_tokens,
            before_tokens=report.before_tokens,
            after_tokens=report.after_tokens,
            message="压缩完成",
            messages=compacted_messages,
        )
    except Exception as e:
        logger.error(f"Compact endpoint failed: {e}", exc_info=True)
        return CompactResponse(
            success=False,
            summary="",
            message=f"压缩失败: {str(e)}",
            messages=request.messages,
        )


async def _persist_compacted_session(
    db: AsyncSession,
    session_id: str,
    user_id: int,
    messages: list[dict],
) -> None:
    """将压缩后的消息列表持久化到 session，替换原有消息。"""
    from sqlalchemy import delete

    from app.models import Session, SessionMessage

    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return

    # 删除旧消息
    await db.execute(
        delete(SessionMessage).where(SessionMessage.session_id == session_id)
    )

    # 写入压缩后的新消息
    import json as _json

    for msg in messages:
        tool_calls = msg.get("tool_calls")
        db_msg = SessionMessage(
            session_id=session_id,
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            tool_calls=_json.dumps(tool_calls) if tool_calls else None,
        )
        db.add(db_msg)

    session.message_count = len(messages)
    await db.commit()


# ==================== 聊天任务持久化实现 ====================


async def _get_chat_task(
    db: AsyncSession, task_id: str, user_id: int
) -> "ChatTask | None":
    """查询任务并验证所有权"""
    from app.models import ChatTask

    result = await db.execute(
        select(ChatTask).where(ChatTask.id == task_id, ChatTask.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _run_chat_task(
    task_id: str,
    user_id: int,
    request: CreateChatTaskRequest,
    user_role: str,
) -> None:
    """在后台执行 AgentLoop 并将输出写入 buffer"""
    from datetime import datetime, timezone

    from app.core.concurrency import concurrency_manager
    from app.core.execution_context import current_user_id
    from app.models import ChatTask
    from app.modules.agent import AgentLoop
    from app.modules.agent.chat_task_buffer import get_buffer_registry
    from app.modules.agent.chat_task_runner import ChatTaskRunner
    from app.modules.agent.compactor import CompactionConfig, Compactor
    from app.modules.agent.compression_service import ContextCompressionService
    from app.modules.agent.context import ContextBuilder, PersonaConfig
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.agent.file_tracker import FileTracker
    from app.modules.llm import SimpleLLMProvider
    from app.modules.tools import ToolRegistry, register_builtin_tools

    current_user_id.set(user_id)
    buffer = get_buffer_registry().get_or_create(task_id)

    # 每个后台任务使用独立的数据库会话
    async with async_session_maker() as db:
        try:
            # 更新任务状态为 running
            task = await db.get(ChatTask, task_id)
            if task:
                task.status = "running"
                task.started_at = datetime.now(timezone.utc)
                await db.commit()

            # 获取模型配置
            result = await db.execute(
                select(AIModelConfig).where(
                    AIModelConfig.id == request.model_config_id
                )
                if request.model_config_id
                else select(AIModelConfig).where(AIModelConfig.is_default)
            )
            config = result.scalar_one_or_none()
            if not config:
                await buffer.mark_failed("没有可用的 AI 模型配置")
                await _update_task_status(task_id, "failed", db, error="没有可用的 AI 模型配置")
                return

            # 创建工具注册表
            tool_registry = ToolRegistry()
            if request.enable_tools:
                register_builtin_tools(tool_registry)

            # 构建系统提示词
            from app.models import Workspace

            ws_result = await db.execute(
                select(Workspace)
                .options(selectinload(Workspace.organization))
                .where(Workspace.owner_id == user_id, Workspace.is_default)
            )
            workspace = ws_result.scalar_one_or_none()
            persona = PersonaConfig.from_workspace(workspace, None)
            from pathlib import Path
            mm = _ensure_memory_manager()

            ctx_builder = ContextBuilder(
                persona_config=persona,
                workspace=Path(workspace.path) if workspace and workspace.path else Path.home(),
                memory_manager=mm,
            )
            system_prompt = await ctx_builder.build_system_prompt_async(
                current_message=request.message,
            ) if request.enable_tools else None

            # 创建 Provider
            provider = SimpleLLMProvider(config=config)
            provider.fast_mode = request.fast_mode

            # Context 压缩组件
            context_pruner = ContextPruner()
            file_tracker = FileTracker(max_files=5, max_tokens=50_000)
            compactor = Compactor(
                config=CompactionConfig(),
                llm_client=provider,
                user_id=user_id,
                session_id=request.session_id,
            )
            compression_service = ContextCompressionService(
                budget=None,
                compactor=compactor,
                context_pruner=context_pruner,
                file_tracker=file_tracker,
            )

            # 创建 AgentLoop
            agent_loop = AgentLoop(
                provider=provider,
                tools=tool_registry,
                system_prompt=system_prompt,
                model=config.model_name,
                context_window=config.context_window,
                max_iterations=request.max_iterations,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                session_id=request.session_id,
                user_role=user_role,
                context_pruner=context_pruner,
                compactor=compactor,
                compression_service=compression_service,
                file_tracker=file_tracker,
            )

            # 创建 CancellationToken
            from app.modules.agent.task_manager import CancellationToken

            cancel_token = CancellationToken()
            task_cancel_tokens[task_id] = cancel_token

            # 构建上下文
            context = None
            if request.context:
                context = [{"role": m.role, "content": m.content} for m in request.context]

            # 运行 AgentLoop
            runner = ChatTaskRunner(agent_loop, task_id, buffer)
            result = await runner.run(
                message=request.message,
                context=context,
                system_prompt=system_prompt,
                cancel_token=cancel_token,
            )

            # 持久化结果
            await _persist_task_result(task_id, result, db)

        except asyncio.CancelledError:
            await buffer.mark_failed("任务已取消")
            await _update_task_status(task_id, "cancelled", db)
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Chat task {task_id} failed: {e}", exc_info=True)
            await buffer.mark_failed("任务执行失败，请稍后重试")
            await _update_task_status(task_id, "failed", db, error="任务执行失败，请稍后重试")
        finally:
            concurrency_manager.release(user_id)
            task_cancel_tokens.pop(task_id, None)
            task_handles.pop(task_id, None)


async def _wait_and_start_task(
    task_id: str,
    user_id: int,
    request: CreateChatTaskRequest,
    wait_future: asyncio.Future,
    user_role: str,
) -> None:
    """等待排队完成，然后启动任务"""
    from app.core.concurrency import concurrency_manager

    try:
        try:
            await asyncio.wait_for(
                wait_future,
                timeout=concurrency_manager.queue_timeout_seconds,
            )
            if not wait_future.result():
                # 排队被取消
                concurrency_manager.release(user_id)
                async with async_session_maker() as db:
                    await _update_task_status(task_id, "cancelled", db, error="排队已取消")
                return
        except asyncio.TimeoutError:
            concurrency_manager.release(user_id)
            async with async_session_maker() as db:
                await _update_task_status(task_id, "failed", db, error="排队超时")
            return

        # 启动实际执行
        task = asyncio.create_task(_run_chat_task(task_id, user_id, request, user_role))
        task_handles[task_id] = task
        task_wait_futures.pop(task_id, None)
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"_wait_and_start_task {task_id} crashed: {e}", exc_info=True)
        concurrency_manager.release(user_id)
        async with async_session_maker() as db:
            await _update_task_status(task_id, "failed", db, error="排队处理异常")


async def _update_task_status(
    task_id: str,
    status: str,
    db: AsyncSession,
    error: str | None = None,
) -> None:
    """更新任务状态"""
    from datetime import datetime, timezone

    from app.models import ChatTask

    task = await db.get(ChatTask, task_id)
    if task:
        task.status = status
        if error:
            task.error_message = error
        if status in ("completed", "failed", "cancelled"):
            task.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def _persist_task_result(
    task_id: str,
    result: dict[str, Any],
    db: AsyncSession,
) -> None:
    """任务完成后，将结果保存到 ChatTask 和 SessionMessage"""
    from datetime import datetime, timezone

    from app.models import ChatTask, Session, SessionMessage

    task = await db.get(ChatTask, task_id)
    if not task:
        return

    task.status = "completed"
    task.final_response = result.get("final_response")
    task.thinking_content = result.get("thinking_content")
    task.input_tokens = result.get("input_tokens", 0)
    task.output_tokens = result.get("output_tokens", 0)
    task.latency_ms = result.get("latency_ms", 0)
    task.iterations = result.get("iterations", 0)
    task.output_chunks = result.get("output_chunks")
    task.completed_at = datetime.now(timezone.utc)

    # 解析 tool_calls 从 output_chunks
    tool_calls = _extract_tool_calls_from_chunks(task.output_chunks)
    task.tool_calls = tool_calls if tool_calls else None

    # 保存 assistant 消息到 SessionMessage
    if task.session_id and task.final_response:
        msg = SessionMessage(
            session_id=task.session_id,
            role="assistant",
            content=task.final_response,
            reasoning_content=task.thinking_content,
            tool_calls=tool_calls if tool_calls else None,
        )
        db.add(msg)

        # 更新会话消息计数
        session = await db.get(Session, task.session_id)
        if session:
            session.message_count = (session.message_count or 0) + 1
            session.updated_at = datetime.now(timezone.utc)

    await db.commit()


def _extract_tool_calls_from_chunks(chunks: list[dict] | None) -> list[dict]:
    """从 output_chunks 中提取 tool_calls"""
    if not chunks:
        return []

    tool_calls = []
    current_tool = None

    for chunk in chunks:
        t = chunk.get("type")
        if t == "tool_start":
            current_tool = {
                "name": chunk.get("name", ""),
                "tool_call_id": chunk.get("tool_call_id", ""),
                "status": "running",
            }
        elif t == "tool_result" and current_tool:
            current_tool["result"] = chunk.get("result", "")
            current_tool["duration_ms"] = chunk.get("duration_ms")
            current_tool["status"] = "success"
            tool_calls.append(current_tool)
            current_tool = None
        elif t == "tool_error" and current_tool:
            current_tool["error"] = chunk.get("error", "")
            current_tool["duration_ms"] = chunk.get("duration_ms")
            current_tool["status"] = "error"
            tool_calls.append(current_tool)
            current_tool = None

    return tool_calls


@router.post("/react/tasks", response_model=CreateChatTaskResponse)
async def create_chat_task(
    request: CreateChatTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    创建聊天任务

    返回 task_id，前端用 task_id 连接 SSE 流消费输出
    """
    import uuid
    from datetime import datetime, timezone

    from app.core.concurrency import concurrency_manager
    from app.models import ChatTask

    task_id = str(uuid.uuid4())

    # 校验 session_id 归属（防止跨用户写入）
    if request.session_id:
        from app.models import Session

        session = await db.get(Session, request.session_id)
        if not session or session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权访问该会话")

    # 创建任务记录
    chat_task = ChatTask(
        id=task_id,
        user_id=current_user.id,
        session_id=request.session_id,
        message=request.message,
        context=[{"role": m.role, "content": m.content} for m in request.context]
        if request.context
        else None,
        model_config_id=request.model_config_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(chat_task)
    await db.commit()

    # 申请并发配额
    result = await concurrency_manager.acquire(
        current_user.id, task_id, len(request.message)
    )

    if result.rejected:
        chat_task.status = "failed"
        chat_task.error_message = "当前排队人数过多，请稍后重试"
        chat_task.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return CreateChatTaskResponse(
            success=False,
            task_id=task_id,
            status="failed",
            message="当前排队人数过多，请稍后重试",
        )

    if result.queued:
        chat_task.status = "queued"
        await db.commit()
        # 记录 wait_future，以便取消时可以中断排队
        task_wait_futures[task_id] = result.wait_future
        # 启动后台等待任务
        asyncio.create_task(
            _wait_and_start_task(
                task_id=task_id,
                user_id=current_user.id,
                request=request,
                wait_future=result.wait_future,
                user_role=current_user.role,
            )
        )
        return CreateChatTaskResponse(
            success=True,
            task_id=task_id,
            status="queued",
            position=result.position,
            message=f"排队中，前方还有 {result.position} 个任务",
        )

    # 直接启动
    chat_task.status = "running"
    chat_task.started_at = datetime.now(timezone.utc)
    await db.commit()

    # 预创建 buffer，避免前端立即连接时 race condition
    from app.modules.agent.chat_task_buffer import get_buffer_registry
    get_buffer_registry().get_or_create(task_id)

    task = asyncio.create_task(_run_chat_task(task_id, current_user.id, request, current_user.role))
    task_handles[task_id] = task

    return CreateChatTaskResponse(
        success=True,
        task_id=task_id,
        status="running",
    )


@router.get("/react/tasks/{task_id}", response_model=ChatTaskDetail)
async def get_chat_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取任务状态和结果"""
    task = await _get_chat_task(db, task_id, current_user.id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return ChatTaskDetail(
        task_id=task.id,
        session_id=task.session_id,
        status=task.status,
        final_response=task.final_response,
        thinking_content=task.thinking_content,
        tool_calls=task.tool_calls,
        input_tokens=task.input_tokens,
        output_tokens=task.output_tokens,
        latency_ms=task.latency_ms,
        iterations=task.iterations,
        error_message=task.error_message,
        created_at=_format_dt(task.created_at),
        started_at=_format_dt(task.started_at),
        completed_at=_format_dt(task.completed_at),
    )


@router.get("/react/tasks/{task_id}/stream")
async def stream_chat_task(
    task_id: str,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """SSE 流式消费任务输出，支持从指定 offset 重连"""
    from app.modules.agent.chat_task_buffer import get_buffer_registry

    # 验证任务所有权
    task = await _get_chat_task(db, task_id, current_user.id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    buffer = get_buffer_registry().get(task_id)

    # 任务已完成且 buffer 已清理：从 DB 重放
    if buffer is None and task.status in ("completed", "failed", "cancelled"):
        async def replay_from_db():
            chunks = task.output_chunks or []
            has_done = False
            for i, chunk in enumerate(chunks):
                if i >= offset:
                    data = {**chunk, "_chunk_index": i}
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                if chunk.get("type") == "done":
                    has_done = True
            # 只有 output_chunks 中不包含 done 时才补发（兼容旧数据）
            if not has_done:
                done_event = {
                    "type": "done",
                    "response": task.final_response or "(未获取到有效回复)",
                    "thinking_content": task.thinking_content,
                    "latency_ms": task.latency_ms,
                    "input_tokens": task.input_tokens,
                    "output_tokens": task.output_tokens,
                    "_chunk_index": len(chunks),
                }
                yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

        return StreamingResponse(replay_from_db(), media_type="text/event-stream")

    # buffer 丢失且任务尚未完成：返回 410，前端降级到 REST API 查询
    if buffer is None:
        raise HTTPException(status_code=410, detail="任务输出已过期，无法恢复")

    # 从 buffer 消费
    async def generate():
        async for chunk in buffer.consume_from(offset):
            if chunk.index == -1:  # keepalive
                yield ":keepalive\n\n"
            else:
                data = {**chunk.data, "_chunk_index": chunk.index}
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/react/tasks/{task_id}/cancel")
async def cancel_chat_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消正在运行的任务"""
    from app.modules.agent.chat_task_buffer import get_buffer_registry

    task = await _get_chat_task(db, task_id, current_user.id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status not in ("queued", "running"):
        return {"success": False, "message": f"任务状态为 {task.status}，无法取消"}

    # 1. 取消排队中的 wait_future
    wait_future = task_wait_futures.pop(task_id, None)
    if wait_future and not wait_future.done():
        wait_future.set_result(False)

    # 2. 取消 CancellationToken
    cancel_token = task_cancel_tokens.pop(task_id, None)
    if cancel_token:
        cancel_token.cancel()

    # 3. 取消 asyncio.Task
    task_handle = task_handles.pop(task_id, None)
    if task_handle and not task_handle.done():
        task_handle.cancel()

    # 4. 标记 buffer 失败
    buffer = get_buffer_registry().get(task_id)
    if buffer:
        await buffer.mark_failed("用户取消")

    # 5. 更新 DB
    from datetime import datetime, timezone

    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"success": True, "message": "任务已取消"}
