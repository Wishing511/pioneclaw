"""
审计日志服务

负责安全审计日志的写入和查询。
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from models.security import SecurityAuditLog
from config import settings


class AuditService:
    """审计服务"""

    async def log(
        self,
        session: AsyncSession,
        check_point: str,
        result: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SecurityAuditLog:
        """写入审计日志

        Args:
            session: 数据库会话
            check_point: 检查点 (input/output/tool)
            result: 安全检测结果 {action, reason, risk_level, matched_rules}
            context: 上下文信息 {user_id, username, session_id, agent_id, request_trace_id}
        """
        context = context or {}

        # 内容摘要：取前 200 字符
        content_preview = context.get("content_preview", "")
        if not content_preview:
            text = context.get("text", "")
            content_preview = text[:200] if text else ""

        log = SecurityAuditLog(
            check_point=check_point,
            event_type=self._infer_event_type(result.get("matched_rules", [])),
            risk_level=result.get("risk_level", "low"),
            user_id=context.get("user_id"),
            username=context.get("username"),
            session_id=context.get("session_id"),
            agent_id=context.get("agent_id"),
            content_preview=content_preview,
            action=result.get("action", "allow"),
            reason=result.get("reason"),
            matched_rules=result.get("matched_rules"),
            request_trace_id=context.get("request_trace_id"),
            extra_data=context.get("extra_data"),
        )

        session.add(log)
        # TODO: 高并发场景下，同步 commit 可能成为瓶颈。
        # 后续优化：使用后台批量写入（如 asyncio.Queue + 定时刷盘），
        # 或使用 asyncio.create_task 异步写入（牺牲一定一致性换取吞吐量）。
        await session.commit()
        await session.refresh(log)
        return log

    async def list_logs(
        self,
        session: AsyncSession,
        check_point: Optional[str] = None,
        risk_level: Optional[str] = None,
        user_id: Optional[int] = None,
        event_type: Optional[str] = None,
        action: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[List[SecurityAuditLog], int]:
        """分页查询审计日志"""
        query = select(SecurityAuditLog)
        count_query = select(func.count()).select_from(SecurityAuditLog)

        conditions = []
        if check_point:
            conditions.append(SecurityAuditLog.check_point == check_point)
        if risk_level:
            conditions.append(SecurityAuditLog.risk_level == risk_level)
        if user_id:
            conditions.append(SecurityAuditLog.user_id == user_id)
        if event_type:
            conditions.append(SecurityAuditLog.event_type == event_type)
        if action:
            conditions.append(SecurityAuditLog.action == action)
        if start_time:
            conditions.append(SecurityAuditLog.created_at >= start_time)
        if end_time:
            conditions.append(SecurityAuditLog.created_at <= end_time)

        if conditions:
            condition = and_(*conditions)
            query = query.where(condition)
            count_query = count_query.where(condition)

        # 清理过期日志
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.LOG_RETENTION_DAYS
        )
        query = query.where(SecurityAuditLog.created_at >= cutoff)
        count_query = count_query.where(SecurityAuditLog.created_at >= cutoff)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        query = (
            query.order_by(SecurityAuditLog.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await session.execute(query)
        logs = result.scalars().all()
        return list(logs), total

    async def get_dashboard_stats(
        self,
        session: AsyncSession,
        days: int = 7,
    ) -> Dict[str, Any]:
        """获取看板统计数据

        Args:
            session: 数据库会话
            days: 统计天数（默认近7天）

        Returns:
            {
                "risk_trend": [...],
                "top_words": [...],
                "top_users": [...],
                "summary": {...}
            }
        """
        from sqlalchemy import func, select, desc, cast, String
        from models.security import SecurityAuditLog

        # 时间范围
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        today_start = end_time.replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. 风险趋势（近 N 天按天聚合）
        trend_query = (
            select(
                func.date(SecurityAuditLog.created_at).label("date"),
                func.sum(func.cast(SecurityAuditLog.action == "block", Integer)).label("block"),
                func.sum(func.cast(SecurityAuditLog.action == "approve", Integer)).label("approve"),
                func.sum(func.cast(SecurityAuditLog.action == "sanitize", Integer)).label("sanitize"),
                func.sum(func.cast(SecurityAuditLog.action == "allow", Integer)).label("allow"),
            )
            .where(SecurityAuditLog.created_at >= start_time)
            .group_by(func.date(SecurityAuditLog.created_at))
            .order_by("date")
        )
        trend_result = await session.execute(trend_query)
        risk_trend = [
            {
                "date": str(row.date),
                "block": int(row.block or 0),
                "approve": int(row.approve or 0),
                "sanitize": int(row.sanitize or 0),
                "allow": int(row.allow or 0),
            }
            for row in trend_result.all()
        ]

        # 2. 高频敏感词 TOP 10
        # 从 matched_rules JSON 中提取词信息
        # 由于 JSON 查询在 SQLite/PostgreSQL 语法不同，这里用 Python 聚合
        word_query = (
            select(SecurityAuditLog.matched_rules)
            .where(
                SecurityAuditLog.created_at >= start_time,
                SecurityAuditLog.matched_rules.isnot(None),
            )
        )
        word_result = await session.execute(word_query)
        word_counts: Dict[str, int] = {}
        for row in word_result.all():
            rules = row.matched_rules
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                word = rule.get("word") or rule.get("type") or "unknown"
                word_counts[word] = word_counts.get(word, 0) + 1

        top_words = sorted(
            [{"word": k, "count": v} for k, v in word_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        # 3. 用户风险排名 TOP 10
        user_query = (
            select(
                SecurityAuditLog.username,
                func.count().label("block_count"),
            )
            .where(
                SecurityAuditLog.created_at >= start_time,
                SecurityAuditLog.action == "block",
                SecurityAuditLog.username.isnot(None),
            )
            .group_by(SecurityAuditLog.username)
            .order_by(desc("block_count"))
            .limit(10)
        )
        user_result = await session.execute(user_query)
        top_users = [
            {
                "username": row.username,
                "block_count": int(row.block_count),
            }
            for row in user_result.all()
        ]

        # 4. 今日概览
        today_total_query = select(func.count()).select_from(SecurityAuditLog).where(
            SecurityAuditLog.created_at >= today_start
        )
        today_total_result = await session.execute(today_total_query)
        total_checks_today = today_total_result.scalar() or 0

        today_block_query = select(func.count()).select_from(SecurityAuditLog).where(
            SecurityAuditLog.created_at >= today_start,
            SecurityAuditLog.action == "block",
        )
        today_block_result = await session.execute(today_block_query)
        block_count_today = today_block_result.scalar() or 0

        today_critical_query = select(func.count()).select_from(SecurityAuditLog).where(
            SecurityAuditLog.created_at >= today_start,
            SecurityAuditLog.risk_level == "critical",
        )
        today_critical_result = await session.execute(today_critical_query)
        critical_count_today = today_critical_result.scalar() or 0

        # 平均响应时间（用 created_at 的分钟粒度模拟，实际可记录响应时间字段）
        # 由于没有专门的响应时间字段，这里返回固定占位值
        avg_response_ms = 0.0

        return {
            "risk_trend": risk_trend,
            "top_words": top_words,
            "top_users": top_users,
            "summary": {
                "total_checks_today": total_checks_today,
                "block_count_today": block_count_today,
                "critical_count_today": critical_count_today,
                "avg_response_ms": avg_response_ms,
            },
        }

    @staticmethod
    def _infer_event_type(matched_rules: List[Dict[str, Any]]) -> str:
        """从匹配规则推断事件类型"""
        if not matched_rules:
            return "pass"
        types = set(r.get("type", "") for r in matched_rules)
        if "model_detection" in types:
            return "model_detection"
        if any(t in types for t in ["sensitive", "risk"]):
            return "word_match"
        if any(t in types for t in ["id_card", "phone", "bank_card"]):
            return "regex_match"
        return "unknown"
