"""
工作流增强测试
- Graph DAG 并行调度
- Council 3轮评审
- 环检测
- 条件执行
"""

import pytest

from app.modules.agent.workflow import (
    AgentSlot,
    SlotPhase,
    WorkflowEngine,
    WorkflowMode,
)


class MockAgentLoop:
    """模拟 AgentLoop"""

    def __init__(self, responses=None):
        self.responses = responses or []
        self._call_count = 0

    async def process_direct(self, message, system_prompt=None, model_override=None):
        if self._call_count < len(self.responses):
            result = self.responses[self._call_count]
            self._call_count += 1
            return result
        return f"Mock response {self._call_count}"


class TestWorkflowMode:
    """测试工作流模式"""

    def test_workflow_modes(self):
        """测试工作流模式枚举"""
        assert WorkflowMode.PIPELINE.value == "pipeline"
        assert WorkflowMode.GRAPH.value == "graph"
        assert WorkflowMode.COUNCIL.value == "council"


class TestSlotPhase:
    """测试节点状态"""

    def test_slot_phases(self):
        """测试节点状态枚举"""
        assert SlotPhase.WAITING.value == "waiting"
        assert SlotPhase.ACTIVE.value == "active"
        assert SlotPhase.DONE.value == "done"
        assert SlotPhase.FAILED.value == "failed"


class TestAgentSlot:
    """测试智能体节点"""

    def test_slot_creation(self):
        """测试节点创建"""
        slot = AgentSlot(
            slot_id="research",
            label="研究员",
            prompt_template="研究这个问题",
            depends_on=["data"],
        )
        assert slot.slot_id == "research"
        assert slot.phase == SlotPhase.WAITING
        assert slot.output is None
        assert not slot.skipped


class TestCycleDetection:
    """测试环检测"""

    def test_no_cycle(self):
        """测试无环图"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        dep_map = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        assert engine._detect_cycle(dep_map) is False

    def test_cycle_detected(self):
        """测试检测到环"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        dep_map = {
            "a": ["b"],
            "b": ["c"],
            "c": ["a"],
        }
        assert engine._detect_cycle(dep_map) is True

    def test_self_cycle(self):
        """测试自环"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        dep_map = {
            "a": ["a"],
        }
        assert engine._detect_cycle(dep_map) is True


class TestConditionEvaluation:
    """测试条件执行"""

    def test_no_condition(self):
        """测试无条件"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)
        assert engine._evaluate_condition(None, {}) is True

    def test_output_contains(self):
        """测试输出包含条件"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        slot_map = {
            "a": AgentSlot(
                slot_id="a", label="A", prompt_template="T", output="Hello world"
            ),
        }
        condition = {"type": "output_contains", "node": "a", "text": "world"}
        assert engine._evaluate_condition(condition, slot_map) is True

    def test_output_not_contains(self):
        """测试输出不包含条件"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        slot_map = {
            "a": AgentSlot(slot_id="a", label="A", prompt_template="T", output="Hello"),
        }
        condition = {"type": "output_not_contains", "node": "a", "text": "world"}
        assert engine._evaluate_condition(condition, slot_map) is True

    def test_condition_missing_node(self):
        """测试引用不存在的节点"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        condition = {"type": "output_contains", "node": "nonexistent", "text": "x"}
        assert engine._evaluate_condition(condition, {}) is False


class TestPipeline:
    """测试 Pipeline 模式"""

    @pytest.mark.asyncio
    async def test_pipeline_execution(self):
        """测试流水线执行"""
        loop = MockAgentLoop(
            responses=[
                "第一步结果",
                "第二步结果",
            ]
        )
        engine = WorkflowEngine(loop)

        result = await engine.run_pipeline(
            goal="完成一项任务",
            stages=[
                {"role": "分析师", "task": "分析数据"},
                {"role": "撰写者", "task": "撰写报告"},
            ],
        )

        assert "Pipeline" in result
        assert "分析师" in result
        assert "撰写者" in result

    @pytest.mark.asyncio
    async def test_pipeline_empty_stages(self):
        """测试空阶段"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_pipeline(goal="test", stages=[])
        assert "No pipeline stages" in result


class TestGraph:
    """测试 Graph 模式"""

    @pytest.mark.asyncio
    async def test_graph_linear(self):
        """测试线性依赖图"""
        loop = MockAgentLoop(
            responses=[
                "数据收集结果",
                "分析结果",
            ]
        )
        engine = WorkflowEngine(loop)

        result = await engine.run_graph(
            goal="数据流水线",
            slots=[
                {"id": "collect", "role": "收集器", "task": "收集数据"},
                {
                    "id": "analyze",
                    "role": "分析器",
                    "task": "分析数据",
                    "depends_on": ["collect"],
                },
            ],
        )

        assert "Graph" in result
        assert "收集器" in result
        assert "分析器" in result

    @pytest.mark.asyncio
    async def test_graph_parallel(self):
        """测试并行执行"""
        call_order = []

        class OrderTrackingLoop:
            async def process_direct(
                self, message, system_prompt=None, model_override=None
            ):
                call_order.append(message[:20])
                return f"Result for {message[:20]}"

        loop = OrderTrackingLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_graph(
            goal="并行分析",
            slots=[
                {"id": "a", "role": "A", "task": "任务A"},
                {"id": "b", "role": "B", "task": "任务B"},
                {"id": "c", "role": "C", "task": "汇总", "depends_on": ["a", "b"]},
            ],
        )

        assert "Graph" in result

    @pytest.mark.asyncio
    async def test_graph_cycle_detected(self):
        """测试环检测"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_graph(
            goal="有环的图",
            slots=[
                {"id": "a", "role": "A", "task": "任务A", "depends_on": ["b"]},
                {"id": "b", "role": "B", "task": "任务B", "depends_on": ["a"]},
            ],
        )

        assert "环路" in result or "Error" in result

    @pytest.mark.asyncio
    async def test_graph_missing_dependency(self):
        """测试缺失依赖"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_graph(
            goal="缺失依赖",
            slots=[
                {
                    "id": "a",
                    "role": "A",
                    "task": "任务A",
                    "depends_on": ["nonexistent"],
                },
            ],
        )

        assert "Error" in result

    @pytest.mark.asyncio
    async def test_graph_empty_slots(self):
        """测试空节点"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_graph(goal="test", slots=[])
        assert "No graph slots" in result


class TestCouncil:
    """测试 Council 模式"""

    @pytest.mark.asyncio
    async def test_council_with_cross_review(self):
        """测试 3轮交叉评审"""
        loop = MockAgentLoop(
            responses=[
                # 第1轮：2个成员初始立场
                "技术视角分析",
                "业务视角分析",
                # 第2轮：2个成员交叉评审
                "技术视角评审",
                "业务视角评审",
                # 第3轮：综合
                "综合分析结果",
            ]
        )
        engine = WorkflowEngine(loop)

        result = await engine.run_council(
            question="是否采用微服务架构？",
            members=[
                {"id": "tech", "perspective": "技术视角"},
                {"id": "biz", "perspective": "业务视角"},
            ],
            cross_review=True,
        )

        assert "评审" in result
        assert "技术视角" in result
        assert "业务视角" in result
        assert "综合分析" in result
        assert "3 轮" in result

    @pytest.mark.asyncio
    async def test_council_no_cross_review(self):
        """测试独立模式（无交叉评审）"""
        loop = MockAgentLoop(
            responses=[
                "技术视角分析",
                "业务视角分析",
            ]
        )
        engine = WorkflowEngine(loop)

        result = await engine.run_council(
            question="是否采用微服务架构？",
            members=[
                {"id": "tech", "perspective": "技术视角"},
                {"id": "biz", "perspective": "业务视角"},
            ],
            cross_review=False,
        )

        assert "独立模式" in result

    @pytest.mark.asyncio
    async def test_council_empty_members(self):
        """测试空成员"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)

        result = await engine.run_council(question="test", members=[])
        assert "No council members" in result


class TestExecutionMetadata:
    """测试执行元数据"""

    def test_build_exec_metadata_empty(self):
        """测试空执行数据"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)
        assert engine._build_exec_metadata() == ""

    def test_build_exec_metadata_with_data(self):
        """测试有执行数据"""
        loop = MockAgentLoop()
        engine = WorkflowEngine(loop)
        engine._execution_data = {"agent1": {"label": "Test", "result": "Done"}}
        metadata = engine._build_exec_metadata()
        assert "WORKFLOW_EXEC" in metadata
        assert "agent1" in metadata
