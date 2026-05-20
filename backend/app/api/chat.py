from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel
import httpx
import json
import time
import asyncio
from datetime import datetime

from app.core import get_db
from app.models import AIModelConfig, ApiUsage
from app.api.auth import get_current_active_user
from app.models import User

router = APIRouter(prefix="/chat", tags=["对话"])


class ChatMessage(BaseModel):
    role: str  # user, assistant, system
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model_config_id: Optional[int] = None
    stream: bool = False


class ChatResponse(BaseModel):
    success: bool
    message: str
    response: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[dict] = None
    latency_ms: Optional[int] = None


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
            select(AIModelConfig).where(AIModelConfig.is_default == True, AIModelConfig.is_active == True)
        )
        config = result.scalar_one_or_none()
        if not config:
            # 尝试获取任意一个激活的配置
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_active == True).limit(1)
            )
            config = result.scalar_one_or_none()
    
    if not config:
        return ChatResponse(
            success=False,
            message="没有可用的 AI 模型配置，请先在「AI 模型配置」中添加配置"
        )
    
    if not config.api_key:
        return ChatResponse(
            success=False,
            message=f"配置「{config.display_name}」未设置 API Key"
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
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
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
                is_success=True
            )
            db.add(api_usage)
            await db.commit()
            
            return ChatResponse(
                success=True,
                message="成功",
                response=content,
                model=config.model_name,
                usage=usage,
                latency_ms=latency_ms
            )
        else:
            error_detail = response.text
            try:
                error_json = response.json()
                if "error" in error_json:
                    error_detail = error_json["error"].get("message", error_detail)
            except:
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
                error_message=error_detail
            )
            db.add(api_usage)
            await db.commit()
            
            return ChatResponse(
                success=False,
                message=f"API 错误 ({response.status_code}): {error_detail}",
                latency_ms=latency_ms
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
            error_message="请求超时"
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
            error_message=str(e)
        )
        db.add(api_usage)
        await db.commit()
        return ChatResponse(success=False, message=f"请求失败: {str(e)}")


@router.get("/models")
async def list_available_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取可用的模型配置列表"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.is_active == True).order_by(AIModelConfig.is_default.desc())
    )
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "display_name": c.display_name,
            "model_name": c.model_name,
            "provider": c.provider,
            "is_default": c.is_default
        }
        for c in configs
    ]


# ==================== ReAct 对话接口 ====================

class ReActRequest(BaseModel):
    """ReAct 对话请求"""
    message: str
    context: Optional[List[ChatMessage]] = None
    model_config_id: Optional[int] = None
    max_iterations: int = 10
    enable_tools: bool = True
    session_id: Optional[str] = None  # WebSocket 会话 ID
    fast_mode: bool = False  # 快速模式：禁用思考/推理，直接回复


class ReActResponse(BaseModel):
    """ReAct 对话响应"""
    success: bool
    message: str
    response: Optional[str] = None
    thinking_content: Optional[str] = None  # AI 推理/思考内容
    iterations: int = 0
    tool_calls: List[dict] = []
    latency_ms: Optional[int] = None
    messages: List[dict] = []  # 分开的消息列表
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    approval_id: Optional[int] = None  # 安全网关审批ID
    pending_approval: bool = False  # 是否等待审批


class CompactRequest(BaseModel):
    """手动压缩请求"""
    messages: List[dict]  # 当前会话消息列表
    instruction: Optional[str] = None  # 自定义压缩指令
    model_config_id: Optional[int] = None  # 用于选择 compactor 的 LLM
    session_id: Optional[str] = None  # 若提供，后端会持久化压缩结果到该 session


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
    messages: List[dict] = []  # 压缩后的消息列表，前端用于替换当前会话


@router.post("/react", response_model=ReActResponse)
async def react_chat(
    request: ReActRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_default == True)
        )
        config = result.scalar_one_or_none()
    
    if not config:
        return ReActResponse(success=False, message="没有可用的 AI 模型配置")
    
    # 导入 AgentLoop 和工具
    from app.modules.agent import AgentLoop
    from app.modules.tools import ToolRegistry, register_builtin_tools
    from app.modules.agent.context import ContextBuilder, PersonaConfig
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.agent.compactor import Compactor, CompactionConfig
    from app.modules.agent.token_budget import TokenBudget
    from app.modules.agent.compression_service import ContextCompressionService
    from pathlib import Path

    # 创建工具注册表
    tool_registry = ToolRegistry()
    tools_list = []
    if request.enable_tools:
        register_builtin_tools(tool_registry)
        tool_definitions = tool_registry.get_definitions()
        tools_list = tool_definitions
        logger.info(f"Tools enabled: {len(tool_definitions)} tools registered")

    # 用 ContextBuilder 构建完整系统提示词
    from app.models import Workspace
    ws_result = await db.execute(
        select(Workspace).where(Workspace.owner_id == current_user.id, Workspace.is_default == True)
    )
    workspace = ws_result.scalar_one_or_none()
    persona = PersonaConfig.from_workspace(workspace, current_user)
    ctx_builder = ContextBuilder(
        persona_config=persona,
        workspace=Path(workspace.path) if workspace and workspace.path else Path.home(),
    )
    system_prompt = ctx_builder.build_system_prompt() if request.enable_tools else None

    # 创建 LLM Provider
    provider = SimpleLLMProvider(config=config)

    # Context 压缩组件（Phase 1: 统一入口）
    from app.modules.agent.file_tracker import FileTracker
    token_budget = TokenBudget(context_window=config.context_window)
    context_pruner = ContextPruner()
    file_tracker = FileTracker(max_files=5, max_tokens=50_000)
    compactor = Compactor(
        config=CompactionConfig(context_window=config.context_window),
        llm_client=provider,
        user_id=current_user.id,
        session_id=request.session_id,
    )
    compression_service = ContextCompressionService(
        budget=token_budget,
        compactor=compactor,
        context_pruner=context_pruner,
        file_tracker=file_tracker,
    )

    # 安全网关：pre_input_call 输入过滤
    from app.core.security_client import security_client, apply_input_filter
    filtered_text, error = await apply_input_filter(
        security_client,
        request.message,
        context={
            "user_id": current_user.id,
            "username": current_user.username,
            "session_id": request.session_id,
        }
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
        thinking_pattern = re.compile(r'<!--THINKING:(.*?)-->', re.DOTALL)
        for match in thinking_pattern.finditer(clean_response):
            thinking_parts.append(match.group(1))
        thinking_content = "".join(thinking_parts) if thinking_parts else None
        # 从响应中移除 thinking 标记和系统提示
        clean_response = thinking_pattern.sub("", clean_response).strip()
        clean_response = clean_response.replace("[思考中...]", "").strip()
        import re as re_mod
        clean_response = re_mod.sub(r'\[达到最大迭代次数 \d+\]', '', clean_response).strip()

        # 构建分开的消息列表
        messages = []

        # 如果有工具调用，添加工具消息
        if agent_loop.last_tool_results:
            logger.info(f"Tool results: {agent_loop.last_tool_results}")
            for name, result in agent_loop.last_tool_results.items():
                messages.append({
                    "type": "tool_call",
                    "name": name,
                    "result": result
                })

        # 添加 AI 回复消息
        if clean_response and clean_response.strip():
            messages.append({
                "type": "assistant",
                "content": clean_response.strip()
            })

        logger.info(f"ReAct completed in {latency_ms}ms, messages: {len(messages)}, thinking: {len(thinking_content) if thinking_content else 0} chars")

        # 获取 token 使用量
        input_tokens = provider.last_input_tokens
        output_tokens = provider.last_output_tokens

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
        logger.error(f"ReAct failed: {e}\n{traceback.format_exc()}")
        return ReActResponse(
            success=False,
            message=f"执行失败: {str(e)}",
            latency_ms=int((time.time() - start_time) * 1000),
        )


@router.post("/react/stream")
async def react_chat_stream(
    request: ReActRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_default == True)
        )
        config = result.scalar_one_or_none()

    if not config:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '没有可用的 AI 模型配置'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    from app.modules.agent import AgentLoop
    from app.modules.tools import ToolRegistry, register_builtin_tools
    from app.modules.agent.context import ContextBuilder, PersonaConfig
    from pathlib import Path

    # 创建工具注册表
    tool_registry = ToolRegistry()
    if request.enable_tools:
        register_builtin_tools(tool_registry)

    # 用 ContextBuilder 构建完整系统提示词
    from app.models import Workspace
    from app.modules.agent.context_pruner import ContextPruner as CP
    from app.modules.agent.compactor import Compactor as C, CompactionConfig as CC
    from app.modules.agent.token_budget import TokenBudget
    from app.modules.agent.compression_service import ContextCompressionService
    ws_result = await db.execute(
        select(Workspace).where(Workspace.owner_id == current_user.id, Workspace.is_default == True)
    )
    workspace = ws_result.scalar_one_or_none()
    persona = PersonaConfig.from_workspace(workspace, current_user)
    ctx_builder = ContextBuilder(
        persona_config=persona,
        workspace=Path(workspace.path) if workspace and workspace.path else Path.home(),
    )
    system_prompt = ctx_builder.build_system_prompt() if request.enable_tools else None

    provider = SimpleLLMProvider(config=config)
    provider.fast_mode = request.fast_mode

    # Context 压缩组件（Phase 1: 统一入口）
    token_budget = TokenBudget(context_window=config.context_window)
    context_pruner = CP()
    from app.modules.agent.file_tracker import FileTracker
    file_tracker = FileTracker(max_files=5, max_tokens=50_000)
    compactor = C(
        config=CC(context_window=config.context_window),
        llm_client=provider,
        user_id=current_user.id,
        session_id=request.session_id,
    )
    compression_service = ContextCompressionService(
        budget=token_budget,
        compactor=compactor,
        context_pruner=context_pruner,
        file_tracker=file_tracker,
    )

    agent_loop = AgentLoop(
        provider=provider,
        tools=tool_registry,
        system_prompt=system_prompt,
        model=config.model_name,
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

    thinking_pattern = re.compile(r'<!--THINKING:(.*?)-->', re.DOTALL)

    # 设执行上下文（Runner 工具需要知道当前用户）
    from app.core.execution_context import current_user_id
    token = current_user_id.set(current_user.id)

    async def generate():
        start_time = time.time()
        content_buffer = ""
        last_good_content = ""  # 保留上一轮有效内容，防止最后迭代为空
        thinking_buffer = ""

        # 并发控制
        from app.core.concurrency import concurrency_manager
        import uuid as _uuid
        task_id = str(_uuid.uuid4())[:8]
        result = await concurrency_manager.acquire(current_user.id, task_id, len(request.message))

        if result.rejected:
            yield f"data: {json.dumps({'type': 'error', 'message': '当前排队人数过多，请稍后重试'})}\n\n"
            return

        if result.queued:
            yield f"data: {json.dumps({'type': 'queued', 'position': result.position, 'wait_ms': result.estimated_wait_ms})}\n\n"
            try:
                await asyncio.wait_for(result.wait_future, timeout=concurrency_manager.queue_timeout_seconds)
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
                context = [{"role": m.role, "content": m.content} for m in request.context]

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

                # 提取非思考内容
                clean = thinking_pattern.sub("", chunk)

                # 检测迭代边界：[思考中...] 表示新一轮迭代开始
                is_boundary = "[思考中...]" in clean
                if is_boundary:
                    if content_buffer.strip():
                        last_good_content = content_buffer  # 保存后备
                    content_buffer = ""
                    yield f"data: {json.dumps({'type': 'new_iteration'}, ensure_ascii=False)}\n\n"

                # 移除标记
                clean = clean.replace("[思考中...]", "")
                clean = re.sub(r'\[达到最大迭代次数 \d+\]', '', clean)
                if is_boundary:
                    clean = clean.strip()       # 边界残余（\n）彻底丢弃
                else:
                    clean = clean.strip(' \t\r')  # 保留 \n 用于 markdown 换行
                if clean:
                    content_buffer += clean
                    last_good_content = ""  # 本轮已有内容，后备已过期
                    yield f"data: {json.dumps({'type': 'content', 'content': clean}, ensure_ascii=False)}\n\n"

            latency_ms = int((time.time() - start_time) * 1000)

            # 发送工具调用结果
            if agent_loop.last_tool_results:
                for name, result in agent_loop.last_tool_results.items():
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': result}, ensure_ascii=False)}\n\n"

            # 记录 API 用量
            try:
                usage = ApiUsage(
                    user_id=current_user.id,
                    model=config.model_name or "unknown",
                    provider=config.provider or "unknown",
                    total_tokens=(provider.last_input_tokens or 0) + (provider.last_output_tokens or 0),
                    input_tokens=provider.last_input_tokens or 0,
                    output_tokens=provider.last_output_tokens or 0,
                    duration_ms=latency_ms,
                    is_success=True,
                )
                db.add(usage)
                await db.commit()
            except Exception:
                pass  # 用量记录失败不影响对话

            # 确定最终响应
            final_response = content_buffer.strip() or last_good_content.strip()

            # 如果 LLM 没有生成文字回复但调用了工具，基于工具结果拼一个总结
            if not final_response and agent_loop.last_tool_results:
                parts = []
                for name, result in agent_loop.last_tool_results.items():
                    if result and len(str(result)) < 500:
                        parts.append(f"{name}: {result}")
                if parts:
                    final_response = "已获取以下信息：\n" + "\n".join(parts[:5])

            # 发送完成事件
            input_tokens = provider.last_input_tokens or 0
            done_event = {
                'type': 'done',
                'thinking_content': thinking_buffer if thinking_buffer else None,
                'response': final_response or '(未获取到有效回复)',
                'latency_ms': latency_ms,
                'input_tokens': input_tokens,
                'output_tokens': provider.last_output_tokens,
                'context_usage': token_budget.to_dict(input_tokens),
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
    current_user: User = Depends(get_current_active_user)
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
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_default == True)
        )
        config = result.scalar_one_or_none()

    if not config:
        return CompactResponse(
            success=False,
            message="没有可用的 AI 模型配置",
            summary="",
        )

    from app.modules.llm import SimpleLLMProvider
    from app.modules.agent.compactor import Compactor, CompactionConfig
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.agent.token_budget import TokenBudget
    from app.modules.agent.compression_service import ContextCompressionService

    provider = SimpleLLMProvider(config=config)

    token_budget = TokenBudget(context_window=config.context_window)
    context_pruner = ContextPruner()
    compactor = Compactor(
        config=CompactionConfig(context_window=config.context_window),
        llm_client=provider,
        user_id=current_user.id,
    )
    compression_service = ContextCompressionService(
        budget=token_budget,
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
    messages: List[dict],
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


from app.modules.llm import SimpleLLMProvider
