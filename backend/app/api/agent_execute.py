"""
Agent 执行 API - 调用 AgentLoop 执行 Agent

提供：
- 执行 Agent
- 流式响应
- 执行历史记录
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import json
import time
from datetime import datetime, timezone

from app.core import get_db
from app.models import Agent, AIModelConfig, User, AgentExecution, Workspace
from app.api.auth import get_current_active_user
from app.modules.agent import AgentLoop, CancelToken
from app.modules.agent.context import PersonaConfig
from app.modules.tools import ToolRegistry, register_builtin_tools

router = APIRouter(prefix="/agents", tags=["Agent 执行"])


def _create_vv_post_turn_services(
    llm_provider,
    model_name: str,
    user_id: int,
    session_id: str,
    agent_id: int,
    workspace_path: str = "",
) -> list:
    """
    创建 Stage VV post-turn 服务列表，用于注入 AgentLoop

    失败时静默降级，不阻塞 Agent 执行。
    """
    services = []
    memory_store = None
    try:
        from app.modules.agent.memory import get_memory_store
        from pathlib import Path
        ws_path = Path(workspace_path) / "memory" if workspace_path else None
        memory_store = get_memory_store(ws_path)
    except Exception:
        pass

    try:
        from app.modules.agent.memory_extractor import MemoryExtractor

        extractor = MemoryExtractor(
            llm_provider=llm_provider,
            model=model_name,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            memory_store=memory_store,
        )
        services.append(("memory_extractor", extractor))
    except Exception:
        pass

    try:
        from app.modules.agent.conversation_summarizer import ConversationSummarizer

        summarizer = ConversationSummarizer(
            llm_provider=llm_provider,
            memory_store=memory_store,
            model=model_name,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )
        services.append(("conversation_summarizer", summarizer))
    except Exception:
        pass

    return services


async def _build_user_aware_system_prompt(
    agent: Agent, user: User, db: AsyncSession, base_prompt: Optional[str] = None
) -> str:
    """
    构建包含用户信息的系统提示词

    如果用户有默认 Workspace，从 workspace.settings 读取人设配置；
    否则使用 user.display_name 作为 fallback。
    """
    persona = None

    # 尝试从 Workspace 加载
    if user.default_workspace_id:
        result = await db.execute(
            select(Workspace).where(Workspace.id == user.default_workspace_id)
        )
        workspace = result.scalar_one_or_none()
        if workspace:
            persona = PersonaConfig.from_workspace(workspace, user)

    # Fallback: 使用用户显示名
    if persona is None:
        persona = PersonaConfig(user_name=user.display_name or user.username)

    # 构建用户信息注入
    user_info_lines = [f"用户称呼: {persona.user_name}"]
    if persona.user_email:
        user_info_lines.append(f"用户邮箱: {persona.user_email}")
    if persona.organization_name:
        user_info_lines.append(f"所属组织: {persona.organization_name}")
    if persona.workspace_name:
        user_info_lines.append(f"工作空间: {persona.workspace_name}")

    user_info = "\n".join(user_info_lines)

    # 注入到系统提示词
    prompt = base_prompt or agent.system_prompt or ""
    if prompt and "用户称呼" not in prompt:
        prompt = f"{prompt}\n\n## 用户信息\n{user_info}"
    elif not prompt:
        prompt = f"## 用户信息\n{user_info}"

    return prompt


class AgentExecuteRequest(BaseModel):
    """Agent 执行请求"""
    message: str
    context: Optional[List[dict]] = None
    stream: bool = True


class AgentExecuteResponse(BaseModel):
    """Agent 执行响应"""
    success: bool
    message: str
    response: Optional[str] = None
    agent_id: Optional[int] = None
    latency_ms: Optional[int] = None
    approval_id: Optional[int] = None  # 安全网关审批ID
    pending_approval: bool = False  # 是否等待审批


# 存储活跃的取消令牌
_active_cancellations: dict[int, CancelToken] = {}


@router.post("/{agent_id}/execute", response_model=AgentExecuteResponse)
async def execute_agent(
    agent_id: int,
    request: AgentExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    执行 Agent（非流式）
    
    Args:
        agent_id: Agent ID
        request: 执行请求
    
    Returns:
        AgentExecuteResponse: 执行结果
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Executing agent {agent_id} with message: {request.message[:50]}")
    
    try:
        # 获取 Agent
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            logger.error(f"Agent {agent_id} not found")
            raise HTTPException(status_code=404, detail="Agent 不存在")
        
        logger.info(f"Agent found: {agent.name}")
        
        # 获取模型配置
        model_config = None
        if agent.model:
            # agent.model 可能是模型名称或 ID
            try:
                model_id = int(agent.model)
                result = await db.execute(
                    select(AIModelConfig).where(AIModelConfig.id == model_id)
                )
                model_config = result.scalar_one_or_none()
            except (ValueError, TypeError):
                # 如果不是 ID，尝试按名称匹配
                result = await db.execute(
                    select(AIModelConfig).where(AIModelConfig.model_name == agent.model)
                )
                model_config = result.scalar_one_or_none()
        
        if not model_config:
            # 使用默认配置
            logger.info("No agent model config, using default")
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_default == True)
            )
            model_config = result.scalar_one_or_none()
        
        if not model_config:
            logger.error("No model config available")
            return AgentExecuteResponse(
                success=False,
                message="没有可用的 AI 模型配置"
            )
        
        logger.info(f"Using model: {model_config.model_name}")
        
        # 创建执行记录
        execution = AgentExecution(
            agent_id=agent_id,
            user_id=current_user.id,
            message=request.message,
            system_prompt=agent.system_prompt,
            status="running",
            model_name=model_config.model_name,
            model_config_id=model_config.id,
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)
        logger.info(f"Created execution record: {execution.id}")
        
        # 创建工具注册表
        tool_registry = ToolRegistry()
        register_builtin_tools(tool_registry)
        
        # 创建 LLM Provider（简化版，直接使用配置）
        provider = SimpleLLMProvider(config=model_config)

        # Context 压缩组件（Phase 1: 统一入口）
        from app.modules.agent.context_pruner import ContextPruner
        from app.modules.agent.compactor import Compactor, CompactionConfig
        from app.modules.agent.token_budget import TokenBudget
        from app.modules.agent.compression_service import ContextCompressionService
        context_pruner = ContextPruner()
        compactor = Compactor(
            config=CompactionConfig(context_window=model_config.context_window),
            llm_client=provider,
            user_id=current_user.id,
            session_id=str(execution.id),
            agent_id=agent.id,
        )
        token_budget = TokenBudget(context_window=model_config.context_window)
        from app.modules.agent.file_tracker import FileTracker
        file_tracker = FileTracker(max_files=5, max_tokens=50_000)
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
                "agent_id": str(agent.id),
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
                    resource_id=str(agent_id),
                    target_scope="org",
                    target_org_id=current_user.organization_id,
                    extra_data={
                        "risk_level": error.get("risk_level"),
                        "agent_id": agent_id,
                        "content_preview": request.message[:200],
                    },
                )
                db.add(approval)
                await db.commit()
                await db.refresh(approval)
                return AgentExecuteResponse(
                    success=False,
                    message=error["message"],
                    agent_id=agent_id,
                    latency_ms=error["latency_ms"],
                    approval_id=approval.id,
                    pending_approval=True,
                )
            return AgentExecuteResponse(
                success=error["success"],
                message=error["message"],
                agent_id=agent_id,
                latency_ms=error["latency_ms"],
            )
        if filtered_text != request.message:
            request.message = filtered_text

        # 创建 AgentLoop
        agent_loop = AgentLoop(
            provider=provider,
            tools=tool_registry,
            model=model_config.model_name,
            max_iterations=agent.max_turns or 25,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
            file_tracker=file_tracker,
            agent_config=agent.config or {},
            user_role=current_user.role,
            context_pruner=context_pruner,
            compactor=compactor,
            compression_service=compression_service,
            security_client=security_client,
        )

        # Stage VV: 注入 post-turn 服务
        vv_services = _create_vv_post_turn_services(
            llm_provider=provider,
            model_name=model_config.model_name,
            user_id=current_user.id,
            session_id=str(execution.id),
            agent_id=agent.id,
            workspace_path=getattr(agent, 'workspace_path', '') or '',
        )
        if vv_services:
            agent_loop.configure_post_turn(vv_services)

        # 执行
        start_time = time.time()
        try:
            # 构建包含用户信息的系统提示词
            system_prompt = await _build_user_aware_system_prompt(
                agent, current_user, db
            )

            logger.info("Starting agent loop execution")
            response_text = await agent_loop.process_direct(
                message=request.message,
                context=request.context,
                system_prompt=system_prompt,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            logger.info(f"Agent loop completed in {latency_ms}ms")
            
            # 更新执行记录
            execution.status = "completed"
            execution.response = response_text
            execution.latency_ms = latency_ms
            execution.completed_at = datetime.now(tz=timezone.utc)
            await db.commit()
            
            return AgentExecuteResponse(
                success=True,
                message="执行成功",
                response=response_text,
                agent_id=agent_id,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            
            # 更新执行记录为失败
            execution.status = "failed"
            execution.error_message = str(e)
            execution.completed_at = datetime.now(tz=timezone.utc)
            await db.commit()
            
            return AgentExecuteResponse(
                success=False,
                message=f"执行失败: {str(e)}",
                agent_id=agent_id,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return AgentExecuteResponse(
            success=False,
            message=f"内部错误: {str(e)}",
            agent_id=agent_id,
        )


@router.post("/{agent_id}/execute/stream")
async def execute_agent_stream(
    agent_id: int,
    request: AgentExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    执行 Agent（流式响应）
    
    使用 Server-Sent Events (SSE) 返回流式响应
    """
    # 获取 Agent
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    # 获取模型配置
    model_config = None
    if agent.model:
        try:
            model_id = int(agent.model)
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.id == model_id)
            )
            model_config = result.scalar_one_or_none()
        except (ValueError, TypeError):
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.model_name == agent.model)
            )
            model_config = result.scalar_one_or_none()
    
    if not model_config:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_default == True)
        )
        model_config = result.scalar_one_or_none()
    
    if not model_config:
        async def error_stream():
            yield f"data: {json.dumps({'error': '没有可用的 AI 模型配置'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    # 创建工具注册表
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    
    # 创建 LLM Provider
    provider = SimpleLLMProvider(config=model_config)

    # Context 压缩组件（Phase 1: 统一入口）
    from app.modules.agent.context_pruner import ContextPruner
    from app.modules.agent.compactor import Compactor, CompactionConfig
    from app.modules.agent.token_budget import TokenBudget
    from app.modules.agent.compression_service import ContextCompressionService
    context_pruner = ContextPruner()
    compactor = Compactor(
        config=CompactionConfig(context_window=model_config.context_window),
        llm_client=provider,
        user_id=current_user.id,
        session_id=f"stream_{agent_id}_{int(time.time())}",
        agent_id=agent.id,
    )
    token_budget = TokenBudget(context_window=model_config.context_window)
    from app.modules.agent.file_tracker import FileTracker
    file_tracker = FileTracker(max_files=5, max_tokens=50_000)
    compression_service = ContextCompressionService(
        budget=token_budget,
        compactor=compactor,
        context_pruner=context_pruner,
        file_tracker=file_tracker,
    )

    # 创建取消令牌
    cancel_token = CancelToken()
    _active_cancellations[agent_id] = cancel_token

    # 创建 AgentLoop
    agent_loop = AgentLoop(
        provider=provider,
        tools=tool_registry,
        file_tracker=file_tracker,
        model=model_config.model_name,
        max_iterations=agent.max_iterations or 25,
        temperature=model_config.temperature,
        max_tokens=model_config.max_tokens,
        context_pruner=context_pruner,
        compactor=compactor,
        compression_service=compression_service,
    )

    # Stage VV: 注入 post-turn 服务
    vv_services = _create_vv_post_turn_services(
        llm_provider=provider,
        model_name=model_config.model_name,
        user_id=current_user.id,
        session_id=f"stream_{agent_id}_{int(time.time())}",
        agent_id=agent.id,
        workspace_path=getattr(agent, 'workspace_path', '') or '',
    )
    if vv_services:
        agent_loop.configure_post_turn(vv_services)

    async def generate():
        try:
            # 构建包含用户信息的系统提示词
            system_prompt = await _build_user_aware_system_prompt(
                agent, current_user, db
            )

            async for chunk in agent_loop.process_message(
                message=request.message,
                context=request.context,
                system_prompt=system_prompt,
                cancel_token=cancel_token,
            ):
                yield f"data: {json.dumps({'content': chunk})}\n\n"
            
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            _active_cancellations.pop(agent_id, None)
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/{agent_id}/cancel")
async def cancel_agent_execution(
    agent_id: int,
    current_user: User = Depends(get_current_active_user)
):
    """取消 Agent 执行"""
    if agent_id in _active_cancellations:
        _active_cancellations[agent_id].cancel()
        return {"success": True, "message": "已发送取消信号"}
    return {"success": False, "message": "没有正在执行的任务"}


from app.modules.llm import SimpleLLMProvider


# ==================== 执行历史 API ====================

@router.get("/{agent_id}/executions")
async def list_agent_executions(
    agent_id: int,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取 Agent 执行历史"""
    from sqlalchemy import desc
    
    result = await db.execute(
        select(AgentExecution)
        .where(AgentExecution.agent_id == agent_id)
        .order_by(desc(AgentExecution.created_at))
        .limit(limit)
        .offset(offset)
    )
    executions = result.scalars().all()
    
    # 获取总数
    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count(AgentExecution.id))
        .where(AgentExecution.agent_id == agent_id)
    )
    total = count_result.scalar()
    
    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "message": e.message[:100] + "..." if len(e.message) > 100 else e.message,
                "response": e.response[:200] + "..." if e.response and len(e.response) > 200 else e.response,
                "status": e.status,
                "latency_ms": e.latency_ms,
                "total_tokens": e.total_tokens,
                "model_name": e.model_name,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in executions
        ]
    }


@router.get("/executions/{execution_id}")
async def get_execution_detail(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取执行详情"""
    result = await db.execute(
        select(AgentExecution).where(AgentExecution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    
    return {
        "id": execution.id,
        "agent_id": execution.agent_id,
        "user_id": execution.user_id,
        "message": execution.message,
        "response": execution.response,
        "system_prompt": execution.system_prompt,
        "status": execution.status,
        "total_iterations": execution.total_iterations,
        "total_tool_calls": execution.total_tool_calls,
        "total_tokens": execution.total_tokens,
        "input_tokens": execution.input_tokens,
        "output_tokens": execution.output_tokens,
        "latency_ms": execution.latency_ms,
        "tool_calls": execution.tool_calls,
        "error_message": execution.error_message,
        "model_name": execution.model_name,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
    }
