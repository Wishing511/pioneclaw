"""
Interrupt API - 中断与恢复 API

提供中断管理和检查点操作的 REST API
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_active_user
from app.models import User
from app.modules.agent.interrupt import (
    Checkpoint,
    InterruptManager,
    InterruptOption,
    InterruptPoint,
    InterruptReason,
    get_interrupt_manager,
)

router = APIRouter(prefix="/interrupt", tags=["中断管理"])


# ==================== Pydantic Models ====================


class InterruptOptionSchema(BaseModel):
    """中断选项"""

    label: str
    value: str
    description: str = ""
    requires_input: bool = False
    input_placeholder: str = ""
    style: str = "default"


class CreateInterruptRequest(BaseModel):
    """创建中断请求"""

    reason: str = Field(..., description="中断原因")
    message: str = Field(..., description="中断消息")
    title: str = Field("", description="标题")
    agent_id: str = Field("", description="Agent ID")
    agent_name: str = Field("", description="Agent 名称")
    conversation_id: str = Field("", description="对话 ID")
    session_id: str = Field("", description="会话 ID")
    details: dict = Field(default_factory=dict, description="详细信息")
    options: list[InterruptOptionSchema] = Field(
        default_factory=list, description="可选项"
    )
    ttl: float | None = Field(None, description="过期时间（秒）")


class ResolveInterruptRequest(BaseModel):
    """解决中断请求"""

    resolution: str = Field(..., description="解决方式：approve/reject/modify/cancel")
    resolution_note: str = Field("", description="解决备注")
    modified_state: dict = Field(default_factory=dict, description="修改后的状态")


class InterruptResponse(BaseModel):
    """中断响应"""

    id: str
    reason: str
    status: str
    agent_id: str
    agent_name: str
    conversation_id: str
    session_id: str
    title: str
    message: str
    details: dict
    options: list[dict]
    created_at: float
    expires_at: float | None
    resolved_at: float | None
    resolution: str | None
    resolved_by: int | None
    resolution_note: str

    class Config:
        from_attributes = True


class InterruptListResponse(BaseModel):
    """中断列表响应"""

    items: list[InterruptResponse]
    total: int


class CheckpointResponse(BaseModel):
    """检查点响应"""

    id: str
    agent_id: str
    name: str
    state: dict
    messages: list[dict]
    iteration: int
    tool_calls: list[dict]
    created_at: float
    metadata: dict

    class Config:
        from_attributes = True


class CheckpointListResponse(BaseModel):
    """检查点列表响应"""

    items: list[CheckpointResponse]
    total: int


class SaveCheckpointRequest(BaseModel):
    """保存检查点请求"""

    name: str = Field("", description="检查点名称")
    agent_id: str = Field(..., description="Agent ID")
    state: dict = Field(default_factory=dict, description="状态快照")
    messages: list[dict] = Field(default_factory=list, description="消息列表")
    iteration: int = Field(0, description="当前迭代次数")
    tool_calls: list[dict] = Field(default_factory=list, description="工具调用列表")
    metadata: dict = Field(default_factory=dict, description="元数据")


class RestoreCheckpointResponse(BaseModel):
    """恢复检查点响应"""

    state: dict
    messages: list[dict]
    iteration: int
    tool_calls: list[dict]


# ==================== API Endpoints ====================


@router.post("/", response_model=InterruptResponse, status_code=status.HTTP_201_CREATED)
async def create_interrupt(
    data: CreateInterruptRequest,
    current_user: User = Depends(get_current_active_user),
    manager: InterruptManager = Depends(get_interrupt_manager),
):
    """创建中断点"""
    try:
        reason = InterruptReason(data.reason)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid reason: {data.reason}")

    options = (
        [
            InterruptOption(
                label=o.label,
                value=o.value,
                description=o.description,
                requires_input=o.requires_input,
                input_placeholder=o.input_placeholder,
                style=o.style,
            )
            for o in data.options
        ]
        if data.options
        else None
    )

    interrupt = await manager.create_interrupt(
        reason=reason,
        message=data.message,
        title=data.title,
        agent_id=data.agent_id,
        agent_name=data.agent_name,
        conversation_id=data.conversation_id,
        session_id=data.session_id,
        user_id=current_user.id,
        details=data.details,
        options=options,
        ttl=data.ttl,
    )

    return _interrupt_to_response(interrupt)


@router.get("/", response_model=InterruptListResponse)
async def list_interrupts(
    conversation_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    user_id: int | None = None,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """获取待处理中断列表"""
    # 如果没有指定 user_id，使用当前用户
    if not user_id and not current_user.is_super_admin:
        user_id = current_user.id

    interrupts = await manager.get_pending_interrupts(
        conversation_id=conversation_id,
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
    )

    return InterruptListResponse(
        items=[_interrupt_to_response(i) for i in interrupts],
        total=len(interrupts),
    )


@router.get("/{interrupt_id}", response_model=InterruptResponse)
async def get_interrupt(
    interrupt_id: str,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """获取中断详情"""
    interrupt = await manager.get_interrupt(interrupt_id)
    if not interrupt:
        raise HTTPException(status_code=404, detail="Interrupt not found")

    # 权限检查：只有中断所属用户或管理员可以查看
    if interrupt.user_id and interrupt.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    return _interrupt_to_response(interrupt)


@router.post("/{interrupt_id}/resolve", response_model=InterruptResponse)
async def resolve_interrupt(
    interrupt_id: str,
    data: ResolveInterruptRequest,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """解决中断"""
    # 先获取中断检查权限
    interrupt = await manager.get_interrupt(interrupt_id)
    if not interrupt:
        raise HTTPException(status_code=404, detail="Interrupt not found")

    # 权限检查
    if interrupt.user_id and interrupt.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    try:
        resolved = await manager.resolve_interrupt(
            interrupt_id=interrupt_id,
            resolution=data.resolution,
            resolved_by=current_user.id,
            resolution_note=data.resolution_note,
            modified_state=data.modified_state,
        )
        return _interrupt_to_response(resolved)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{interrupt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_interrupt(
    interrupt_id: str,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """取消中断"""
    interrupt = await manager.get_interrupt(interrupt_id)
    if not interrupt:
        raise HTTPException(status_code=404, detail="Interrupt not found")

    # 权限检查
    if interrupt.user_id and interrupt.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    await manager.cancel_interrupt(interrupt_id)


# ==================== Checkpoint Endpoints ====================


@router.post(
    "/checkpoints",
    response_model=CheckpointResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_checkpoint(
    data: SaveCheckpointRequest,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """保存检查点"""
    checkpoint = await manager.save_checkpoint(
        agent_id=data.agent_id,
        name=data.name,
        state=data.state,
        messages=data.messages,
        iteration=data.iteration,
        tool_calls=data.tool_calls,
        metadata=data.metadata,
    )
    return _checkpoint_to_response(checkpoint)


@router.get("/checkpoints/{agent_id}", response_model=CheckpointListResponse)
async def list_checkpoints(
    agent_id: str,
    limit: int = 10,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """列出检查点"""
    checkpoints = await manager.list_checkpoints(agent_id=agent_id, limit=limit)
    return CheckpointListResponse(
        items=[_checkpoint_to_response(c) for c in checkpoints],
        total=len(checkpoints),
    )


@router.get(
    "/checkpoints/{agent_id}/{checkpoint_id}", response_model=CheckpointResponse
)
async def get_checkpoint(
    agent_id: str,
    checkpoint_id: str,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """获取检查点详情"""
    checkpoint = await manager.load_checkpoint(checkpoint_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return _checkpoint_to_response(checkpoint)


@router.post(
    "/checkpoints/{checkpoint_id}/restore", response_model=RestoreCheckpointResponse
)
async def restore_checkpoint(
    checkpoint_id: str,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """恢复到检查点"""
    try:
        state = await manager.restore_checkpoint(checkpoint_id)
        return RestoreCheckpointResponse(**state)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/checkpoints/{checkpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_checkpoint(
    checkpoint_id: str,
    manager: InterruptManager = Depends(get_interrupt_manager),
    current_user: User = Depends(get_current_active_user),
):
    """删除检查点"""
    await manager.delete_checkpoint(checkpoint_id)


# ==================== Helper Functions ====================


def _interrupt_to_response(interrupt: InterruptPoint) -> InterruptResponse:
    """转换中断点到响应"""
    return InterruptResponse(
        id=interrupt.id,
        reason=interrupt.reason.value,
        status=interrupt.status.value,
        agent_id=interrupt.agent_id,
        agent_name=interrupt.agent_name,
        conversation_id=interrupt.conversation_id,
        session_id=interrupt.session_id,
        title=interrupt.title,
        message=interrupt.message,
        details=interrupt.details,
        options=[
            {
                "label": o.label,
                "value": o.value,
                "description": o.description,
                "requires_input": o.requires_input,
                "input_placeholder": o.input_placeholder,
                "style": o.style,
            }
            for o in interrupt.options
        ],
        created_at=interrupt.created_at,
        expires_at=interrupt.expires_at,
        resolved_at=interrupt.resolved_at,
        resolution=interrupt.resolution,
        resolved_by=interrupt.resolved_by,
        resolution_note=interrupt.resolution_note,
    )


def _checkpoint_to_response(checkpoint: Checkpoint) -> CheckpointResponse:
    """转换检查点到响应"""
    return CheckpointResponse(
        id=checkpoint.id,
        agent_id=checkpoint.agent_id,
        name=checkpoint.name,
        state=checkpoint.state,
        messages=checkpoint.messages,
        iteration=checkpoint.iteration,
        tool_calls=checkpoint.tool_calls,
        created_at=checkpoint.created_at,
        metadata=checkpoint.metadata,
    )
