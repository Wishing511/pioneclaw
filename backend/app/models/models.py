from __future__ import annotations

import enum
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.models import (  # noqa: F811
        AgentSkill,
        Organization,
        TaskComment,
        User,
        Workspace,
    )

logger = logging.getLogger(__name__)


class EncryptedString(TypeDecorator):
    """透明加密字符串类型 — 写入时自动加密，读取时自动解密。

    兼容明文回退：未配置 ENCRYPTION_KEY 时以 PLAINTEXT: 前缀存储。
    也兼容旧数据（纯明文）的自动识别。
    """

    impl = String
    cache_ok = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        """写入 DB 前加密。"""
        if value is None:
            return None
        from app.core.crypto import encrypt

        return encrypt(value)

    def process_result_value(self, value, dialect):
        """从 DB 读取后解密。"""
        if value is None:
            return None
        from app.core.crypto import decrypt

        try:
            return decrypt(value)
        except Exception:
            logger.warning("Failed to decrypt DB value, returning as-is")
            return value


class UserRole(str, enum.Enum):
    USER = "user"
    ORG_ADMIN = "org_admin"
    SUPER_ADMIN = "super_admin"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SkillScope(str, enum.Enum):
    SYSTEM = "system"  # 系统级，超管创建，全局可用
    ORG = "org"  # 组织级，组织管理员创建/审批，组织内可用
    USER = "user"  # 用户级，用户自建，仅自己可用


class RunnerStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    OFFLINE = "offline"
    ONLINE = "online"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(50))
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # PioneClaw 扩展字段
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    position: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_org_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    default_workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True
    )
    default_runner_id: Mapped[int | None] = mapped_column(
        ForeignKey("runners.id"), nullable=True
    )

    # 登录安全
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agents: Mapped[list[Agent]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    skills: Mapped[list[Skill]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    organization: Mapped[Organization | None] = relationship(
        back_populates="users", foreign_keys=[organization_id]
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(100), default="gpt-4o")
    max_turns: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[AgentStatus] = mapped_column(
        SQLEnum(AgentStatus), default=AgentStatus.ACTIVE
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    creator: Mapped[User] = relationship(back_populates="agents")
    workspace: Mapped[Workspace | None] = relationship(foreign_keys=[workspace_id])
    skills: Mapped[list[AgentSkill]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), default="custom")
    scope: Mapped[str] = mapped_column(String(20), default="user", index=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    package_type: Mapped[str] = mapped_column(String(20), default="inline")
    package_size: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    always_activate: Mapped[bool] = mapped_column(Boolean, default=False)
    skill_format: Mapped[str] = mapped_column(String(20), default="inline")
    dependencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    creator: Mapped[User] = relationship(back_populates="skills")
    organization: Mapped[Organization | None] = relationship(
        foreign_keys=[organization_id]
    )
    agents: Mapped[list[AgentSkill]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    agent: Mapped[Agent] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship(back_populates="agents")


class Runner(Base):
    __tablename__ = "runners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RunnerStatus] = mapped_column(
        SQLEnum(RunnerStatus), default=RunnerStatus.PENDING
    )
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_key: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_task: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    success_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_type: Mapped[str] = mapped_column(String(20))
    schedule_value: Mapped[str] = mapped_column(String(100))
    job_type: Mapped[str] = mapped_column(String(20), default="system")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CronExecutionLog(Base):
    """Cron 任务执行日志"""

    __tablename__ = "cron_execution_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cron_job_id: Mapped[int] = mapped_column(ForeignKey("cron_jobs.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running/completed/failed
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MCPServerConfig(Base):
    __tablename__ = "mcp_server_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    transport: Mapped[str] = mapped_column(String(20), default="stdio")
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50), default="general")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    call_count: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class AIModelConfig(Base):
    """AI 模型配置表"""

    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # 配置名称
    display_name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(
        String(50), default="openai"
    )  # openai, anthropic, azure, custom
    model_name: Mapped[str] = mapped_column(String(100))  # gpt-4o, claude-3-opus 等
    base_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # API base URL
    api_key: Mapped[str | None] = mapped_column(EncryptedString(500), nullable=True)
    context_window: Mapped[int] = mapped_column(Integer, default=128000)  # 上下文窗口
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)  # 最大输出 token
    temperature: Mapped[float] = mapped_column(default=0.7)
    tier: Mapped[str] = mapped_column(
        String(20), default="sonnet"
    )  # opus/sonnet/haiku/custom
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否本 tier 默认
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 其他配置

    # 组织分配
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    allowed_orgs: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # 允许使用的组织 ID 列表
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class KnowledgeBase(Base):
    """知识库"""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关联文档
    documents: Mapped[list[KnowledgeDocument]] = relationship(
        "KnowledgeDocument", back_populates="knowledge_base"
    )


class KnowledgeDocument(Base):
    """知识库文档"""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # 来源 URL 或文件路径
    doc_type: Mapped[str] = mapped_column(
        String(50), default="text"
    )  # text, markdown, pdf, url
    file_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # 上传文件路径
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 文件大小
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)  # 分块数量
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关联知识库
    knowledge_base: Mapped[KnowledgeBase] = relationship(
        "KnowledgeBase", back_populates="documents"
    )


class Role(Base):
    """角色表"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 角色名称
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 角色代码
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 描述
    permissions: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 权限列表

    # PioneClaw 扩展字段
    type: Mapped[str] = mapped_column(String(20), default="custom")  # system/custom
    level: Mapped[int] = mapped_column(Integer, default=0)  # 0-3 权限等级
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )

    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否系统角色
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    organization: Mapped[Organization | None] = relationship()


class Task(Base):
    """任务表"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))  # 任务标题
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # 任务描述
    status: Mapped[str] = mapped_column(
        String(20), default="todo"
    )  # todo, in_progress, done, cancelled
    priority: Mapped[str] = mapped_column(
        String(20), default="normal"
    )  # low, normal, high, urgent
    task_type: Mapped[str] = mapped_column(
        String(50), default="manual"
    )  # manual, agent, cron

    # 关联
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True, index=True
    )  # 父任务
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )  # 关联智能体
    runner_id: Mapped[int | None] = mapped_column(
        ForeignKey("runners.id"), nullable=True
    )  # 执行 Runner
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 创建者
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )  # 指派人
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True, index=True
    )

    # 执行信息
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 输入数据
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 输出结果
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # 错误信息

    # 时间
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # 开始时间
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # 完成时间
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 截止时间

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # PioneClaw 扩展关系
    comments: Mapped[list[TaskComment]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class AgentExecution(Base):
    """Agent 执行历史记录"""

    __tablename__ = "agent_executions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), index=True
    )  # 关联智能体
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # 执行用户

    # 输入输出
    message: Mapped[str] = mapped_column(Text)  # 用户消息
    response: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI 响应
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # 系统提示词

    # 执行状态
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running, completed, failed, cancelled

    # 统计信息
    total_iterations: Mapped[int] = mapped_column(Integer, default=0)  # 总迭代次数
    total_tool_calls: Mapped[int] = mapped_column(Integer, default=0)  # 工具调用次数
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 总 token 数
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 输入 token
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 输出 token
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # 耗时（毫秒）

    # 工具调用记录
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 工具调用列表

    # 错误信息
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 模型信息
    model_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # 使用的模型
    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_model_configs.id"), nullable=True
    )

    # 时间
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Session(Base):
    """聊天会话"""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    runner_id: Mapped[int | None] = mapped_column(
        ForeignKey("runners.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # active / archived
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SessionMessage(Base):
    """会话消息"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user / assistant / system / tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class TaskTemplate(Base):
    """任务模板"""

    __tablename__ = "task_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    task_type: Mapped[str] = mapped_column(String(50), default="manual")
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class TaskDependency(Base):
    """任务依赖关系"""

    __tablename__ = "task_dependencies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
