"""
WorkflowEngine - 多智能体编排引擎

借鉴自 CountBot 的 workflow.py

执行模式：
  pipeline - 顺序执行（上下文传递）
  graph    - 依赖 DAG（自动并行）
  council  - 多视角审议（立场→评审→合成）
"""

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowMode(Enum):
    """工作流模式"""

    PIPELINE = "pipeline"
    GRAPH = "graph"
    COUNCIL = "council"


class SlotPhase(Enum):
    """节点状态"""

    WAITING = "waiting"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass
class AgentSlot:
    """智能体在工作流图中的节点"""

    slot_id: str
    label: str
    prompt_template: str
    depends_on: list[str] = field(default_factory=list)
    condition: dict | None = None  # 可选：执行条件
    phase: SlotPhase = SlotPhase.WAITING
    output: str | None = None
    error: str | None = None
    skipped: bool = False  # 是否因条件不满足而跳过


class WorkflowEngine:
    """
    多智能体工作流引擎

    编排 Pipeline / Graph / Council 三种执行模式
    """

    def __init__(
        self,
        agent_loop,  # AgentLoop 实例
        session_id: str | None = None,
        cancel_token=None,
        model_override: dict[str, Any] | None = None,
        event_callback=None,
        taskflow_db=None,  # AsyncSession，用于 TaskFlow 持久化
    ) -> None:
        """
        初始化 WorkflowEngine

        Args:
            agent_loop: AgentLoop 实例，用于执行单个 Agent
            session_id: 会话 ID
            cancel_token: 取消令牌
            model_override: 模型配置覆盖
            event_callback: 事件回调函数
            taskflow_db: 可选 AsyncSession，绑定后工作流执行会持久化到 TaskFlow
        """
        self._agent_loop = agent_loop
        self._session_id = session_id
        self._cancel_token = cancel_token
        self._model_override = model_override
        self._event_callback = event_callback
        self._execution_data: dict[str, dict] = {}
        self._taskflow_db = taskflow_db
        self._taskflow_id: str | None = None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def bind_taskflow(
        self, name: str, goal: str, owner_id: str | None = None
    ) -> str | None:
        """绑定 TaskFlow 持久化（可选）

        Returns:
            flow_id 或 None（无 db 时）
        """
        if not self._taskflow_db:
            return None
        from app.modules.agent.taskflow import TaskFlowManager

        mgr = TaskFlowManager(self._taskflow_db)
        flow = await mgr.create(
            name=name,
            goal=goal,
            owner_id=owner_id,
            session_id=self._session_id,
        )
        self._taskflow_id = flow.id
        return flow.id

    async def _taskflow_step(
        self, step_name: str, step_result: dict | None = None
    ) -> None:
        """持久化步骤到 TaskFlow"""
        if not self._taskflow_id or not self._taskflow_db:
            return
        try:
            from app.modules.agent.taskflow import TaskFlowManager

            mgr = TaskFlowManager(self._taskflow_db)
            await mgr.run_step(self._taskflow_id, step_name, step_result)
        except Exception as exc:
            logger.warning(f"[Workflow] TaskFlow step persist failed: {exc}")

    async def _taskflow_finish(self, final_result: dict | None = None) -> None:
        """完成 TaskFlow"""
        if not self._taskflow_id or not self._taskflow_db:
            return
        try:
            from app.modules.agent.taskflow import TaskFlowManager

            mgr = TaskFlowManager(self._taskflow_db)
            await mgr.finish(self._taskflow_id, final_result)
        except Exception as exc:
            logger.warning(f"[Workflow] TaskFlow finish persist failed: {exc}")

    async def _taskflow_fail(self, error: str) -> None:
        """标记 TaskFlow 失败"""
        if not self._taskflow_id or not self._taskflow_db:
            return
        try:
            from app.modules.agent.taskflow import TaskFlowManager

            mgr = TaskFlowManager(self._taskflow_db)
            await mgr.fail(self._taskflow_id, error)
        except Exception as exc:
            logger.warning(f"[Workflow] TaskFlow fail persist failed: {exc}")

    async def _emit_event(self, event_type: str, **data: Any) -> None:
        """推送工作流事件"""
        if self._event_callback:
            try:
                maybe_result = self._event_callback(event_type, data)
                if inspect.isawaitable(maybe_result):
                    await maybe_result
            except Exception as exc:
                logger.warning(
                    f"[Workflow] Event callback failed ({event_type}): {exc}"
                )

    def _is_cancelled(self) -> bool:
        """检查是否已取消"""
        return bool(self._cancel_token and self._cancel_token.is_cancelled)

    async def _invoke_agent(
        self,
        prompt: str,
        label: str = "",
        system_prompt: str | None = None,
        agent_id: str = "",
    ) -> str:
        """执行单个 Agent 并返回输出"""
        if self._is_cancelled():
            logger.info(
                f"[Workflow] Workflow cancelled, stopping agent '{agent_id or label}'"
            )
            raise asyncio.CancelledError("Workflow cancelled before agent start")

        short_label = label or (prompt[:40] + ("..." if len(prompt) > 40 else ""))
        aid = agent_id or short_label

        self._execution_data[aid] = {
            "label": label or aid,
            "result": "",
        }

        await self._emit_event(
            "workflow_agent_start",
            agent_id=aid,
            agent_label=label or aid,
        )

        try:
            # 使用 AgentLoop 执行
            result = await self._agent_loop.process_direct(
                message=prompt,
                system_prompt=system_prompt,
                model_override=self._model_override,
            )

            self._execution_data[aid]["result"] = result

            await self._emit_event(
                "workflow_agent_complete",
                agent_id=aid,
                agent_label=label or aid,
                result=result,
            )

            return result

        except Exception as e:
            logger.error(f"[Workflow] Agent '{aid}' failed: {e}")
            raise

    def _detect_cycle(self, dep_map: dict[str, list[str]]) -> bool:
        """检测依赖图环路"""
        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node: str) -> bool:
            visited.add(node)
            in_stack.add(node)
            for parent in dep_map.get(node, []):
                if parent not in visited:
                    if _dfs(parent):
                        return True
                elif parent in in_stack:
                    return True
            in_stack.discard(node)
            return False

        return any(_dfs(n) for n in dep_map if n not in visited)

    def _evaluate_condition(
        self, condition: dict, slot_map: dict[str, AgentSlot]
    ) -> bool:
        """评估节点执行条件"""
        if not condition:
            return True

        cond_type = condition.get("type")
        node_id = condition.get("node")
        text = condition.get("text", "")

        if not node_id or node_id not in slot_map:
            return False

        output = slot_map[node_id].output or ""

        if cond_type == "output_contains":
            return text in output
        elif cond_type == "output_not_contains":
            return text not in output

        return True

    def _build_exec_metadata(self) -> str:
        """序列化执行数据为 HTML 注释"""
        if not self._execution_data:
            return ""
        try:
            payload = json.dumps(self._execution_data, ensure_ascii=False)
            return f"\n\n<!--WORKFLOW_EXEC:{payload}:WORKFLOW_EXEC-->"
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Pipeline 模式
    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        goal: str,
        stages: list[dict[str, Any]],
    ) -> str:
        """
        顺序流水线，每个阶段继承前序输出

        Args:
            goal: 工作流目标
            stages: 阶段列表，每个阶段包含 role、task、system_prompt

        Returns:
            str: 汇总结果
        """
        if not stages:
            return "No pipeline stages defined."

        # 启动 TaskFlow
        if self._taskflow_id and self._taskflow_db:
            try:
                from app.modules.agent.taskflow import TaskFlowManager

                mgr = TaskFlowManager(self._taskflow_db)
                await mgr.start(self._taskflow_id, initial_step="pipeline:stage-0")
            except Exception:
                pass

        accumulated: str = ""
        stage_outputs: list[dict] = []

        for idx, stage in enumerate(stages):
            if self._is_cancelled():
                logger.info("[Workflow/Pipeline] 用户取消，终止流水线")
                break

            role = stage.get("role", f"Stage-{idx + 1}")
            task_desc = stage.get("task", "")
            custom_sp = stage.get("system_prompt")

            prior_ctx = f"\n\n## 前序阶段输出:\n{accumulated}" if accumulated else ""
            prompt = (
                f"# 工作流目标\n{goal}\n\n"
                f"# 你的任务\n{task_desc}"
                f"{prior_ctx}\n\n"
                "请完成任务并给出清晰、详细的输出。"
            )
            system_prompt = custom_sp or (
                f"你是 {role}。"
                f"你正在参与一个多智能体流水线工作流。"
                f"你的职责是：{task_desc}。"
                "专注于你的任务，给出完整、精确的结果。"
            )

            logger.info(f"[Workflow/Pipeline] Stage {idx + 1}/{len(stages)}: {role}")
            output = await self._invoke_agent(
                prompt,
                label=role,
                system_prompt=system_prompt,
                agent_id=stage.get("id", role),
            )

            stage_outputs.append({"role": role, "output": output})
            accumulated += f"\n### {role}:\n{output}"
            await self._taskflow_step(
                f"pipeline:stage-{idx + 1}", {"role": role, "output": output[:200]}
            )

        # 完成 TaskFlow
        await self._taskflow_finish({"stages": len(stage_outputs)})

        lines = [f"# Pipeline 工作流结果\n\n**目标:** {goal}\n"]
        for entry in stage_outputs:
            lines.append(f"## {entry['role']}\n\n{entry['output']}")
        return "\n\n---\n\n".join(lines) + self._build_exec_metadata()

    # ------------------------------------------------------------------
    # Graph 模式
    # ------------------------------------------------------------------

    async def run_graph(
        self,
        goal: str,
        slots: list[dict[str, Any]],
    ) -> str:
        """
        依赖 DAG，自动并行调度

        Args:
            goal: 工作流目标
            slots: 节点列表，每个节点包含 id、role、task、depends_on、condition

        Returns:
            str: 汇总结果
        """
        if not slots:
            return "No graph slots defined."

        # 启动 TaskFlow
        if self._taskflow_id and self._taskflow_db:
            try:
                from app.modules.agent.taskflow import TaskFlowManager

                mgr = TaskFlowManager(self._taskflow_db)
                await mgr.start(self._taskflow_id, initial_step="graph:init")
            except Exception:
                pass

        slot_system_prompts: dict[str, str] = {}
        slot_map: dict[str, AgentSlot] = {}
        dep_map: dict[str, list[str]] = {}

        for s in slots:
            sid = s.get("id", "")
            if not sid:
                return "Error: 每个节点必须有 'id' 字段。"

            deps = s.get("depends_on", s.get("depends", []))
            role = s.get("role", sid)
            task_desc = s.get("task", "")
            custom_sp = s.get("system_prompt")
            condition = s.get("condition")

            slot_system_prompts[sid] = custom_sp or (
                f"你是 {role}。"
                "你是一个依赖图工作流中的专家智能体。"
                f"你的职责是：{task_desc}。"
                "给出完整、精确的结果。"
            )
            slot_map[sid] = AgentSlot(
                slot_id=sid,
                label=role,
                prompt_template=task_desc,
                depends_on=list(deps),
                condition=condition,
            )
            dep_map[sid] = list(deps)

        # 验证依赖
        for sid, slot in slot_map.items():
            for dep in slot.depends_on:
                if dep not in slot_map:
                    return f"Error: 节点 '{sid}' 依赖未知节点 '{dep}'。"

        # 检测环路
        if self._detect_cycle(dep_map):
            return "Error: 依赖图包含环路。"

        # 执行调度
        while any(s.phase == SlotPhase.WAITING for s in slot_map.values()):
            if self._is_cancelled():
                logger.info("[Workflow/Graph] 用户取消，终止依赖图调度")
                break

            # 找出可执行的节点
            ready = [
                s
                for s in slot_map.values()
                if s.phase == SlotPhase.WAITING
                and all(
                    slot_map[d].phase == SlotPhase.DONE or slot_map[d].skipped
                    for d in s.depends_on
                )
            ]

            if not ready:
                # 检查是否有节点被上游失败阻塞
                for s in slot_map.values():
                    if s.phase == SlotPhase.WAITING and any(
                        slot_map[d].phase == SlotPhase.FAILED for d in s.depends_on
                    ):
                        s.phase = SlotPhase.FAILED
                        s.error = "Blocked by upstream failure"
                break

            # 评估条件
            to_execute = []
            for s in ready:
                if self._evaluate_condition(s.condition, slot_map):
                    to_execute.append(s)
                else:
                    s.phase = SlotPhase.DONE
                    s.skipped = True
                    s.output = "[跳过: 条件不满足]"
                    logger.info(
                        f"[Workflow/Graph] 节点 '{s.slot_id}' 跳过（条件不满足）"
                    )

            if not to_execute:
                continue

            # 标记为活跃
            for s in to_execute:
                s.phase = SlotPhase.ACTIVE

            logger.info(
                f"[Workflow/Graph] 并行调度 {len(to_execute)} 个节点: "
                f"{[s.slot_id for s in to_execute]}"
            )

            # 并行执行
            async def _run_slot(slot: AgentSlot) -> None:
                dep_ctx = ""
                if slot.depends_on:
                    dep_parts = [
                        f"### {slot_map[d].label}:\n{slot_map[d].output}"
                        for d in slot.depends_on
                        if slot_map[d].output and not slot_map[d].skipped
                    ]
                    if dep_parts:
                        dep_ctx = "\n\n## 上游节点输出:\n" + "\n\n".join(dep_parts)

                prompt = (
                    f"# 工作流目标\n{goal}\n\n"
                    f"# 你的任务\n{slot.prompt_template}"
                    f"{dep_ctx}\n\n"
                    "请完成任务并给出清晰、详细的输出。"
                )
                try:
                    slot.output = await self._invoke_agent(
                        prompt,
                        label=slot.label,
                        system_prompt=slot_system_prompts.get(slot.slot_id),
                        agent_id=slot.slot_id,
                    )
                    slot.phase = SlotPhase.DONE
                except Exception as exc:
                    slot.phase = SlotPhase.FAILED
                    slot.error = str(exc)
                    logger.error(f"[Workflow/Graph] 节点 '{slot.slot_id}' 失败: {exc}")

            await asyncio.gather(*[_run_slot(s) for s in to_execute])

        # 汇总结果
        await self._taskflow_finish({"slots": len(slot_map)})
        lines = [f"# Graph 工作流结果\n\n**目标:** {goal}\n"]
        for slot in slot_map.values():
            if slot.skipped:
                icon = "⏭️"
                status_text = "跳过"
            elif slot.phase == SlotPhase.DONE:
                icon = "✅"
                status_text = "完成"
            else:
                icon = "❌"
                status_text = "失败"

            lines.append(f"## {icon} {slot.label} ({status_text})")
            if slot.output:
                lines.append(slot.output)
            elif slot.error:
                lines.append(f"*错误: {slot.error}*")

        return "\n\n---\n\n".join(lines) + self._build_exec_metadata()

    # ------------------------------------------------------------------
    # Council 模式
    # ------------------------------------------------------------------

    async def run_council(
        self,
        question: str,
        members: list[dict[str, Any]],
        cross_review: bool = True,
    ) -> str:
        """
        多视角评审：立场陈述 → [可选]交叉评审 → 综合输出

        Args:
            question: 待评审的问题
            members: 成员列表，每个成员包含 id、perspective、system_prompt
            cross_review: 是否进行交叉评审

        Returns:
            str: 汇总结果
        """
        if not members:
            return "No council members defined."

        # 启动 TaskFlow
        if self._taskflow_id and self._taskflow_db:
            try:
                from app.modules.agent.taskflow import TaskFlowManager

                mgr = TaskFlowManager(self._taskflow_db)
                await mgr.start(self._taskflow_id, initial_step="council:round-1")
            except Exception:
                pass

        member_map: dict[str, str] = {
            m["id"]: m.get("perspective", "neutral analyst") for m in members
        }
        member_system_prompts: dict[str, str] = {}

        for m in members:
            mid = m["id"]
            perspective = member_map[mid]
            custom_sp = m.get("system_prompt")
            member_system_prompts[mid] = custom_sp or (
                f"你是评审委员会成员，代表视角：{perspective}。"
                "你从该视角严谨分析问题，用证据支持你的立场。"
            )

        # 第1轮：初始立场
        async def _initial(member: dict) -> tuple[str, str]:
            mid = member["id"]
            perspective = member_map[mid]
            prompt = (
                f"# 评审问题\n{question}\n\n"
                f"# 你的视角\n{perspective}\n\n"
                "请从你的视角深入分析这个问题，给出有理有据的详细回答。"
            )
            logger.info(f"[Workflow/Council] 第1轮: {mid}")
            result = await self._invoke_agent(
                prompt,
                label=f"{perspective} — 第1轮",
                system_prompt=member_system_prompts[mid],
                agent_id=f"{mid}:R1",
            )
            return mid, result

        round1: dict[str, str] = dict(
            await asyncio.gather(*[_initial(m) for m in members])
        )

        if self._is_cancelled():
            logger.info("[Workflow/Council] 用户取消，终止于第1轮完成后")
            return "Workflow cancelled after round 1."

        # 是否进行交叉评审
        if not cross_review:
            logger.info("[Workflow/Council] 独立模式，跳过交叉评审")
            blocks = []
            for m in members:
                mid = m["id"]
                persp = member_map[mid]
                blocks.append(f"### {persp}\n\n{round1.get(mid, '')}")

            body = "\n\n---\n\n".join(blocks)
            return (
                f"# 多视角分析结果（独立模式）\n\n"
                f"**议题：** {question}\n\n"
                f"## 各成员独立分析\n\n"
                f"{body}\n\n"
                f"---\n\n"
                f"*分析完成 — 共 {len(members)} 位成员独立分析，无交叉评审。*"
            ) + self._build_exec_metadata()

        # 第2轮：交叉评审
        async def _cross_review(member: dict) -> tuple[str, str]:
            mid = member["id"]
            perspective = member_map[mid]
            others = "\n\n".join(
                f"**{member_map[oid]} ({oid}):**\n{pos}"
                for oid, pos in round1.items()
                if oid != mid
            )
            prompt = (
                f"# 评审问题\n{question}\n\n"
                f"# 你的视角\n{perspective}\n\n"
                f"# 你的初始立场\n{round1[mid]}\n\n"
                f"# 其他成员的立场\n{others}\n\n"
                "请仔细评审其他成员的立场。"
                "你同意或不同意他们的分析？"
                "他们遗漏了什么或做对了什么？"
                "根据他们的输入完善或捍卫你的立场。"
            )
            logger.info(f"[Workflow/Council] 第2轮交叉评审: {mid}")
            result = await self._invoke_agent(
                prompt,
                label=f"{perspective} — 交叉评审",
                system_prompt=member_system_prompts[mid],
                agent_id=f"{mid}:R2",
            )
            return mid, result

        round2: dict[str, str] = dict(
            await asyncio.gather(*[_cross_review(m) for m in members])
        )

        # 汇总结果（第3轮：综合）
        # 综合所有成员的立场和评审，给出最终结论
        all_perspectives = "\n\n".join(
            f"**{member_map[mid]} ({mid}):**\n\n"
            f"初始立场：{round1.get(mid, '')}\n\n"
            f"交叉评审：{round2.get(mid, '')}"
            for mid in member_map
        )

        synthesis_prompt = (
            f"# 评审问题\n{question}\n\n"
            f"# 所有成员的立场与评审\n{all_perspectives}\n\n"
            "请综合所有成员的观点，给出一份全面、平衡的最终分析报告。"
            "指出各成员的关键贡献、共识点和分歧点，"
            "并给出你的综合建议。"
        )
        logger.info("[Workflow/Council] 第3轮: 综合分析")

        synthesis = await self._invoke_agent(
            synthesis_prompt,
            label="综合分析",
            system_prompt="你是一位公正的评审委员会主席。"
            "你的职责是综合所有成员的观点，"
            "给出一份全面、客观的最终分析报告。",
            agent_id="council:synthesis",
        )

        blocks = []
        for m in members:
            mid = m["id"]
            persp = member_map[mid]
            blocks.append(
                f"### {persp}\n\n"
                f"**第1轮立场：**\n\n{round1.get(mid, '')}\n\n"
                f"**交叉评审：**\n\n{round2.get(mid, '')}"
            )

        body = "\n\n---\n\n".join(blocks)
        await self._taskflow_finish(
            {"members": len(members), "rounds": 3 if cross_review else 1}
        )
        return (
            f"# 多视角评审结果（3轮交叉模式）\n\n"
            f"**议题：** {question}\n\n"
            f"## 成员立场与评审\n\n"
            f"{body}\n\n"
            f"---\n\n"
            f"## 综合分析\n\n{synthesis}\n\n"
            f"---\n\n"
            f"*评审完成 — 共 {len(members)} 位成员，3 轮讨论（立场→评审→综合）。*"
        ) + self._build_exec_metadata()
