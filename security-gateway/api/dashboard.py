"""
审计看板 API

提供安全网关看板统计数据：风险趋势、高频敏感词、用户风险排名、今日概览。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit_service import AuditService
from core.deps import get_db

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/stats")
async def get_dashboard_stats(
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    db: AsyncSession = Depends(get_db),
):
    """获取安全看板统计数据

    - risk_trend: 近 N 天按天的 block/approve/sanitize/allow 趋势
    - top_words: 高频敏感词 TOP 10
    - top_users: 用户拦截排名 TOP 10
    - summary: 今日总检测数、拦截数、严重事件数
    """
    audit_service = AuditService()
    stats = await audit_service.get_dashboard_stats(db, days=days)
    return stats
