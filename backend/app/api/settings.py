"""
系统设置 API
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models.models import SystemSetting, User

router = APIRouter(prefix="/settings", tags=["系统设置"])


class SettingUpdate(BaseModel):
    value: str


class SettingsBatchUpdate(BaseModel):
    settings: dict[str, str]


@router.get("")
async def get_all_settings(
    category: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取所有设置"""
    query = select(SystemSetting)
    if category:
        query = query.where(SystemSetting.category == category)

    result = await db.execute(query.order_by(SystemSetting.key))
    settings = result.scalars().all()

    # 转换为字典格式
    return {
        s.key: {"value": s.value, "category": s.category, "description": s.description}
        for s in settings
    }


@router.get("/{key}")
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取单个设置"""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(status_code=404, detail="设置项不存在")

    return {"key": setting.key, "value": setting.value, "category": setting.category}


@router.put("/{key}")
async def update_setting(
    key: str,
    data: SettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新单个设置"""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()

    if not setting:
        # 创建新设置
        setting = SystemSetting(key=key, value=data.value, category="custom")
        db.add(setting)
    else:
        setting.value = data.value

    await db.commit()
    return {"message": "设置已更新"}


@router.put("")
async def batch_update_settings(
    data: SettingsBatchUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """批量更新设置"""
    for key, value in data.settings.items():
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = result.scalar_one_or_none()

        if not setting:
            setting = SystemSetting(key=key, value=value, category="custom")
            db.add(setting)
        else:
            setting.value = value

    await db.commit()
    return {"message": "设置已更新"}


# 预定义的设置项默认值
DEFAULT_SETTINGS = {
    # 基本配置
    "system_name": ("PioneClaw", "general", "系统名称"),
    "system_desc": ("AI Agent 管理平台", "general", "系统描述"),
    "language": ("zh-CN", "general", "默认语言"),
    "timezone": ("Asia/Shanghai", "general", "时区"),
    "debug_mode": ("false", "general", "调试模式"),
    # 执行配置
    "max_tool_turns": ("20", "execution", "最大工具调用轮次"),
    "skill_timeout": ("300", "execution", "Skill 执行超时（秒）"),
    "task_timeout": ("60", "execution", "任务分发超时（秒）"),
    "max_concurrency": ("10", "execution", "最大并发任务数"),
    "enable_planning": ("false", "execution", "启用步骤规划"),
    "enable_reflection": ("false", "execution", "启用反思机制"),
    "auto_retry": ("true", "execution", "自动重试"),
    "max_retries": ("3", "execution", "最大重试次数"),
    # 安全设置
    "token_expiry": ("1440", "security", "Token 有效期（分钟）"),
    "multi_login": ("true", "security", "允许同时登录"),
    "file_approval": ("false", "security", "文件操作审批"),
    "network_approval": ("false", "security", "网络请求审批"),
    "code_approval": ("true", "security", "代码执行审批"),
    "command_approval": ("true", "security", "危险命令执行前审批"),
    "permission_mode": (
        "workspace_write",
        "security",
        "全局默认权限模式: read_only/workspace_write/danger_full_access",
    ),
    "recovery_auto_fix": ("true", "security", "允许自动执行无副作用的恢复操作"),
    "log_level": ("info", "security", "日志级别"),
    "log_retention": ("30", "security", "日志保留天数"),
    # 通知配置
    "email_enabled": ("false", "notification", "启用邮件通知"),
    "smtp_host": ("", "notification", "SMTP 服务器"),
    "smtp_port": ("587", "notification", "SMTP 端口"),
    "smtp_user": ("", "notification", "发件人邮箱"),
    "smtp_pass": ("", "notification", "邮箱密码"),
    "webhook_enabled": ("false", "notification", "启用 Webhook"),
    "webhook_url": ("", "notification", "Webhook URL"),
    "notify_task_complete": ("true", "notification", "任务完成通知"),
    "notify_task_failed": ("true", "notification", "任务失败通知"),
    "notify_system_alert": ("true", "notification", "系统告警通知"),
}


async def init_default_settings(db: AsyncSession):
    """初始化默认设置"""
    for key, (value, category, description) in DEFAULT_SETTINGS.items():
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        if not result.scalar_one_or_none():
            setting = SystemSetting(
                key=key, value=value, category=category, description=description
            )
            db.add(setting)
    await db.commit()
