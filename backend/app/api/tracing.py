"""
Tracing API - 追踪系统 API

提供追踪数据查询的 REST API
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User
from app.modules.agent.tracing import (
    AgentTracer,
    Span,
    Trace,
    get_tracer,
)

router = APIRouter(prefix="/tracing", tags=["追踪管理"])


# ==================== Pydantic Models ====================


class SpanResponse(BaseModel):
    """跨度响应"""

    id: str
    trace_id: str
    parent_id: str | None
    kind: str
    name: str
    start_time: float
    end_time: float | None
    duration_ms: int
    status: str
    error: str | None
    input_data: dict
    output_data: dict
    metadata: dict
    tokens: dict | None
    children: list["SpanResponse"] = []

    class Config:
        from_attributes = True


class TraceResponse(BaseModel):
    """追踪响应"""

    id: str
    name: str
    start_time: float
    end_time: float | None
    duration_ms: int
    total_tokens: int
    total_cost: float
    span_count: int
    error_count: int
    agent_id: str
    agent_name: str
    session_id: str
    user_id: int | None
    metadata: dict
    root_span: SpanResponse | None

    class Config:
        from_attributes = True


class TraceListResponse(BaseModel):
    """追踪列表响应"""

    items: list[TraceResponse]
    total: int


class TimelineItem(BaseModel):
    """时间线项"""

    id: str
    name: str
    kind: str
    start: float
    end: float | None
    duration_ms: int
    status: str
    depth: int
    start_offset_ms: int = 0


class TimelineResponse(BaseModel):
    """时间线响应"""

    trace_id: str
    trace_name: str
    total_duration_ms: int
    items: list[TimelineItem]


class TraceStatsResponse(BaseModel):
    """追踪统计响应"""

    total_traces: int
    total_spans: int
    total_tokens: int
    total_errors: int
    avg_duration_ms: float
    by_kind: dict
    by_agent: dict


# ==================== API Endpoints ====================


@router.get("/", response_model=TraceListResponse)
async def list_traces(
    agent_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """获取追踪列表"""
    # 非管理员只能查看自己的追踪
    if not current_user.is_super_admin and not current_user.is_org_admin:
        user_id = current_user.id

    traces = tracer.list_traces(
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
    )

    return TraceListResponse(
        items=[_trace_to_response(t) for t in traces],
        total=len(traces),
    )


@router.get("/stats", response_model=TraceStatsResponse)
async def get_trace_stats(
    agent_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """获取追踪统计"""
    # 非管理员只能查看自己的统计
    if not current_user.is_super_admin and not current_user.is_org_admin:
        user_id = current_user.id

    traces = tracer.list_traces(
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        limit=1000,
    )

    if not traces:
        return TraceStatsResponse(
            total_traces=0,
            total_spans=0,
            total_tokens=0,
            total_errors=0,
            avg_duration_ms=0,
            by_kind={},
            by_agent={},
        )

    total_spans = sum(t.span_count for t in traces)
    total_tokens = sum(t.total_tokens for t in traces)
    total_errors = sum(t.error_count for t in traces)
    avg_duration = sum(t.duration_ms for t in traces) / len(traces)

    # 按类型统计
    by_kind = {}
    by_agent = {}

    for trace in traces:
        # Agent 统计
        if trace.agent_name:
            by_agent[trace.agent_name] = by_agent.get(trace.agent_name, 0) + 1

        # Span 类型统计
        for span in trace.flatten_spans():
            kind = span.kind.value
            by_kind[kind] = by_kind.get(kind, 0) + 1

    return TraceStatsResponse(
        total_traces=len(traces),
        total_spans=total_spans,
        total_tokens=total_tokens,
        total_errors=total_errors,
        avg_duration_ms=avg_duration,
        by_kind=by_kind,
        by_agent=by_agent,
    )


@router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: str,
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """获取追踪详情"""
    trace = tracer.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # 权限检查
    if trace.user_id and trace.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    return _trace_to_response(trace)


@router.get("/{trace_id}/timeline", response_model=TimelineResponse)
async def get_trace_timeline(
    trace_id: str,
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """获取追踪时间线（用于 Gantt 图）"""
    trace = tracer.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # 权限检查
    if trace.user_id and trace.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    timeline = tracer.get_timeline(trace_id)

    return TimelineResponse(
        trace_id=trace.id,
        trace_name=trace.name,
        total_duration_ms=trace.duration_ms,
        items=[TimelineItem(**item) for item in timeline],
    )


@router.get("/{trace_id}/spans", response_model=list[SpanResponse])
async def get_trace_spans(
    trace_id: str,
    kind: str | None = None,
    status: str | None = None,
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """获取追踪的所有跨度（扁平列表）"""
    trace = tracer.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # 权限检查
    if trace.user_id and trace.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    spans = trace.flatten_spans()

    # 过滤
    if kind:
        spans = [s for s in spans if s.kind.value == kind]
    if status:
        spans = [s for s in spans if s.status.value == status]

    return [_span_to_response(s) for s in spans]


@router.delete("/{trace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trace(
    trace_id: str,
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """删除追踪"""
    trace = tracer.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # 权限检查：只有管理员或所有者可删除
    if trace.user_id and trace.user_id != current_user.id:
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    # 从内存中删除
    if trace_id in tracer._traces:
        del tracer._traces[trace_id]


@router.delete("/", status_code=status.HTTP_204_NO_CONTENT)
async def clear_traces(
    keep_recent: int = Query(100, ge=0, le=1000),
    tracer: AgentTracer = Depends(get_tracer),
    current_user: User = Depends(get_current_active_user),
):
    """清除历史追踪（只保留最近的）"""
    # 只有管理员可以清除
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only admins can clear traces")

    tracer.clear_traces(keep_recent=keep_recent)


# ==================== Helper Functions ====================


def _span_to_response(span: Span) -> SpanResponse:
    """转换跨度到响应"""
    return SpanResponse(
        id=span.id,
        trace_id=span.trace_id,
        parent_id=span.parent_id,
        kind=span.kind.value,
        name=span.name,
        start_time=span.start_time,
        end_time=span.end_time,
        duration_ms=span.duration_ms,
        status=span.status.value,
        error=span.error,
        input_data=span.input_data,
        output_data=span.output_data,
        metadata=span.metadata,
        tokens={
            "prompt": span.tokens.prompt_tokens,
            "completion": span.tokens.completion_tokens,
            "total": span.tokens.total_tokens,
        }
        if span.tokens
        else None,
        children=[_span_to_response(c) for c in span.children],
    )


def _trace_to_response(trace: Trace) -> TraceResponse:
    """转换追踪到响应"""
    return TraceResponse(
        id=trace.id,
        name=trace.name,
        start_time=trace.start_time,
        end_time=trace.end_time,
        duration_ms=trace.duration_ms,
        total_tokens=trace.total_tokens,
        total_cost=trace.total_cost,
        span_count=trace.span_count,
        error_count=trace.error_count,
        agent_id=trace.agent_id,
        agent_name=trace.agent_name,
        session_id=trace.session_id,
        user_id=trace.user_id,
        metadata=trace.metadata,
        root_span=_span_to_response(trace.root_span) if trace.root_span else None,
    )
