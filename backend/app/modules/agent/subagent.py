"""
SubagentManager - 子 Agent 管理器

借鉴自 CountBot 的 subagent.py + OpenClaw 的 subagent-capabilities/announce/registry

功能：
- 后台任务创建、执行、取消
- 3种任务类型（GENERAL / RESEARCH / BUILD）
- 深度与角色系统（main / orchestrator / leaf）— 借鉴 OpenClaw
- Push-based 结果回传 — 借鉴 OpenClaw
- 并发隔离 Lane（nested / subagent / cron）— 借鉴 OpenClaw
- Agent 间访问控制（target policy）— 借鉴 OpenClaw
- 任务状态追踪
- 进度通知
- 工具调用记录
- 自动重试（指数退避）
- 心跳监控（超时取消）
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.modules.agent.handoff import HandoffConfig, HandoffResult

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(Enum):
    """任务类型枚举"""

    GENERAL = "general"  # 通用任务
    RESEARCH = "research"  # 研究任务（深度分析）
    BUILD = "build"  # 构建任务（代码生成）


class BuiltinAgentType(str, Enum):
    """内置 Agent 类型枚举 — 统一 SpawnTool 和 SubagentManager 的类型系统

    借鉴 Claude Code src/tools/AgentTool/built-in/ 的 5 种 built-in agent types，
    加上 SubagentManager 原有的 3 种 TaskType，共 7 种。
    """

    GENERAL = "general"  # 通用 Agent — 完整工具集
    RESEARCH = "research"  # 研究 Agent — 深度分析
    BUILD = "build"  # 构建 Agent — 代码生成
    EXPLORE = "explore"  # 探索 Agent — 代码库只读探索（Glob+Grep+Read）
    PLAN = "plan"  # 方案 Agent — 架构设计（Plan 模式）
    VERIFICATION = "verification"  # 验证 Agent — 运行测试+E2E
    GUIDE = "guide"  # 指南 Agent — PioneClaw 使用指南


# ==================== 深度与角色系统（借鉴 OpenClaw subagent-capabilities.ts）====================


class SubagentRole(Enum):
    """子 Agent 角色枚举

    借鉴 OpenClaw resolveSubagentRoleForDepth:
    - depth <= 0 → main（顶层，可 spawn orchestrator 或 leaf）
    - 0 < depth < max_depth → orchestrator（中间层，可 spawn leaf）
    - depth >= max_depth → leaf（叶子节点，不可再 spawn）

    默认 max_spawn_depth=1，即深度1就是leaf，保持扁平架构。
    """

    MAIN = "main"
    ORCHESTRATOR = "orchestrator"
    LEAF = "leaf"


class SubagentConfig:
    """子 Agent 全局配置

    借鉴 OpenClaw config/agent-limits.ts
    """

    DEFAULT_MAX_SPAWN_DEPTH = 1  # 默认扁平：深度1=leaf
    DEFAULT_MAX_CHILDREN_PER_AGENT = 5  # 每个 Agent 最多 spawn 5 个子任务
    DEFAULT_MAX_CONCURRENT = 8  # 全局最大并发子 Agent
    DEFAULT_AGENT_MAX_CONCURRENT = 4  # 单 Agent 最大并发

    def __init__(
        self,
        max_spawn_depth: int = DEFAULT_MAX_SPAWN_DEPTH,
        max_children_per_agent: int = DEFAULT_MAX_CHILDREN_PER_AGENT,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        agent_max_concurrent: int = DEFAULT_AGENT_MAX_CONCURRENT,
    ):
        self.max_spawn_depth = max_spawn_depth
        self.max_children_per_agent = max_children_per_agent
        self.max_concurrent = max_concurrent
        self.agent_max_concurrent = agent_max_concurrent


def resolve_subagent_role(
    depth: int, max_spawn_depth: int = SubagentConfig.DEFAULT_MAX_SPAWN_DEPTH
) -> SubagentRole:
    """根据深度决定角色

    借鉴 OpenClaw resolveSubagentRoleForDepth
    """
    if depth <= 0:
        return SubagentRole.MAIN
    return SubagentRole.ORCHESTRATOR if depth < max_spawn_depth else SubagentRole.LEAF


def resolve_subagent_capabilities(role: SubagentRole) -> dict[str, Any]:
    """根据角色决定能力

    借鉴 OpenClaw resolveSubagentCapabilities
    """
    return {
        "role": role,
        "can_spawn": role in (SubagentRole.MAIN, SubagentRole.ORCHESTRATOR),
        "can_control_children": role != SubagentRole.LEAF,
    }


# ==================== 并发隔离 Lane（借鉴 OpenClaw lanes.ts）====================


class LaneType(Enum):
    """并发隔离 Lane 类型

    不同来源的 Agent 运行在不同 Lane，防止自死锁。
    借鉴 OpenClaw lanes.ts: nested / subagent / cron
    """

    NESTED = "nested"  # 嵌套调用
    SUBAGENT = "subagent"  # 子 Agent
    CRON = "cron"  # 定时任务


class SubagentLane:
    """并发隔离 Lane

    每个 Lane 独立信号量，互不影响。
    借鉴 OpenClaw lanes.ts
    """

    def __init__(self, lane_type: LaneType, max_concurrent: int = 4):
        self.lane_type = lane_type
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0

    @property
    def active_count(self) -> int:
        return self._active_count

    async def acquire(self):
        await self._semaphore.acquire()
        self._active_count += 1

    def release(self):
        self._semaphore.release()
        self._active_count = max(0, self._active_count - 1)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()


# ==================== Agent 间访问控制（借鉴 OpenClaw subagent-target-policy.ts）====================


class SubagentTargetPolicy:
    """Agent 间 spawn 访问控制

    借鉴 OpenClaw subagent-target-policy.ts:
    - allow_agents=None → 仅允许同 Agent spawn
    - allow_agents=["*"] → 允许任意 Agent
    - allow_agents=["agent-a", "agent-b"] → 白名单
    """

    def __init__(self, allow_agents: list[str] | None = None):
        self.allow_agents = allow_agents

    def can_spawn_target(self, source_agent_id: str, target_agent_id: str) -> bool:
        """检查源 Agent 是否可以 spawn 目标 Agent"""
        if self.allow_agents is None:
            return source_agent_id == target_agent_id
        if "*" in self.allow_agents:
            return True
        return target_agent_id in self.allow_agents


# ==================== Push-based 结果回传（借鉴 OpenClaw subagent-announce.ts）====================


class SubagentAnnouncer:
    """子 Agent 结果推送器

    借鉴 OpenClaw subagent-announce.ts:
    子 Agent 完成后主动推送结果给父 Agent（Push），而非父 Agent 轮询。
    系统提示词明确禁止轮询行为。
    """

    def __init__(self):
        # parent_task_id → list of completed child events
        self._pending_announcements: dict[str, list[dict[str, Any]]] = {}

    async def announce(
        self,
        child_task_id: str,
        parent_task_id: str | None,
        result: str | None,
        status: str,
        manager: "SubagentManager",
    ) -> None:
        """子 Agent 完成后主动推送结果给父 Agent"""
        event = {
            "type": "subagent_completed",
            "task_id": child_task_id,
            "result": result,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }

        if not parent_task_id:
            # 无父任务，无需推送
            return

        # 缓存待推送事件
        if parent_task_id not in self._pending_announcements:
            self._pending_announcements[parent_task_id] = []
        self._pending_announcements[parent_task_id].append(event)

        # 推送给父任务的事件回调
        parent_task = manager.tasks.get(parent_task_id)
        if parent_task and parent_task.event_callback:
            try:
                cb_result = parent_task.event_callback(
                    "subagent_child_completed", event
                )
                if asyncio.iscoroutine(cb_result):
                    await cb_result
                logger.debug(
                    f"Announced child {child_task_id} completion to parent {parent_task_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to announce to parent {parent_task_id}: {e}")

    def get_pending_announcements(self, parent_task_id: str) -> list[dict[str, Any]]:
        """获取父任务待处理的子任务完成事件"""
        return self._pending_announcements.pop(parent_task_id, [])

    def clear(self, parent_task_id: str) -> None:
        """清除指定父任务的待推送事件"""
        self._pending_announcements.pop(parent_task_id, None)


# ==================== 子 Agent 专用系统提示词（借鉴 OpenClaw subagent-system-prompt.ts）====================

SUBAGENT_SYSTEM_PROMPT_TEMPLATE = """# 子 Agent 上下文

你是**子 Agent**，由 {parent_label} 为特定任务生成。

## 规则

1. **专注任务** — 只做分配的任务，不做其他
2. **完成任务** — 你的最终消息将自动报告给父 Agent
3. **不要主动** — 不发心跳、不做主动行为、不搞副业
4. **短暂存在** — 任务完成后你可能被终止
5. **不要轮询** — 不要反复检查状态（sessions_list/sleep 等），等待结果推送

## 任务

{task_description}

## 角色信息

- 角色: {role}
- 深度: {depth}
- 可否 spawn: {can_spawn}
"""


@dataclass
class SubagentTask:
    """子 Agent 任务"""

    task_id: str
    label: str
    message: str
    task_type: TaskType = TaskType.GENERAL
    session_id: str | None = None
    system_prompt: str | None = None
    event_callback: Callable | None = None
    enable_tools: bool = True
    model_override: dict[str, Any] | None = None
    cancel_token: Any | None = None
    max_retries: int = 2
    retry_count: int = 0

    # 深度与角色（借鉴 OpenClaw）
    depth: int = 0
    role: SubagentRole = SubagentRole.MAIN
    parent_task_id: str | None = None  # 父任务 ID，用于 Push-based 回传
    agent_id: str | None = None  # 所属 Agent ID，用于 target policy

    # 状态
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    result: str | None = None
    error: str | None = None

    # 时间戳
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # 心跳
    last_heartbeat: float = field(default_factory=time.time)

    # 工具调用记录
    tool_call_records: list[dict[str, Any]] = field(default_factory=list)

    # 完成事件
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def can_spawn(self) -> bool:
        """当前任务是否可以 spawn 子任务"""
        return resolve_subagent_capabilities(self.role)["can_spawn"]

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "label": self.label,
            "message": self.message,
            "task_type": self.task_type.value,
            "session_id": self.session_id,
            "status": self.status.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "depth": self.depth,
            "role": self.role.value,
            "parent_task_id": self.parent_task_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "tool_call_records": self.tool_call_records,
        }


class SubagentManager:
    """
    子 Agent 管理器

    管理后台任务的创建、执行、取消和状态查询
    支持自动重试、心跳监控、并发控制

    OpenClaw 借鉴增强：
    - 深度与角色系统（main/orchestrator/leaf）
    - Push-based 结果回传（SubagentAnnouncer）
    - 并发隔离 Lane（nested/subagent/cron）
    - Agent 间访问控制（SubagentTargetPolicy）
    """

    def __init__(
        self,
        agent_loop=None,
        max_concurrent: int = 3,
        timeout_seconds: int = 600,
        heartbeat_interval: float = 30.0,
        heartbeat_timeout: float = 300.0,
        config: SubagentConfig | None = None,
        target_policy: SubagentTargetPolicy | None = None,
    ):
        """
        初始化 SubagentManager

        Args:
            agent_loop: AgentLoop 实例
            max_concurrent: 最大并发任务数
            timeout_seconds: 任务超时时间（秒）
            heartbeat_interval: 心跳检查间隔（秒）
            heartbeat_timeout: 心跳超时时间（秒）
            config: 子 Agent 全局配置（深度/角色限制）
            target_policy: Agent 间访问控制策略
        """
        self._agent_loop = agent_loop
        self._max_concurrent = max_concurrent
        self._timeout_seconds = timeout_seconds
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout

        # 全局配置
        self.config = config or SubagentConfig()
        self.target_policy = target_policy or SubagentTargetPolicy()

        # 任务存储
        self.tasks: dict[str, SubagentTask] = {}
        self.running_tasks: dict[str, asyncio.Task] = {}

        # 信号量控制并发
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # 并发隔离 Lane（借鉴 OpenClaw）
        self._lanes: dict[LaneType, SubagentLane] = {
            LaneType.NESTED: SubagentLane(
                LaneType.NESTED, max_concurrent=self.config.agent_max_concurrent
            ),
            LaneType.SUBAGENT: SubagentLane(
                LaneType.SUBAGENT, max_concurrent=self.config.max_concurrent
            ),
            LaneType.CRON: SubagentLane(LaneType.CRON, max_concurrent=2),
        }

        # Push-based 结果回传
        self.announcer = SubagentAnnouncer()

        # 心跳监控
        self._heartbeat_running = False
        self._heartbeat_task: asyncio.Task | None = None

        logger.debug(
            f"SubagentManager initialized: max_concurrent={max_concurrent}, "
            f"max_spawn_depth={self.config.max_spawn_depth}"
        )

    def create_task(
        self,
        label: str,
        message: str,
        task_type: TaskType = TaskType.GENERAL,
        session_id: str | None = None,
        system_prompt: str | None = None,
        event_callback: Callable | None = None,
        enable_tools: bool = True,
        model_override: dict[str, Any] | None = None,
        cancel_token: Any | None = None,
        max_retries: int = 2,
        depth: int = 0,
        parent_task_id: str | None = None,
        agent_id: str | None = None,
        lane_type: LaneType = LaneType.SUBAGENT,
    ) -> str:
        """
        创建新的后台任务

        Args:
            depth: 子 Agent 深度（0=顶层，默认扁平模式下1=leaf）
            parent_task_id: 父任务 ID（用于 Push-based 回传）
            agent_id: 所属 Agent ID（用于 target policy）
            lane_type: 并发隔离 Lane 类型

        Returns:
            str: 任务 ID
        """
        task_id = str(uuid.uuid4())

        # 根据 depth 自动决定角色
        role = resolve_subagent_role(depth, self.config.max_spawn_depth)
        capabilities = resolve_subagent_capabilities(role)

        # 深度检查：leaf 不能 spawn
        if depth > 0 and not capabilities["can_spawn"]:
            logger.debug(f"Task at depth {depth} is leaf, cannot spawn children")

        # 子任务数量检查
        if parent_task_id:
            parent = self.tasks.get(parent_task_id)
            if parent:
                sibling_count = len(
                    [
                        t
                        for t in self.tasks.values()
                        if t.parent_task_id == parent_task_id
                    ]
                )
                if sibling_count >= self.config.max_children_per_agent:
                    raise ValueError(
                        f"Parent task {parent_task_id} already has {sibling_count} children "
                        f"(max: {self.config.max_children_per_agent})"
                    )

        # Agent 间访问控制检查
        if agent_id and parent_task_id:
            parent = self.tasks.get(parent_task_id)
            if parent and parent.agent_id and parent.agent_id != agent_id:
                if not self.target_policy.can_spawn_target(parent.agent_id, agent_id):
                    raise ValueError(
                        f"Agent {parent.agent_id} is not allowed to spawn target agent {agent_id}"
                    )

        task = SubagentTask(
            task_id=task_id,
            label=label,
            message=message,
            task_type=task_type,
            session_id=session_id,
            system_prompt=system_prompt,
            event_callback=event_callback,
            enable_tools=enable_tools,
            model_override=model_override,
            cancel_token=cancel_token,
            max_retries=max_retries,
            depth=depth,
            role=role,
            parent_task_id=parent_task_id,
            agent_id=agent_id,
        )

        self.tasks[task_id] = task
        logger.info(
            f"Created task {task_id}: {label} (type={task_type.value}, "
            f"depth={depth}, role={role.value})"
        )

        return task_id

    async def execute_task(self, task_id: str) -> None:
        """调度任务执行"""
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != TaskStatus.PENDING:
            logger.warning(
                f"Task {task_id} is not pending, current status: {task.status}"
            )
            return

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        task.progress = 0
        task.last_heartbeat = time.time()

        logger.info(f"Starting task {task_id}: {task.label}")

        async_task = asyncio.create_task(self._run_task(task))
        self.running_tasks[task_id] = async_task

    async def _run_task(self, task: SubagentTask) -> None:
        """执行任务（内部方法）"""
        try:
            await asyncio.wait_for(
                self._run_task_impl(task), timeout=self._timeout_seconds
            )
        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            task.error = f"任务超时（超过{self._timeout_seconds}秒）"
            task.completed_at = datetime.now()
            logger.error(
                f"Task {task.task_id} timed out after {self._timeout_seconds}s"
            )
            await self._emit_event(task, "task_failed", {"error": task.error})
            # 尝试重试
            await self._maybe_retry(task)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "任务被取消"
            task.completed_at = datetime.now()
            logger.info(f"Task {task.task_id} cancelled")
            await self._emit_event(task, "task_cancelled", {})
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            logger.error(f"Task {task.task_id} failed: {e}")
            await self._emit_event(task, "task_failed", {"error": str(e)})
            # 尝试重试
            await self._maybe_retry(task)
        finally:
            if task.task_id in self.running_tasks:
                del self.running_tasks[task.task_id]
            task.done_event.set()

    async def _run_task_impl(self, task: SubagentTask) -> None:
        """实际的任务执行逻辑"""
        # 选择 Lane（根据深度和来源）
        lane = self._resolve_lane(task)

        async with lane, self._semaphore:
            await self._emit_event(
                task,
                "task_started",
                {
                    "label": task.label,
                    "message": task.message,
                    "task_type": task.task_type.value,
                    "depth": task.depth,
                    "role": task.role.value,
                },
            )

            # 检查取消
            if self._is_cancelled(task):
                raise asyncio.CancelledError("Task cancelled before start")

            # 更新心跳
            task.last_heartbeat = time.time()

            # 构建系统提示词
            system_prompt = task.system_prompt or self._build_default_prompt(task)

            # 如果有重试历史，注入失败原因
            if task.retry_count > 0:
                system_prompt += (
                    f"\n\n注意：这是第 {task.retry_count + 1} 次尝试。"
                    f"之前的失败原因：{task.error or '未知'}\n"
                    "请分析失败原因并尝试不同的方法。"
                )

            if self._agent_loop:
                # 执行 AgentLoop
                result = await self._agent_loop.process_direct(
                    message=task.message,
                    system_prompt=system_prompt,
                    model_override=task.model_override,
                )
            else:
                # 无 agent_loop，返回模拟结果
                result = f"[模拟] 任务 '{task.label}' 执行完成"

            # 更新心跳
            task.last_heartbeat = time.time()

            # 检查取消
            if self._is_cancelled(task):
                raise asyncio.CancelledError("Task cancelled after execution")

            # 标记完成
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.progress = 100
            task.completed_at = datetime.now()

            logger.info(f"Task {task.task_id} completed successfully")

            await self._emit_event(
                task,
                "task_completed",
                {
                    "result": result,
                    "progress": 100,
                },
            )

            # Push-based 结果回传：子 Agent 完成后主动通知父 Agent
            await self.announcer.announce(
                child_task_id=task.task_id,
                parent_task_id=task.parent_task_id,
                result=result,
                status="completed",
                manager=self,
            )

    def _resolve_lane(self, task: SubagentTask) -> SubagentLane:
        """根据任务属性选择并发隔离 Lane"""
        if task.depth > 0:
            return self._lanes[LaneType.NESTED]
        return self._lanes.get(LaneType.SUBAGENT, self._lanes[LaneType.NESTED])

    async def _maybe_retry(self, task: SubagentTask) -> None:
        """尝试重试失败的任务"""
        if task.retry_count >= task.max_retries:
            logger.info(f"Task {task.task_id} reached max retries ({task.max_retries})")
            return

        # 指数退避
        backoff = min(2**task.retry_count, 30)  # 最大 30 秒
        logger.info(
            f"Task {task.task_id} retrying in {backoff}s (attempt {task.retry_count + 1}/{task.max_retries})"
        )

        await asyncio.sleep(backoff)

        # 重新入队
        task.retry_count += 1
        task.status = TaskStatus.PENDING
        task.error = None
        task.completed_at = None
        task.progress = 0
        task.last_heartbeat = time.time()

        await self._emit_event(
            task,
            "task_retrying",
            {
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
            },
        )

        await self.execute_task(task.task_id)

    async def _emit_event(
        self, task: SubagentTask, event_type: str, data: dict
    ) -> None:
        """发送事件"""
        if task.event_callback:
            try:
                result = task.event_callback(event_type, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Event callback failed: {e}")

    def _is_cancelled(self, task: SubagentTask) -> bool:
        """检查任务是否被取消"""
        if task.cancel_token and hasattr(task.cancel_token, "is_cancelled"):
            return task.cancel_token.is_cancelled
        return False

    def _build_default_prompt(self, task: SubagentTask) -> str:
        """构建默认系统提示词

        深度 > 0 的子 Agent 使用专用提示词（借鉴 OpenClaw subagent-system-prompt.ts），
        明确告知角色和约束，防止越权行为。
        """
        # 深度 > 0 的子 Agent 使用专用提示词
        if task.depth > 0:
            capabilities = resolve_subagent_capabilities(task.role)
            parent_label = "父 Agent"
            if task.parent_task_id:
                parent = self.tasks.get(task.parent_task_id)
                if parent:
                    parent_label = f"父 Agent（{parent.label}）"

            task_desc = f"**类型**: {task.task_type.value}\n**目标**: {task.label}\n**详情**: {task.message}"

            return SUBAGENT_SYSTEM_PROMPT_TEMPLATE.format(
                parent_label=parent_label,
                task_description=task_desc,
                role=task.role.value,
                depth=task.depth,
                can_spawn="是" if capabilities["can_spawn"] else "否",
            )

        # 顶层任务：使用详细的类型专用提示词
        type_prompts = {
            TaskType.GENERAL: """你是一个通用子智能体。
你的任务是完成用户指定的任务。
- 理解用户的需求
- 采取适当的行动
- 提供清晰准确的结果
- 如果遇到问题，说明原因""",
            TaskType.RESEARCH: """你是一个研究专家。
你的任务是深入研究给定的主题。
- 收集相关信息和资料
- 分析不同来源的观点
- 提供全面准确的调研报告
- 标注信息来源和可信度""",
            TaskType.BUILD: """你是一个构建和测试专家。
你的任务是执行构建和测试任务。
- 按照要求执行构建命令
- 运行相关测试
- 分析构建和测试结果
- 报告成功/失败以及原因
- 提供错误排查建议""",
            "explore": """你是一个代码库探索专家。
你的任务是深入理解代码库的结构和功能。
- 先了解整体目录结构
- 找到相关的核心文件和模块
- 分析代码逻辑和依赖关系
- 用简洁清晰的方式总结你的发现""",
            "plan": """你是一个架构设计专家。
你的任务是分析需求并设计实现方案。
- 理解业务需求和技术约束
- 设计系统架构和模块划分
- 制定分步骤的实施计划
- 评估潜在风险和权衡""",
            "verification": """你是一个验证专家。
你的任务是验证代码的正确性和质量。
- 运行测试套件确认功能正确
- 检查边界条件和异常情况
- 验证安全和性能要求
- 按严重程度分类问题""",
            "guide": """你是 PioneClaw 使用指南专家。
你的任务是帮助用户了解和使用 PioneClaw 平台。
- 解释 PioneClaw 的功能和概念
- 提供操作指导和最佳实践
- 回答使用中的问题
- 推荐适合用户场景的配置""",
            "debug": """你是一个调试专家。
你的任务是帮助定位和解决问题。
- 首先理解问题的表现
- 分析错误信息和日志
- 定位可能的根本原因
- 提供具体的修复建议
- 验证修复方案的有效性""",
            "review": """你是一个代码审查专家。
你的任务是审查代码并提供改进建议。
- 检查代码的正确性和完整性
- 识别潜在的安全风险
- 评估代码性能和可维护性
- 提供具体的改进建议
- 按照严重程度分类问题""",
            "long_running": """你是长时任务启动器。你的唯一职责是：分析任务 → 准备环境 → 用 run_background 启动后台命令 → 标记成功退出。

## 核心规则（必须遵守）
1. **必须用 run_background 启动耗时命令**（下载、编译、克隆等），这是你唯一的启动方式
2. **exec 只能用于快速准备**（ls、mkdir 等几秒内完成的命令），绝对不能用来执行实际任务
3. 启动成功后立即标记 [TASK_SUCCESS] 并退出
4. 如果启动失败，标记 [TASK_FAILED: 原因]

## 你有最多5轮对话，按以下顺序执行：
- 第1-2轮：用 exec 做准备工作（检查目录、清理旧文件等）
- 第3-5轮：必须调用 run_background 启动任务
- 如果你用 exec 执行了耗时命令，任务会失败并重试""",
        }

        # Try task_type first, then agent_type string
        task_type_str = (
            task.task_type.value
            if hasattr(task.task_type, "value")
            else str(task.task_type)
        )
        base = type_prompts.get(task_type_str, type_prompts[TaskType.GENERAL])
        return (
            f"{base}\n\n任务: {task.label}\n详情: {task.message}\n\n请专注于完成任务，给出清晰、详细的结果。禁止编造未获取的数据。"
            ""
        )

    # ==================== 心跳监控 ====================

    async def start_heartbeat(self) -> None:
        """启动心跳监控"""
        if self._heartbeat_running:
            return

        self._heartbeat_running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Heartbeat monitor started")

    async def stop_heartbeat(self) -> None:
        """停止心跳监控"""
        self._heartbeat_running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        logger.info("Heartbeat monitor stopped")

    async def _heartbeat_loop(self) -> None:
        """心跳检查循环"""
        while self._heartbeat_running:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                await self._check_heartbeats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat check error: {e}")

    async def _check_heartbeats(self) -> None:
        """检查所有运行中任务的心跳"""
        now = time.time()

        for task_id, task in list(self.tasks.items()):
            if task.status != TaskStatus.RUNNING:
                continue

            elapsed = now - task.last_heartbeat
            if elapsed > self._heartbeat_timeout:
                logger.warning(
                    f"Task {task_id} heartbeat timeout: "
                    f"last heartbeat {elapsed:.0f}s ago (timeout: {self._heartbeat_timeout}s)"
                )
                await self.cancel_task(task_id)

    def update_heartbeat(self, task_id: str) -> None:
        """更新任务心跳"""
        task = self.tasks.get(task_id)
        if task:
            task.last_heartbeat = time.time()

    # ==================== 任务操作 ====================

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self.tasks.get(task_id)
        if not task:
            logger.warning(f"Cannot cancel task {task_id}: not found")
            return False

        if task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
            logger.warning(f"Cannot cancel task {task_id}: status is {task.status}")
            return False

        # 取消 asyncio.Task
        async_task = self.running_tasks.get(task_id)
        if async_task:
            async_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await async_task

        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now()
        logger.info(f"Cancelled task {task_id}")

        return True

    async def cancel_all_tasks(self) -> int:
        """取消所有运行中的任务"""
        cancelled_count = 0
        running_task_ids = list(self.running_tasks.keys())

        for task_id in running_task_ids:
            if await self.cancel_task(task_id):
                cancelled_count += 1

        return cancelled_count

    def get_task(self, task_id: str) -> SubagentTask | None:
        """获取任务信息"""
        return self.tasks.get(task_id)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        session_id: str | None = None,
        task_type: TaskType | None = None,
    ) -> list[SubagentTask]:
        """列出任务"""
        tasks = list(self.tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]

        if session_id:
            tasks = [t for t in tasks if t.session_id == session_id]

        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]

        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def get_running_tasks(self) -> list[SubagentTask]:
        """获取所有运行中的任务"""
        return self.list_tasks(status=TaskStatus.RUNNING)

    def get_stats(self) -> dict[str, Any]:
        """获取任务统计信息"""
        type_stats = {}
        for t in self.tasks.values():
            type_stats.setdefault(
                t.task_type.value,
                {"total": 0, "running": 0, "completed": 0, "failed": 0},
            )
            type_stats[t.task_type.value]["total"] += 1
            if t.status == TaskStatus.RUNNING:
                type_stats[t.task_type.value]["running"] += 1
            elif t.status == TaskStatus.COMPLETED:
                type_stats[t.task_type.value]["completed"] += 1
            elif t.status == TaskStatus.FAILED:
                type_stats[t.task_type.value]["failed"] += 1

        return {
            "total": len(self.tasks),
            "pending": len(
                [t for t in self.tasks.values() if t.status == TaskStatus.PENDING]
            ),
            "running": len(
                [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
            ),
            "completed": len(
                [t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED]
            ),
            "failed": len(
                [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]
            ),
            "cancelled": len(
                [t for t in self.tasks.values() if t.status == TaskStatus.CANCELLED]
            ),
            "by_type": type_stats,
        }

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        if task.status == TaskStatus.RUNNING:
            asyncio.create_task(self.cancel_task(task_id))

        del self.tasks[task_id]
        logger.info(f"Deleted task {task_id}")
        return True

    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """清理旧任务"""
        from datetime import timedelta

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleaned = 0

        for task_id, task in list(self.tasks.items()):
            if task.status in [
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ]:
                if task.completed_at and task.completed_at < cutoff_time:
                    del self.tasks[task_id]
                    cleaned += 1

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} old tasks")

        return cleaned

    async def wait_for_task(
        self, task_id: str, timeout: float | None = None
    ) -> SubagentTask | None:
        """等待任务完成"""
        task = self.tasks.get(task_id)
        if not task:
            return None

        try:
            await asyncio.wait_for(task.done_event.wait(), timeout=timeout)
            return task
        except asyncio.TimeoutError:
            return None

    # ==================== Handoff 集成（借鉴 PraisonAI）====================

    async def handoff_to(
        self,
        source_task_id: str,
        target_agent: Any,
        prompt: str,
        config: Optional["HandoffConfig"] = None,
    ) -> "HandoffResult":
        """委托给目标 Agent（Handoff 模式）

        借鉴 PraisonAI Handoff，提供轻量级委托机制。

        Args:
            source_task_id: 源任务 ID
            target_agent: 目标 Agent（AgentLoop 或类似对象）
            prompt: 委托的任务描述
            config: Handoff 配置

        Returns:
            HandoffResult: 委托结果
        """
        from app.modules.agent.handoff import Handoff, HandoffConfig

        source_task = self.tasks.get(source_task_id)
        if not source_task:
            raise ValueError(f"Source task {source_task_id} not found")

        config = config or HandoffConfig()
        handoff = Handoff(target_agent, config=config)

        # 获取源任务的上下文（如果有）
        context = getattr(source_task, "_context_messages", None)

        result = await handoff.execute(
            source_agent=source_task,
            prompt=prompt,
            context=context,
        )

        return result

    async def parallel_handoffs(
        self,
        source_task_id: str,
        targets: list[tuple],  # List[(agent, prompt)] 或 List[(agent, prompt, config)]
        max_concurrent: int = 5,
    ) -> list["HandoffResult"]:
        """并行委托给多个 Agent

        借鉴 PraisonAI parallel_handoffs

        Args:
            source_task_id: 源任务 ID
            targets: 目标列表，每个元素是 (agent, prompt) 或 (agent, prompt, config)
            max_concurrent: 最大并发数

        Returns:
            List[HandoffResult]: 各委托的结果列表
        """
        from app.modules.agent.handoff import parallel_handoffs as _parallel_handoffs

        source_task = self.tasks.get(source_task_id)
        if not source_task:
            raise ValueError(f"Source task {source_task_id} not found")

        return await _parallel_handoffs(
            source_agent=source_task,
            targets=targets,
            max_concurrent=max_concurrent,
        )
