from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.models import AgentStatus, RunnerStatus, UserRole


# ==================== User Schemas ====================
class UserBase(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_一-鿿]+$",
        description="用户名，3-50位，支持字母/数字/下划线/中文",
    )
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=50)


class UserCreate(UserBase):
    password: str = Field(min_length=8, description="密码，至少8位")


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar: str | None = None
    email: EmailStr | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str  # 改为 str 类型，避免 EmailStr 严格验证导致已有数据无法返回
    display_name: str | None = None
    role: UserRole
    is_active: bool
    avatar: str | None = None
    organization_id: str | None = None
    is_super_admin: bool = False
    is_org_admin: bool = False
    permissions: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    username: str
    password: str
    ip: str | None = None  # 可选，记录登录 IP


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    avatar: str | None = Field(default=None, max_length=200000)
    phone: str | None = Field(default=None, max_length=20, pattern=r"^[\d\-\+\(\)\s]*$")
    department: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=100)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: int
    exp: datetime


# ==================== Agent Schemas ====================
class AgentBase(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    model: str = "gpt-4o"
    max_turns: int = 20
    system_prompt: str | None = None


class AgentCreate(AgentBase):
    skill_ids: list[int] = []


class AgentUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    model: str | None = None
    max_turns: int | None = None
    system_prompt: str | None = None
    status: AgentStatus | None = None
    skill_ids: list[int] | None = None


class AgentResponse(AgentBase):
    id: int
    status: AgentStatus
    creator_id: int
    created_at: datetime
    updated_at: datetime
    skills: list["SkillBrief"] = []

    class Config:
        from_attributes = True


class AgentBrief(BaseModel):
    id: int
    name: str
    display_name: str
    status: AgentStatus

    class Config:
        from_attributes = True


# ==================== Skill Schemas ====================
class SkillBase(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    category: str = "custom"
    scope: str = "user"


class SkillCreate(SkillBase):
    content: str | None = None
    is_public: bool = True
    always_activate: bool = False
    skill_format: str = "inline"
    dependencies: dict | None = None


class SkillUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    content: str | None = None
    is_active: bool | None = None
    is_public: bool | None = None
    always_activate: bool | None = None
    skill_format: str | None = None
    dependencies: dict | None = None
    scope: str | None = None


class SkillResponse(SkillBase):
    id: int | None = None
    source: str = "db"  # "db" | "file"
    content: str | None = None
    package_type: str = "inline"
    package_size: int = 0
    always_activate: bool = False
    skill_format: str = "inline"
    tags: list | None = None
    dependencies: dict | None = None
    is_active: bool = True
    is_public: bool = True
    creator_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class SkillBrief(BaseModel):
    id: int
    name: str
    display_name: str
    category: str
    always_activate: bool = False

    class Config:
        from_attributes = True


# ==================== Dashboard Schemas ====================
class DashboardStats(BaseModel):
    total_calls: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    avg_duration_ms: float
    failed_calls: int
    model_distribution: dict
    hourly_calls: list = []


class UsageStats(BaseModel):
    date: str
    calls: int
    tokens: int


# ==================== Runner Schemas ====================


class RunnerBase(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    host: str | None = None
    port: int | None = None


class RunnerCreate(RunnerBase):
    api_key: str | None = None
    capabilities: dict | None = None
    version: str | None = None
    platform: str | None = None
    user_token: str | None = None  # Runner 端携带的用户 JWT，用于自动关联用户


class RunnerUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    host: str | None = None
    port: int | None = None
    status: RunnerStatus | None = None
    user_id: int | None = None


class RunnerResponse(RunnerBase):
    id: int
    status: RunnerStatus
    api_key: str | None = None
    capabilities: dict | None
    version: str | None
    platform: str | None
    last_heartbeat: datetime | None
    current_task: str | None
    total_tasks: int
    success_tasks: int
    failed_tasks: int
    applied_at: datetime
    approved_at: datetime | None
    approved_by: int | None = None
    user_id: int | None = None
    username: str | None = None
    reject_reason: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("api_key", mode="before")
    @classmethod
    def mask_api_key(cls, v):
        if v and str(v).strip():
            return "••••••••"
        return None

    class Config:
        from_attributes = True


class RunnerApprove(BaseModel):
    approve: bool
    user_id: int | None = None
    reject_reason: str | None = None


class RunnerHeartbeat(BaseModel):
    current_task: str | None = None
    capabilities: dict | None = None


# ==================== AI Model Config Schemas ====================
class AIModelConfigBase(BaseModel):
    name: str
    display_name: str | None = None
    provider: str = "openai"
    model_name: str
    base_url: str
    tier: str = "sonnet"  # opus/sonnet/haiku/custom
    context_window: int = 128000
    max_tokens: int = 4096
    temperature: float = 0.7


class AIModelConfigCreate(AIModelConfigBase):
    api_key: str  # 必填
    is_default: bool = False
    extra_config: dict | None = None


class AIModelConfigUpdate(BaseModel):
    display_name: str | None = None
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    extra_config: dict | None = None


class AIModelConfigResponse(AIModelConfigBase):
    id: int
    api_key: str | None = None
    is_default: bool
    is_active: bool
    tier: str
    extra_config: dict | None
    created_at: datetime
    updated_at: datetime

    @field_validator("api_key", mode="before")
    @classmethod
    def mask_api_key(cls, v):
        if v and str(v).strip():
            return "••••••••"
        return None

    class Config:
        from_attributes = True


class AIModelTestRequest(BaseModel):
    model_config_id: int | None = None
    # 或者直接传入配置测试
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    test_prompt: str = "Hello, are you working?"


class AIModelTestResponse(BaseModel):
    success: bool
    message: str
    response: str | None = None
    latency_ms: int | None = None


# ==================== Knowledge Base Schemas ====================
class KnowledgeBaseBase(BaseModel):
    name: str
    description: str | None = None


class KnowledgeBaseCreate(KnowledgeBaseBase):
    pass


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class KnowledgeDocumentBase(BaseModel):
    title: str
    content: str
    source: str | None = None


class KnowledgeDocumentCreate(KnowledgeDocumentBase):
    knowledge_base_id: int


class KnowledgeDocumentUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    source: str | None = None


class KnowledgeDocumentResponse(KnowledgeDocumentBase):
    id: int
    knowledge_base_id: int
    doc_type: str
    file_path: str | None
    file_size: int | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class KnowledgeBaseResponse(KnowledgeBaseBase):
    id: int
    is_active: bool
    document_count: int = 0
    total_chunks: int = 0
    created_at: datetime
    updated_at: datetime
    documents: list[KnowledgeDocumentResponse] = []

    class Config:
        from_attributes = True


# ==================== Role Schemas ====================
class RoleBase(BaseModel):
    name: str
    code: str
    description: str | None = None


class RoleCreate(RoleBase):
    permissions: dict | None = None
    is_active: bool = True


class RoleUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    permissions: dict | None = None
    is_active: bool | None = None


class RoleResponse(RoleBase):
    id: int
    permissions: dict | None
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== Task Schemas ====================
class TaskBase(BaseModel):
    title: str
    description: str | None = None
    priority: str = "normal"
    task_type: str = "manual"
    parent_id: int | None = None
    agent_id: int | None = None
    assignee_id: int | None = None
    due_at: datetime | None = None
    input_data: dict | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee_id: int | None = None
    due_at: datetime | None = None
    output_data: dict | None = None
    error_message: str | None = None


class TaskResponse(TaskBase):
    id: int
    status: str
    parent_id: int | None = None
    runner_id: int | None
    creator_id: int
    input_data: dict | None
    output_data: dict | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== Cron Schemas ====================
class CronJobBase(BaseModel):
    name: str
    cron_expr: str
    agent_id: int | None = None
    input_data: dict | None = None
    description: str | None = None


class CronJobCreate(CronJobBase):
    is_active: bool = True


class CronJobUpdate(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    agent_id: int | None = None
    input_data: dict | None = None
    description: str | None = None
    is_active: bool | None = None


class CronJobResponse(CronJobBase):
    id: int
    is_active: bool
    last_run: datetime | None
    next_run: datetime | None
    run_count: int
    created_at: datetime
    updated_at: datetime


class CronExecutionLogResponse(BaseModel):
    """Cron 任务执行日志响应"""

    id: int
    cron_job_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    result: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None

    class Config:
        from_attributes = True


# ==================== MCP Schemas ====================
class MCPServerConfigCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    auth_config: dict | None = None
    is_enabled: bool = True


class MCPServerConfigUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    auth_config: dict | None = None
    is_enabled: bool | None = None


class MCPServerConfigResponse(BaseModel):
    id: int
    name: str
    transport: str
    command: str | None = None
    args: list | None = None
    env: dict | None = None
    url: str | None = None
    auth_config: dict | None = None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MCPConnectionStatus(BaseModel):
    server: str
    status: str
    tool_count: int = 0
    resource_count: int = 0
    server_info: dict | None = None
    error_message: str | None = None


# ==================== Common Schemas ====================
class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


class MessageResponse(BaseModel):
    message: str
    success: bool = True


# 更新 forward references
AgentResponse.model_rebuild()
