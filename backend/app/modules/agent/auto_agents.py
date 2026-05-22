"""
AutoAgents 自动编排系统

借鉴 PraisonAI AutoAgents：
- 自动任务分解：根据任务描述自动识别需要的 Agent
- 动态 Agent 创建：按需创建 Agent 实例
- 任务编排：自动分配任务给合适的 Agent
- 结果聚合：收集各 Agent 结果并合成最终输出

使用场景：
- 复杂任务自动分解
- 多 Agent 协作
- 动态工作流生成
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """任务复杂度"""

    SIMPLE = "simple"  # 单 Agent 可完成
    MODERATE = "moderate"  # 需要 2-3 个 Agent
    COMPLEX = "complex"  # 需要多 Agent 协作


class AgentRole(Enum):
    """预定义 Agent 角色"""

    COORDINATOR = "coordinator"  # 协调者
    RESEARCHER = "researcher"  # 研究员
    ANALYST = "analyst"  # 分析师
    WRITER = "writer"  # 写作者
    CODER = "coder"  # 程序员
    REVIEWER = "reviewer"  # 审核者


@dataclass
class AgentTemplate:
    """Agent 模板"""

    role: AgentRole
    name: str
    description: str
    system_prompt: str
    tools_filter: list[str] | None = None  # 该角色可用的工具
    priority: int = 100

    def create_agent_config(self) -> dict[str, Any]:
        """创建 Agent 配置"""
        return {
            "name": self.name,
            "role": self.role.value,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools_filter": self.tools_filter,
            "priority": self.priority,
        }


# 预定义 Agent 模板
DEFAULT_TEMPLATES: dict[AgentRole, AgentTemplate] = {
    AgentRole.COORDINATOR: AgentTemplate(
        role=AgentRole.COORDINATOR,
        name="Coordinator",
        description="协调多个 Agent 完成复杂任务",
        system_prompt="你是一个任务协调者。你的职责是分解复杂任务，分配给合适的 Agent，并整合结果。",
        priority=50,
    ),
    AgentRole.RESEARCHER: AgentTemplate(
        role=AgentRole.RESEARCHER,
        name="Researcher",
        description="搜索和收集信息",
        system_prompt="你是一个研究员。你的职责是搜索和收集与任务相关的信息。",
        tools_filter=["web_search", "knowledge_search", "document_search"],
        priority=100,
    ),
    AgentRole.ANALYST: AgentTemplate(
        role=AgentRole.ANALYST,
        name="Analyst",
        description="分析数据和信息",
        system_prompt="你是一个分析师。你的职责是分析数据，提取洞察，形成结论。",
        tools_filter=["data_analysis", "chart_generation", "statistics"],
        priority=100,
    ),
    AgentRole.WRITER: AgentTemplate(
        role=AgentRole.WRITER,
        name="Writer",
        description="撰写文档和报告",
        system_prompt="你是一个专业写作者。你的职责是根据研究结果撰写清晰、专业的文档。",
        tools_filter=["document_writer", "markdown_editor"],
        priority=100,
    ),
    AgentRole.CODER: AgentTemplate(
        role=AgentRole.CODER,
        name="Coder",
        description="编写和分析代码",
        system_prompt="你是一个程序员。你的职责是编写、分析和调试代码。",
        tools_filter=["code_execution", "file_operations", "git_operations"],
        priority=100,
    ),
    AgentRole.REVIEWER: AgentTemplate(
        role=AgentRole.REVIEWER,
        name="Reviewer",
        description="审核和验证结果",
        system_prompt="你是一个审核者。你的职责是检查其他 Agent 的工作，确保质量和准确性。",
        priority=200,
    ),
}


@dataclass
class SubTask:
    """子任务"""

    id: str
    description: str
    assigned_role: AgentRole
    assigned_agent: Any | None = None
    status: str = "pending"  # pending, running, completed, failed
    result: str | None = None
    dependencies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskDecomposition:
    """任务分解结果"""

    original_task: str
    complexity: TaskComplexity
    subtasks: list[SubTask]
    execution_order: list[str]  # 子任务 ID 列表
    estimated_agents: int
    reasoning: str = ""


@dataclass
class AutoAgentResult:
    """自动编排结果"""

    task_id: str
    original_task: str
    decomposition: TaskDecomposition | None = None
    agent_results: dict[str, Any] = field(default_factory=dict)
    final_result: str | None = None
    total_time: float = 0.0
    error: str | None = None


class TaskAnalyzer:
    """任务分析器

    分析任务复杂度，识别需要的 Agent 角色
    """

    def __init__(self, templates: dict[AgentRole, AgentTemplate] | None = None):
        self.templates = templates or DEFAULT_TEMPLATES

        # 关键词 -> 角色映射
        self.role_keywords: dict[AgentRole, list[str]] = {
            AgentRole.RESEARCHER: [
                "搜索",
                "查找",
                "研究",
                "调研",
                "收集",
                "信息",
                "search",
                "research",
                "find",
                "gather",
                "investigate",
            ],
            AgentRole.ANALYST: [
                "分析",
                "统计",
                "对比",
                "洞察",
                "趋势",
                "数据",
                "analyze",
                "analysis",
                "statistics",
                "trend",
                "insight",
            ],
            AgentRole.WRITER: [
                "写",
                "撰写",
                "文档",
                "报告",
                "文章",
                "总结",
                "write",
                "document",
                "report",
                "article",
                "summarize",
            ],
            AgentRole.CODER: [
                "代码",
                "编程",
                "实现",
                "开发",
                "调试",
                "程序",
                "code",
                "program",
                "develop",
                "implement",
                "debug",
            ],
            AgentRole.REVIEWER: [
                "审核",
                "检查",
                "验证",
                "评估",
                "审查",
                "review",
                "check",
                "verify",
                "evaluate",
                "audit",
            ],
        }

    def analyze_complexity(self, task: str) -> TaskComplexity:
        """分析任务复杂度"""
        # 简单启发式规则
        sentence_count = len(re.split(r"[。.!！?？]", task))
        keyword_count = sum(
            1
            for keywords in self.role_keywords.values()
            for kw in keywords
            if kw.lower() in task.lower()
        )

        # 检查是否有多个子任务（通过"然后"、"接着"等词）
        sequence_words = ["然后", "接着", "之后", "最后", "and then", "after that"]
        has_sequence = any(w in task.lower() for w in sequence_words)

        if sentence_count <= 1 and keyword_count <= 1 and not has_sequence:
            return TaskComplexity.SIMPLE
        elif sentence_count <= 3 and keyword_count <= 3:
            return TaskComplexity.MODERATE
        else:
            return TaskComplexity.COMPLEX

    def identify_roles(self, task: str) -> list[AgentRole]:
        """识别任务需要的角色"""
        matched_roles = set()
        task_lower = task.lower()

        for role, keywords in self.role_keywords.items():
            for keyword in keywords:
                if keyword.lower() in task_lower:
                    matched_roles.add(role)
                    break

        # 如果没有匹配到任何角色，默认需要协调者
        if not matched_roles:
            matched_roles.add(AgentRole.COORDINATOR)

        return list(matched_roles)

    def decompose(self, task: str) -> TaskDecomposition:
        """分解任务为子任务"""
        complexity = self.analyze_complexity(task)
        roles = self.identify_roles(task)

        subtasks = []
        execution_order = []

        if complexity == TaskComplexity.SIMPLE:
            # 简单任务：单个子任务
            subtask_id = str(uuid.uuid4())[:8]
            role = roles[0] if roles else AgentRole.COORDINATOR
            subtasks.append(
                SubTask(
                    id=subtask_id,
                    description=task,
                    assigned_role=role,
                )
            )
            execution_order.append(subtask_id)

        elif complexity == TaskComplexity.MODERATE:
            # 中等复杂度：按角色分解
            for _i, role in enumerate(roles):
                subtask_id = str(uuid.uuid4())[:8]
                subtasks.append(
                    SubTask(
                        id=subtask_id,
                        description=f"作为{self.templates[role].name}完成任务",
                        assigned_role=role,
                    )
                )
                execution_order.append(subtask_id)

        else:
            # 复杂任务：需要协调者 + 专业角色
            # 先添加协调任务
            coord_id = str(uuid.uuid4())[:8]
            subtasks.append(
                SubTask(
                    id=coord_id,
                    description="分解任务并协调各 Agent",
                    assigned_role=AgentRole.COORDINATOR,
                )
            )
            execution_order.append(coord_id)

            # 添加专业任务
            for role in roles:
                if role == AgentRole.COORDINATOR:
                    continue
                subtask_id = str(uuid.uuid4())[:8]
                subtasks.append(
                    SubTask(
                        id=subtask_id,
                        description=f"执行{self.templates[role].description}",
                        assigned_role=role,
                        dependencies=[coord_id],  # 依赖协调任务
                    )
                )
                execution_order.append(subtask_id)

            # 添加审核任务
            review_id = str(uuid.uuid4())[:8]
            subtasks.append(
                SubTask(
                    id=review_id,
                    description="审核并整合最终结果",
                    assigned_role=AgentRole.REVIEWER,
                    dependencies=[s.id for s in subtasks if s.id != coord_id],
                )
            )
            execution_order.append(review_id)

        return TaskDecomposition(
            original_task=task,
            complexity=complexity,
            subtasks=subtasks,
            execution_order=execution_order,
            estimated_agents=len(roles),
            reasoning=f"识别到 {len(roles)} 个角色需求，复杂度为 {complexity.value}",
        )


class AutoAgents:
    """自动 Agent 编排器

    借鉴 PraisonAI AutoAgents
    """

    def __init__(
        self,
        provider: Any,
        tools_registry: Any | None = None,
        templates: dict[AgentRole, AgentTemplate] | None = None,
        agent_factory: Callable | None = None,
    ):
        """
        Args:
            provider: LLM 提供商
            tools_registry: 工具注册表
            templates: Agent 模板字典
            agent_factory: Agent 工厂函数，用于创建 Agent 实例
        """
        self.provider = provider
        self.tools_registry = tools_registry
        self.templates = templates or DEFAULT_TEMPLATES
        self.agent_factory = agent_factory

        self.analyzer = TaskAnalyzer(self.templates)
        self._created_agents: dict[str, Any] = {}

    def create_agent(self, role: AgentRole) -> Any:
        """根据角色创建 Agent

        Args:
            role: Agent 角色

        Returns:
            Agent 实例
        """
        template = self.templates.get(role)
        if not template:
            raise ValueError(f"No template found for role: {role}")

        # 检查缓存
        cache_key = f"{role.value}_{template.name}"
        if cache_key in self._created_agents:
            return self._created_agents[cache_key]

        # 使用工厂函数创建
        if self.agent_factory:
            agent = self.agent_factory(template.create_agent_config())
        else:
            # 默认创建配置字典
            agent = template.create_agent_config()

        self._created_agents[cache_key] = agent
        return agent

    async def run(
        self,
        task: str,
        context: list[dict] | None = None,
        max_iterations: int = 25,
    ) -> AutoAgentResult:
        """运行自动编排

        Args:
            task: 任务描述
            context: 对话上下文
            max_iterations: 最大迭代次数

        Returns:
            AutoAgentResult: 编排结果
        """
        import time

        start_time = time.time()
        task_id = str(uuid.uuid4())[:8]

        result = AutoAgentResult(
            task_id=task_id,
            original_task=task,
        )

        try:
            # 1. 分解任务
            decomposition = self.analyzer.decompose(task)
            result.decomposition = decomposition

            logger.info(
                f"Task decomposed: {decomposition.complexity.value}, "
                f"{len(decomposition.subtasks)} subtasks"
            )

            # 2. 按顺序执行子任务
            completed_results: dict[str, str] = {}

            for subtask_id in decomposition.execution_order:
                subtask = next(s for s in decomposition.subtasks if s.id == subtask_id)

                # 检查依赖
                if subtask.dependencies:
                    pending = [
                        d for d in subtask.dependencies if d not in completed_results
                    ]
                    if pending:
                        logger.warning(
                            f"Subtask {subtask_id} has unmet dependencies: {pending}"
                        )
                        subtask.status = "failed"
                        continue

                # 创建 Agent
                try:
                    agent = self.create_agent(subtask.assigned_role)
                    subtask.assigned_agent = agent
                    subtask.status = "running"

                    # 执行子任务
                    subtask_result = await self._execute_subtask(
                        agent=agent,
                        subtask=subtask,
                        context=context,
                        previous_results=completed_results,
                        original_task=task,
                    )

                    subtask.result = subtask_result
                    subtask.status = "completed"
                    completed_results[subtask_id] = subtask_result

                    result.agent_results[subtask_id] = {
                        "role": subtask.assigned_role.value,
                        "description": subtask.description,
                        "result": subtask_result,
                    }

                except Exception as e:
                    logger.error(f"Subtask {subtask_id} failed: {e}")
                    subtask.status = "failed"
                    subtask.result = str(e)

            # 3. 生成最终结果
            if completed_results:
                result.final_result = self._aggregate_results(
                    task=task,
                    decomposition=decomposition,
                    results=completed_results,
                )

            result.total_time = time.time() - start_time
            return result

        except Exception as e:
            logger.error(f"AutoAgents run failed: {e}")
            result.error = str(e)
            result.total_time = time.time() - start_time
            return result

    async def _execute_subtask(
        self,
        agent: Any,
        subtask: SubTask,
        context: list[dict] | None,
        previous_results: dict[str, str],
        original_task: str = "",
    ) -> str:
        """执行单个子任务"""
        # 构建子任务提示
        prompt = f"任务: {subtask.description}"
        if original_task:
            prompt += f"\n\n原始任务: {original_task}"

        # 添加前置结果
        if previous_results:
            results_text = "\n".join(
                f"- {sid}: {result[:200]}..."
                for sid, result in previous_results.items()
            )
            prompt += f"\n\n前置任务结果:\n{results_text}"

        # 执行 Agent
        if hasattr(agent, "process_direct"):
            result = await agent.process_direct(
                message=prompt,
                context=context,
            )
            return str(result)
        elif hasattr(agent, "run"):
            result = agent.run(prompt)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        elif isinstance(agent, dict):
            # 如果是配置字典，返回模拟结果
            return f"[{agent.get('name', 'Agent')}] 处理完成: {subtask.description}"
        else:
            raise ValueError(f"Agent has no callable method: {type(agent)}")

    def _aggregate_results(
        self,
        task: str,
        decomposition: TaskDecomposition,
        results: dict[str, str],
    ) -> str:
        """聚合子任务结果"""
        if len(results) == 1:
            # 单个结果直接返回
            return list(results.values())[0]

        # 多个结果聚合
        lines = [f"任务: {task}", "", "执行结果:"]

        for subtask in decomposition.subtasks:
            if subtask.id in results:
                lines.append(f"\n## {self.templates[subtask.assigned_role].name}")
                lines.append(results[subtask.id])

        return "\n".join(lines)

    def get_created_agents(self) -> dict[str, Any]:
        """获取已创建的 Agent"""
        return dict(self._created_agents)

    def clear_agents(self) -> None:
        """清除缓存的 Agent"""
        self._created_agents.clear()


# ==================== 便捷函数 ====================


async def auto_run(
    task: str,
    provider: Any,
    tools_registry: Any | None = None,
    context: list[dict] | None = None,
) -> AutoAgentResult:
    """自动运行任务的便捷函数

    Args:
        task: 任务描述
        provider: LLM 提供商
        tools_registry: 工具注册表
        context: 对话上下文

    Returns:
        AutoAgentResult: 执行结果

    Example:
        result = await auto_run(
            task="研究 AI 趋势并写一份报告",
            provider=my_provider,
        )
        print(result.final_result)
    """
    auto_agents = AutoAgents(
        provider=provider,
        tools_registry=tools_registry,
    )
    return await auto_agents.run(task, context=context)
