"""
CronScheduler - 基于 croniter 的精确定时调度器

替代原来的轮询方式，使用 croniter 精确计算下次执行时间。
支持：
- 标准 5 字段 cron 表达式
- 精确到秒级的下次执行时间计算
- 自动注册 Heartbeat 任务
- 后台调度循环
- 执行结果持久化（CronExecutionLog）
- 启动时从 DB 恢复任务
- 真实 Agent 执行回调
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from croniter import croniter

logger = logging.getLogger(__name__)


class CronScheduler:
    """
    基于 croniter 的精确调度器

    用法:
        scheduler = CronScheduler()
        scheduler.add_job("greeting", "0 9 * * *", my_callback)
        await scheduler.start()
    """

    def __init__(self, timezone_offset: int = 8) -> None:
        """
        初始化调度器

        Args:
            timezone_offset: 时区偏移（小时），默认东八区
        """
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._timezone_offset = timezone_offset

    def _now(self) -> datetime:
        """获取当前时间（本地时区）"""
        from datetime import timedelta

        return datetime.now(timezone(timedelta(hours=self._timezone_offset)))

    def add_job(
        self,
        job_id: str,
        cron_expr: str,
        callback: Callable,
        enabled: bool = True,
        **kwargs: Any,
    ) -> bool:
        """
        添加定时任务

        Args:
            job_id: 任务 ID
            cron_expr: cron 表达式
            callback: 回调函数（同步或异步）
            enabled: 是否启用
            **kwargs: 额外参数

        Returns:
            bool: 是否添加成功
        """
        try:
            if not croniter.is_valid(cron_expr):
                logger.error(f"[CronScheduler] 无效的 cron 表达式: {cron_expr}")
                return False

            now = self._now()
            cron = croniter(cron_expr, now)
            next_run = cron.get_next(datetime)

            self._jobs[job_id] = {
                "cron_expr": cron_expr,
                "callback": callback,
                "enabled": enabled,
                "next_run": next_run,
                "last_run": None,
                "run_count": 0,
                "cron_iter": cron,
                **kwargs,
            }

            logger.info(
                f"[CronScheduler] 添加任务 '{job_id}': {cron_expr}, "
                f"下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return True

        except Exception as exc:
            logger.error(f"[CronScheduler] 添加任务失败: {exc}")
            return False

    def remove_job(self, job_id: str) -> bool:
        """移除定时任务"""
        if job_id in self._jobs:
            del self._jobs[job_id]
            logger.info(f"[CronScheduler] 移除任务: {job_id}")
            return True
        return False

    def enable_job(self, job_id: str) -> bool:
        """启用任务"""
        if job_id in self._jobs:
            self._jobs[job_id]["enabled"] = True
            now = self._now()
            cron = croniter(self._jobs[job_id]["cron_expr"], now)
            self._jobs[job_id]["next_run"] = cron.get_next(datetime)
            self._jobs[job_id]["cron_iter"] = cron
            return True
        return False

    def disable_job(self, job_id: str) -> bool:
        """禁用任务"""
        if job_id in self._jobs:
            self._jobs[job_id]["enabled"] = False
            return True
        return False

    def get_job(self, job_id: str) -> dict | None:
        """获取任务信息"""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job_id,
            "cron_expr": job["cron_expr"],
            "enabled": job["enabled"],
            "next_run": job["next_run"],
            "last_run": job["last_run"],
            "run_count": job["run_count"],
        }

    def list_jobs(self) -> list[dict]:
        """列出所有任务"""
        return [self.get_job(jid) for jid in self._jobs]

    def get_next_run(self, cron_expr: str) -> datetime | None:
        """
        计算指定 cron 表达式的下次执行时间

        Args:
            cron_expr: cron 表达式

        Returns:
            datetime: 下次执行时间
        """
        try:
            now = self._now()
            cron = croniter(cron_expr, now)
            return cron.get_next(datetime)
        except Exception:
            return None

    @staticmethod
    def validate_cron_expr(cron_expr: str) -> bool:
        """验证 cron 表达式是否有效"""
        return croniter.is_valid(cron_expr)

    @staticmethod
    def describe_cron_expr(cron_expr: str) -> str:
        """
        人类可读的 cron 表达式描述

        Args:
            cron_expr: cron 表达式

        Returns:
            str: 描述文字
        """
        if not croniter.is_valid(cron_expr):
            return "无效表达式"

        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return "格式错误"

        minute, hour, day, month, weekday = parts

        # 常见模式
        if minute == "*" and hour == "*":
            return "每分钟"
        if minute != "*" and hour == "*":
            return f"每小时第 {minute} 分钟"
        if (
            minute == "0"
            and hour != "*"
            and day == "*"
            and month == "*"
            and weekday == "*"
        ):
            return f"每天 {hour}:00"
        if (
            minute != "*"
            and hour != "*"
            and day == "*"
            and month == "*"
            and weekday == "*"
        ):
            return f"每天 {hour}:{minute}"
        if weekday != "*" and hour != "*" and minute != "*":
            weekday_names = {
                "0": "周日",
                "1": "周一",
                "2": "周二",
                "3": "周三",
                "4": "周四",
                "5": "周五",
                "6": "周六",
                "7": "周日",
            }
            day_name = weekday_names.get(weekday, f"周{weekday}")
            return f"每{day_name} {hour}:{minute}"

        return f"自定义 ({cron_expr})"

    async def start(self) -> None:
        """启动调度循环"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        logger.info("[CronScheduler] 调度器已启动")

    async def stop(self) -> None:
        """停止调度循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("[CronScheduler] 调度器已停止")

    async def _persist_execution(
        self,
        job_id: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        result: str | None,
        error_message: str | None,
        duration_ms: int,
        last_run: datetime,
        run_count: int,
    ) -> None:
        """将执行日志写入 DB，并同步 CronJob 的 last_run/run_count"""
        try:
            from sqlalchemy import select

            from app.core.database import async_session_maker
            from app.models.models import CronExecutionLog, CronJob

            async with async_session_maker() as session:
                db_result = await session.execute(
                    select(CronJob).where(CronJob.name == job_id)
                )
                db_job = db_result.scalar_one_or_none()

                if db_job:
                    log = CronExecutionLog(
                        cron_job_id=db_job.id,
                        started_at=started_at,
                        finished_at=finished_at,
                        status=status,
                        result=result[:10000] if result else None,
                        error_message=error_message[:2000] if error_message else None,
                        duration_ms=duration_ms,
                    )
                    session.add(log)
                    db_job.last_run = last_run
                    db_job.run_count = run_count
                    await session.commit()

        except Exception as exc:
            logger.warning(f"[CronScheduler] 执行日志持久化失败: {exc}")

    async def _schedule_loop(self) -> None:
        """调度主循环"""
        while self._running:
            try:
                now = self._now()

                for job_id, job in list(self._jobs.items()):
                    if not job["enabled"]:
                        continue

                    next_run = job.get("next_run")
                    if next_run and now >= next_run:
                        started_at = datetime.now(timezone.utc)
                        result_str = None
                        error_msg = None
                        status = "completed"

                        try:
                            callback = job["callback"]
                            result = callback()
                            if asyncio.iscoroutine(result):
                                result = await result
                            result_str = str(result)[:10000] if result else None
                            job["last_run"] = now
                            job["run_count"] += 1

                        except Exception as exc:
                            status = "failed"
                            error_msg = str(exc)
                            job["last_run"] = now
                            job["run_count"] += 1
                            logger.error(
                                f"[CronScheduler] 任务 '{job_id}' 执行失败: {exc}"
                            )

                        finished_at = datetime.now(timezone.utc)
                        duration_ms = int(
                            (finished_at - started_at).total_seconds() * 1000
                        )

                        # 持久化执行日志
                        await self._persist_execution(
                            job_id=job_id,
                            started_at=started_at,
                            finished_at=finished_at,
                            status=status,
                            result=result_str,
                            error_message=error_msg,
                            duration_ms=duration_ms,
                            last_run=now,
                            run_count=job["run_count"],
                        )

                        # 计算下次执行时间
                        try:
                            cron = croniter(job["cron_expr"], now)
                            job["next_run"] = cron.get_next(datetime)
                            job["cron_iter"] = cron
                        except Exception:
                            job["enabled"] = False

                # 每秒检查一次
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[CronScheduler] 调度循环异常: {exc}")
                await asyncio.sleep(5)

    async def run_job_now(self, job_id: str) -> dict:
        """手动触发任务执行"""
        job = self._jobs.get(job_id)
        if not job:
            return {"success": False, "error": f"任务不存在: {job_id}"}

        started_at = datetime.now(timezone.utc)
        result_str = None
        error_msg = None
        status = "completed"

        try:
            callback = job["callback"]
            result = callback()
            if asyncio.iscoroutine(result):
                result = await result
            result_str = str(result)[:10000] if result else None
            job["last_run"] = self._now()
            job["run_count"] += 1
        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            logger.error(f"[CronScheduler] 手动执行 '{job_id}' 失败: {exc}")

        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        # 持久化
        await self._persist_execution(
            job_id=job_id,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            result=result_str,
            error_message=error_msg,
            duration_ms=duration_ms,
            last_run=job["last_run"],
            run_count=job["run_count"],
        )

        return {
            "success": status == "completed",
            "status": status,
            "duration_ms": duration_ms,
        }

    def ensure_heartbeat_job(
        self,
        schedule: str = "0 9,12,18 * * *",
        callback: Callable | None = None,
    ) -> str:
        """
        确保 Heartbeat 任务已注册

        Args:
            schedule: cron 表达式，默认每天 9/12/18 点
            callback: 心跳回调函数

        Returns:
            str: 任务 ID
        """
        job_id = "heartbeat_default"

        if job_id not in self._jobs:
            if callback is None:
                callback = self._default_heartbeat_callback

            self.add_job(job_id, schedule, callback, enabled=True)
            logger.info(f"[CronScheduler] Heartbeat 任务已注册: {schedule}")
        else:
            existing = self._jobs[job_id]
            if existing["cron_expr"] != schedule:
                self.remove_job(job_id)
                self.add_job(
                    job_id,
                    schedule,
                    callback or self._default_heartbeat_callback,
                    enabled=True,
                )
                logger.info(f"[CronScheduler] Heartbeat 调度已更新: {schedule}")

        return job_id

    @staticmethod
    def _default_heartbeat_callback():
        """默认心跳回调（日志记录）"""
        logger.info("[CronScheduler] Heartbeat tick")


def _make_cron_callback(job_name: str, config: dict):
    """创建执行 Agent 的 cron 回调

    Args:
        job_name: 任务名称
        config: 任务配置，包含 prompt 和/或 agent_id
    """

    async def _execute_agent():
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.models.models import AIModelConfig

        prompt = config.get("prompt") or config.get("input_data", {}).get("message", "")

        if not prompt:
            logger.warning(f"[Cron] 任务 '{job_name}' 没有 prompt，跳过执行")
            return "No prompt configured"

        try:
            from app.modules.agent.loop import AgentLoop
            from app.modules.agent.providers.simple import SimpleLLMProvider
            from app.modules.tools import ToolRegistry, register_builtin_tools

            # 获取默认 AI 模型配置
            async with async_session_maker() as session:
                result = await session.execute(
                    select(AIModelConfig).where(AIModelConfig.is_default).limit(1)
                )
                ai_config = result.scalar_one_or_none()

            if not ai_config:
                logger.warning("[Cron] 未找到默认 AIModelConfig，跳过执行")
                return "No AI model configured"

            provider_config = type(
                "Config",
                (),
                {
                    "api_key": ai_config.api_key or "",
                    "base_url": ai_config.base_url or "",
                    "model_name": ai_config.model_name or "gpt-4o",
                    "temperature": ai_config.temperature,
                    "max_tokens": ai_config.max_tokens,
                },
            )()
            provider = SimpleLLMProvider(config=provider_config)

            tool_registry = ToolRegistry()
            register_builtin_tools(tool_registry)

            from app.core.security_client import security_client

            agent_loop = AgentLoop(
                provider=provider,
                tools=tool_registry,
                model=provider_config.model_name,
                max_iterations=5,
                security_client=security_client,
            )
            result = await agent_loop.process_direct(message=prompt)
            return result

        except Exception as e:
            logger.error(f"[Cron] Agent 执行失败 '{job_name}': {e}")
            return f"Error: {e}"

    return _execute_agent


async def reconcile_cron_jobs() -> dict:
    """启动时从 DB 加载 enabled 的 CronJob，重新注册到调度器

    确保任务在进程重启后不丢失。

    Returns:
        {"registered": int, "skipped": int}
    """
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models.models import CronJob

    scheduler = get_cron_scheduler()
    summary: dict = {"registered": 0, "skipped": 0}

    try:
        async with async_session_maker() as session:
            result = await session.execute(select(CronJob).where(CronJob.is_enabled))
            jobs = result.scalars().all()
    except Exception as e:
        logger.warning(f"[Cron] 启动恢复查询 DB 失败: {e}")
        return summary

    for job in jobs:
        if job.name in scheduler._jobs:
            summary["skipped"] += 1
            continue

        callback = _make_cron_callback(job.name, job.config or {})
        success = scheduler.add_job(
            job_id=job.name,
            cron_expr=job.schedule_value,
            callback=callback,
            enabled=True,
        )
        if success:
            summary["registered"] += 1

    logger.info(
        f"[Cron] 启动恢复: {summary['registered']} 注册, {summary['skipped']} 跳过"
    )

    # ── 注册 AutoDream 内置定时任务 ──────────────────────────────────────
    try:
        from app.core.database import async_session_maker
        from app.models.autodream import AutoDreamConfig

        async with async_session_maker() as session:
            result = await session.execute(select(AutoDreamConfig).limit(1))
            ad_config = result.scalar_one_or_none()

        if ad_config and ad_config.enabled and "autodream" not in scheduler._jobs:
            from datetime import datetime, timezone

            async def _run_autodream_cron():
                """AutoDream cron 回调（Phase 1 Stub）"""
                logger.info("[AutoDream] Cron 触发记忆整理（Phase 1 stub）")
                async with async_session_maker() as session:
                    from app.models.autodream import AutoDreamLog

                    log = AutoDreamLog(triggered_by="cron", status="success")
                    log.details = '{"note": "Phase 1 stub - engine not yet implemented"}'
                    session.add(log)
                    await session.commit()

            ok = scheduler.add_job(
                job_id="autodream",
                cron_expr=ad_config.cron_expression,
                callback=_run_autodream_cron,
                enabled=True,
            )
            if ok:
                summary["registered"] += 1
                logger.info(
                    f"[AutoDream] 已注册定时任务: {ad_config.cron_expression}"
                )
    except Exception as e:
        logger.warning(f"[AutoDream] Cron 注册跳过: {e}")

    # 启动调度器
    if not scheduler._running:
        await scheduler.start()

    return summary


# 全局调度器实例
_scheduler: CronScheduler | None = None


def get_cron_scheduler() -> CronScheduler:
    """获取全局调度器"""
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler
