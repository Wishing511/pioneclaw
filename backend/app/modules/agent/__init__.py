"""
Agent 模块 - Agent 执行引擎

包含：
- AgentLoop: ReAct 推理循环
- WorkflowEngine: 多智能体工作流
- SubagentManager: 子 Agent 管理
- TaskManager: 任务取消令牌和管理
- ContextBuilder: 上下文构建（待实现）
- Handoff: 统一委托机制（借鉴 PraisonAI）
"""

from app.modules.agent.analyzer import (
    MessageAnalyzer,
    MessageStats,
)
from app.modules.agent.auto_agents import (
    DEFAULT_TEMPLATES,
    AgentRole,
    AgentTemplate,
    AutoAgentResult,
    AutoAgents,
    SubTask,
    TaskAnalyzer,
    TaskComplexity,
    TaskDecomposition,
    auto_run,
)
from app.modules.agent.compactor import (
    CompactionConfig,
    CompactionResult,
    Compactor,
    create_compactor,
)
from app.modules.agent.context import (
    ContextBuilder,
    PersonaConfig,
    SessionContext,
    create_context_builder,
    get_default_persona_config,
)
from app.modules.agent.context_files import (
    CONTEXT_FILE_ORDER,
    DYNAMIC_FILES,
    STABLE_FILES,
    ContextFileLoader,
    IdentityFile,
    PromptCacheStrategy,
    merge_identity_content,
    parse_identity_md,
)
from app.modules.agent.conversation_summarizer import (
    ConversationSummarizer,
    SummarizerConfig,
)
from app.modules.agent.guardrails import (
    Guardrail,
    GuardrailConfig,
    GuardrailExecutor,
    GuardrailFailedError,
    ValidationResult,
    builtin_validators,
)
from app.modules.agent.handoff import (
    ContextPolicy,
    CycleDetectedError,
    Handoff,
    HandoffConfig,
    HandoffDepthExceededError,
    HandoffResult,
    HandoffTracker,
    get_handoff_tracker,
    handoff_filters,
    parallel_handoffs,
    reset_handoff_tracker,
)
from app.modules.agent.heartbeat import (
    HEARTBEAT_JOB_ID,
    HEARTBEAT_MESSAGE,
    HeartbeatConfig,
    HeartbeatDispatch,
    HeartbeatService,
    create_heartbeat_service,
    get_default_heartbeat_config,
)
from app.modules.agent.injected_state import (
    AgentState,
    Injected,
    InjectedContext,
    StateInjector,
    get_injected_inner_type,
    get_state_injector,
    injectable,
    is_injected_type,
    mark_injected_in_schema,
    reset_state_injector,
    with_state,
)
from app.modules.agent.interrupt import (
    Checkpoint,
    InterruptManager,
    InterruptOption,
    InterruptPoint,
    InterruptReason,
    InterruptStatus,
    get_interrupt_manager,
    interrupt_options,
    reset_interrupt_manager,
)
from app.modules.agent.loop import (
    AgentExecutionResult,
    AgentIteration,
    AgentLoop,
    AgentStatus,
    CancelToken,
    ToolCall,
)
from app.modules.agent.magic_docs import MagicDocUpdater
from app.modules.agent.memory import (
    MemoryEntry,
    MemorySource,
    MemoryStats,
    MemoryStore,
    get_memory_store,
    init_memory_store,
)
from app.modules.agent.memory_extractor import MemoryExtractor
from app.modules.agent.personalities import (
    Personality,
    PersonalityCategory,
    get_all_personalities,
    get_all_personality_ids,
    get_default_personality_id,
    get_personality_info,
    get_personality_prompt,
    get_personality_system_prompt,
    register_custom_personality,
)
from app.modules.agent.prompts import (
    format_memory_entry,
    get_conversation_to_memory_prompt,
    get_cron_task_prompt,
    get_heartbeat_greeting_prompt,
    get_overflow_summary_prompt,
    get_recursive_summary_prompt,
    get_short_context_summary_prompt,
)
from app.modules.agent.skills import (
    Skill,
    SkillMetadata,
    SkillsLoader,
    _xml_escape,
    get_skills_loader,
    init_skills_loader,
)
from app.modules.agent.skills_config import (
    ConfigStatus,
    SkillsConfigManager,
    get_config_manager,
    init_config_manager,
)
from app.modules.agent.skills_schema import (
    SchemaField,
    SkillSchema,
    SkillsSchemaRegistry,
    get_schema_registry,
    init_schema_registry,
)
from app.modules.agent.subagent import (
    SUBAGENT_SYSTEM_PROMPT_TEMPLATE,
    BuiltinAgentType,
    LaneType,
    SubagentAnnouncer,
    SubagentConfig,
    SubagentLane,
    SubagentManager,
    SubagentRole,
    SubagentTargetPolicy,
    SubagentTask,
    TaskStatus,
    TaskType,
    resolve_subagent_capabilities,
    resolve_subagent_role,
)
from app.modules.agent.task_manager import (
    CancellationToken,
    CancellationTokenSource,
    SessionTask,
    TaskManager,
    TaskState,
    create_cancellation_token,
    get_task_manager,
)
from app.modules.agent.taskflow import (
    VALID_TRANSITIONS,
    InvalidStateTransition,
    RevisionConflictError,
    TaskFlowManager,
)
from app.modules.agent.tool_hooks import (
    HookContext,
    HookEvent,
    HookResult,
    ToolHook,
    ToolHookRunner,
    builtin_hooks,
    hook,
)
from app.modules.agent.tracing import (
    AgentTracer,
    Span,
    SpanKind,
    SpanStatus,
    TokenUsage,
    Trace,
    get_tracer,
    reset_tracer,
    trace_agent,
    trace_tool,
)
from app.modules.agent.workflow import (
    AgentSlot,
    SlotPhase,
    WorkflowEngine,
    WorkflowMode,
)

__all__ = [
    "AgentLoop",
    "AgentStatus",
    "AgentIteration",
    "AgentExecutionResult",
    "CancelToken",
    "ToolCall",
    # Handoff（借鉴 PraisonAI）
    "Handoff",
    "HandoffConfig",
    "HandoffResult",
    "ContextPolicy",
    "CycleDetectedError",
    "HandoffDepthExceededError",
    "HandoffTracker",
    "handoff_filters",
    "parallel_handoffs",
    "get_handoff_tracker",
    "reset_handoff_tracker",
    # Guardrails（借鉴 CrewAI）
    "Guardrail",
    "GuardrailConfig",
    "GuardrailExecutor",
    "ValidationResult",
    "GuardrailFailedError",
    "builtin_validators",
    # Tool Hooks（借鉴 PraisonAI）
    "HookEvent",
    "HookContext",
    "HookResult",
    "ToolHook",
    "ToolHookRunner",
    "builtin_hooks",
    "hook",
    # Injected State（借鉴 PraisonAI）
    "Injected",
    "AgentState",
    "InjectedContext",
    "StateInjector",
    "is_injected_type",
    "get_injected_inner_type",
    "get_state_injector",
    "reset_state_injector",
    "injectable",
    "with_state",
    "mark_injected_in_schema",
    # AutoAgents（借鉴 PraisonAI）
    "TaskComplexity",
    "AgentRole",
    "AgentTemplate",
    "DEFAULT_TEMPLATES",
    "SubTask",
    "TaskDecomposition",
    "AutoAgentResult",
    "TaskAnalyzer",
    "AutoAgents",
    "auto_run",
    # Interrupt（借鉴 LangGraph）
    "InterruptManager",
    "InterruptPoint",
    "InterruptReason",
    "InterruptStatus",
    "InterruptOption",
    "Checkpoint",
    "get_interrupt_manager",
    "reset_interrupt_manager",
    "interrupt_options",
    # Tracing（借鉴 LangSmith）
    "SpanKind",
    "SpanStatus",
    "Span",
    "Trace",
    "TokenUsage",
    "AgentTracer",
    "get_tracer",
    "reset_tracer",
    "trace_agent",
    "trace_tool",
    "SubTask",
    "TaskDecomposition",
    "AutoAgentResult",
    "TaskAnalyzer",
    "AutoAgents",
    "auto_run",
    # Workflow
    "WorkflowEngine",
    "WorkflowMode",
    "AgentSlot",
    "SlotPhase",
    "SubagentManager",
    "SubagentTask",
    "TaskStatus",
    "TaskType",
    "BuiltinAgentType",
    "SubagentRole",
    "SubagentConfig",
    "SubagentLane",
    "LaneType",
    "SubagentTargetPolicy",
    "SubagentAnnouncer",
    "resolve_subagent_role",
    "resolve_subagent_capabilities",
    "SUBAGENT_SYSTEM_PROMPT_TEMPLATE",
    # TaskManager
    "CancellationToken",
    "CancellationTokenSource",
    "TaskManager",
    "TaskState",
    "SessionTask",
    "get_task_manager",
    "create_cancellation_token",
    # Personalities
    "Personality",
    "PersonalityCategory",
    "get_personality_prompt",
    "get_personality_system_prompt",
    "get_all_personalities",
    "get_all_personality_ids",
    "get_personality_info",
    "register_custom_personality",
    "get_default_personality_id",
    # Analyzer
    "MessageAnalyzer",
    "MessageStats",
    # Compactor
    "Compactor",
    "CompactionConfig",
    "CompactionResult",
    "create_compactor",
    # Prompts
    "get_conversation_to_memory_prompt",
    "get_recursive_summary_prompt",
    "get_short_context_summary_prompt",
    "get_overflow_summary_prompt",
    "get_heartbeat_greeting_prompt",
    "get_cron_task_prompt",
    "format_memory_entry",
    # Memory
    "MemoryStore",
    "MemoryEntry",
    "MemoryStats",
    "MemorySource",
    "get_memory_store",
    "init_memory_store",
    # Heartbeat
    "HeartbeatService",
    "HeartbeatConfig",
    "HeartbeatDispatch",
    "create_heartbeat_service",
    "get_default_heartbeat_config",
    "HEARTBEAT_JOB_ID",
    "HEARTBEAT_MESSAGE",
    # Context
    "ContextBuilder",
    "PersonaConfig",
    "SessionContext",
    "create_context_builder",
    "get_default_persona_config",
    # Context Files (OpenClaw)
    "ContextFileLoader",
    "IdentityFile",
    "PromptCacheStrategy",
    "parse_identity_md",
    "merge_identity_content",
    "CONTEXT_FILE_ORDER",
    "STABLE_FILES",
    "DYNAMIC_FILES",
    # Skills
    "SkillsLoader",
    "Skill",
    "SkillMetadata",
    "get_skills_loader",
    "init_skills_loader",
    "_xml_escape",
    # SkillsSchema
    "SkillsSchemaRegistry",
    "SkillSchema",
    "SchemaField",
    "get_schema_registry",
    "init_schema_registry",
    # SkillsConfig
    "SkillsConfigManager",
    "ConfigStatus",
    "get_config_manager",
    "init_config_manager",
    # TaskFlow
    "TaskFlowManager",
    "RevisionConflictError",
    "InvalidStateTransition",
    "VALID_TRANSITIONS",
    # Stage VV: 持久化记忆增强
    "MemoryExtractor",
    "ConversationSummarizer",
    "SummarizerConfig",
    "MagicDocUpdater",
]
