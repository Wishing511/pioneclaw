"""
Doctor 系统诊断 — DB / 模型 / 磁盘 / 配置健康检查
"""

import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import User

router = APIRouter(prefix="/doctor", tags=["系统诊断"])


@router.get("")
async def run_doctor(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """运行系统诊断检查"""
    checks = []

    # 1. DB 连接
    try:
        await db.execute(text("SELECT 1"))
        checks.append({"name": "数据库连接", "status": "ok", "detail": "正常"})
    except Exception as e:
        checks.append({"name": "数据库连接", "status": "error", "detail": str(e)})

    # 2. 用户数量
    try:
        r = await db.execute(text("SELECT COUNT(*) FROM users"))
        count = r.scalar()
        checks.append({"name": "用户数据", "status": "ok", "detail": f"{count} 个用户"})
    except Exception as e:
        checks.append({"name": "用户数据", "status": "error", "detail": str(e)})

    # 3. 磁盘空间
    try:
        backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
        usage = shutil.disk_usage(backend_dir)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        pct = (1 - usage.free / usage.total) * 100
        status = "ok" if free_gb > 5 else ("warn" if free_gb > 1 else "error")
        checks.append(
            {
                "name": "磁盘空间",
                "status": status,
                "detail": f"已用 {pct:.1f}%，剩余 {free_gb:.1f} GB / {total_gb:.1f} GB",
            }
        )
    except Exception as e:
        checks.append({"name": "磁盘空间", "status": "error", "detail": str(e)})

    # 4. 配置完整性
    try:
        from app.core.config import settings

        issues = []
        if not settings.SECRET_KEY or len(settings.SECRET_KEY) < 16:
            issues.append("SECRET_KEY 未配置或过短")
        if not settings.DATABASE_URL:
            issues.append("DATABASE_URL 未配置")
        checks.append(
            {
                "name": "配置完整性",
                "status": "error" if issues else "ok",
                "detail": "; ".join(issues) if issues else "关键配置完整",
            }
        )
    except Exception as e:
        checks.append({"name": "配置完整性", "status": "error", "detail": str(e)})

    # 5. 模型可用性（快速检查）
    try:
        from sqlalchemy import select

        from app.models import AIModelConfig

        r = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_active).limit(1)
        )
        config = r.scalar_one_or_none()
        if config:
            checks.append(
                {
                    "name": "AI 模型配置",
                    "status": "ok",
                    "detail": f"默认模型: {config.model_name} ({config.provider})",
                }
            )
        else:
            checks.append(
                {"name": "AI 模型配置", "status": "warn", "detail": "无活跃模型配置"}
            )
    except Exception as e:
        checks.append({"name": "AI 模型配置", "status": "warn", "detail": str(e)})

    # 6. Runner 状态
    try:
        r = await db.execute(
            text("SELECT COUNT(*) FROM runners WHERE status = 'online'")
        )
        online = r.scalar() or 0
        r2 = await db.execute(text("SELECT COUNT(*) FROM runners"))
        total = r2.scalar() or 0
        checks.append(
            {
                "name": "Runner 状态",
                "status": "ok",
                "detail": f"{online}/{total} 在线",
            }
        )
    except Exception as e:
        checks.append({"name": "Runner 状态", "status": "warn", "detail": str(e)})

    # Summary
    errors = sum(1 for c in checks if c["status"] == "error")
    warns = sum(1 for c in checks if c["status"] == "warn")
    healthy = errors == 0

    return {
        "healthy": healthy,
        "errors": errors,
        "warnings": warns,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
