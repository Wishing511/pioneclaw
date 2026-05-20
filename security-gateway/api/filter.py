"""
过滤检测 API

提供 pre_input_call, post_llm_call, pre_tool_call 的检测端点。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from schemas.security import (
    FilterInputRequest,
    FilterInputResponse,
    CheckToolRequest,
)
from services.filter_service import FilterService
from services.audit_service import AuditService
from services.alert_service import AlertService
from core.deps import get_db
from config import settings

router = APIRouter(tags=["filter"])


def _should_alert(result: dict) -> bool:
    """判断是否触发告警：BLOCK + critical"""
    return result.get("action") == "block" and result.get("risk_level") == "critical"


@router.post("/filter/input", response_model=FilterInputResponse)
async def filter_input(
    req: FilterInputRequest,
    db: AsyncSession = Depends(get_db),
):
    """pre_input_call: 用户输入安全过滤

    检测敏感词、个人隐私数据、Prompt 注入等风险。
    """
    try:
        service = FilterService()
        result = await service.filter_input(req.text)

        # 写入审计日志
        audit_ctx = req.context or {}
        audit_ctx["text"] = req.text[:500]
        audit = AuditService()
        await audit.log(db, "filter_input", result, audit_ctx)

        # 高危拦截告警
        if _should_alert(result):
            alert_service = AlertService()
            await alert_service.send_alert("filter_input", result, audit_ctx)

        return FilterInputResponse(**result)
    except Exception as e:
        if settings.FAIL_OPEN:
            return FilterInputResponse(
                action="allow",
                reason=f"检测异常(降级放行): {str(e)}",
                risk_level="low",
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/filter/output", response_model=FilterInputResponse)
async def filter_output(
    req: FilterInputRequest,
    db: AsyncSession = Depends(get_db),
):
    """post_llm_call: 模型输出安全过滤

    检测模型输出中的敏感数据泄露。
    """
    try:
        service = FilterService()
        result = await service.filter_output(req.text)

        audit_ctx = req.context or {}
        audit_ctx["text"] = req.text[:500]
        audit = AuditService()
        await audit.log(db, "filter_output", result, audit_ctx)

        # 高危拦截告警
        if _should_alert(result):
            alert_service = AlertService()
            await alert_service.send_alert("filter_output", result, audit_ctx)

        return FilterInputResponse(**result)
    except Exception as e:
        if settings.FAIL_OPEN:
            return FilterInputResponse(
                action="allow",
                reason=f"检测异常(降级放行): {str(e)}",
                risk_level="low",
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check/tool", response_model=FilterInputResponse)
async def check_tool(
    req: CheckToolRequest,
    db: AsyncSession = Depends(get_db),
):
    """pre_tool_call: 工具调用安全校验

    检查工具名称和参数的安全性。
    """
    try:
        service = FilterService()
        result = await service.check_tool(req.tool_name, req.arguments)

        audit_ctx = req.context or {}
        # 对工具参数做截断，避免大文件内容导致审计日志过大
        args_preview = str(req.arguments)[:1000] if req.arguments else ""
        audit_ctx["extra_data"] = {
            "tool_name": req.tool_name,
            "arguments_preview": args_preview,
        }
        audit = AuditService()
        await audit.log(db, "check_tool", result, audit_ctx)

        # 高危拦截告警
        if _should_alert(result):
            alert_service = AlertService()
            await alert_service.send_alert("check_tool", result, audit_ctx)

        return FilterInputResponse(**result)
    except Exception as e:
        if settings.FAIL_OPEN:
            return FilterInputResponse(
                action="allow",
                reason=f"检测异常(降级放行): {str(e)}",
                risk_level="low",
            )
        raise HTTPException(status_code=500, detail=str(e))
