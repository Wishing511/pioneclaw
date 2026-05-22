"""
UU.1 任务工具测试

覆盖：TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool,
      TaskStopTool, TaskOutputTool, TodoWriteTool, task_store
"""

import json

import pytest

from app.modules.tools.task_create import TaskCreateTool
from app.modules.tools.task_get import TaskGetTool
from app.modules.tools.task_list import TaskListTool
from app.modules.tools.task_output import TaskOutputTool
from app.modules.tools.task_stop import TaskStopTool
from app.modules.tools.task_update import TaskUpdateTool
from app.modules.tools.todo_write import TodoWriteTool

# ── Fixtures: 清理共享状态 ────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_task_store():
    """每个测试前后清理 _background_tasks"""
    from app.modules.tools.task_store import _background_tasks

    _background_tasks.clear()
    yield
    _background_tasks.clear()


@pytest.fixture(autouse=True)
def _clean_todo_store():
    """每个测试前后清理 _agent_todos"""
    from app.modules.tools.todo_write import _agent_todos

    _agent_todos.clear()
    yield
    _agent_todos.clear()


# ── 辅助函数 ──────────────────────────────────────────────────


def _create_test_task(
    task_id="test001", label="Test Task", tool_name="exec", status="pending"
):
    """直接在 task_store 中创建测试任务"""
    from app.modules.tools.task_store import create_task

    create_task(task_id, label, tool_name)
    if status != "pending":
        from app.modules.tools.task_store import update_task_status

        if status == "running":
            update_task_status(task_id, "running")
        elif status in ("done", "failed", "cancelled"):
            update_task_status(task_id, "running")
            update_task_status(task_id, status)


# ============================================================
# TaskCreateTool 测试
# ============================================================


class TestTaskCreateTool:
    """测试 TaskCreateTool"""

    @pytest.fixture
    def tool(self):
        return TaskCreateTool()

    @pytest.mark.asyncio
    async def test_create_success(self, tool):
        result = await tool.execute(
            label="My Task", tool_name="exec", args='{"command": "dir"}'
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "task_id" in data
        assert data["label"] == "My Task"
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_create_empty_label(self, tool):
        result = await tool.execute(label="")
        data = json.loads(result)
        assert data["success"] is False
        assert "不能为空" in data["error"]

    @pytest.mark.asyncio
    async def test_create_invalid_args_json(self, tool):
        result = await tool.execute(label="Bad Task", args="not json")
        data = json.loads(result)
        assert data["success"] is False
        assert "JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_create_with_parent(self, tool):
        result = await tool.execute(label="Subtask", parent_task_id="parent001")
        data = json.loads(result)
        assert data["success"] is True
        assert data["parent_task_id"] == "parent001"

    @pytest.mark.asyncio
    async def test_create_defaults(self, tool):
        result = await tool.execute(label="Default Task")
        data = json.loads(result)
        assert data["success"] is True
        assert data["tool_name"] == "spawn"

    def test_label_required(self, tool):
        assert "label" in tool.required

    def test_label_param_type(self, tool):
        assert tool.parameters["label"].type == "string"


# ============================================================
# TaskGetTool 测试
# ============================================================


class TestTaskGetTool:
    """测试 TaskGetTool"""

    @pytest.fixture
    def tool(self):
        return TaskGetTool()

    @pytest.mark.asyncio
    async def test_get_existing_task(self, tool):
        _create_test_task("task001", "Test Task")
        result = await tool.execute(task_id="task001")
        data = json.loads(result)
        assert data["success"] is True
        assert data["label"] == "Test Task"
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, tool):
        result = await tool.execute(task_id="nonexistent")
        data = json.loads(result)
        assert data["success"] is False
        assert "不存在" in data["error"]

    @pytest.mark.asyncio
    async def test_get_empty_task_id(self, tool):
        result = await tool.execute(task_id="")
        data = json.loads(result)
        assert data["success"] is False
        assert "不能为空" in data["error"]


# ============================================================
# TaskListTool 测试
# ============================================================


class TestTaskListTool:
    """测试 TaskListTool"""

    @pytest.fixture
    def tool(self):
        return TaskListTool()

    @pytest.mark.asyncio
    async def test_list_all(self, tool):
        _create_test_task("task001", "Task 1")
        _create_test_task("task002", "Task 2")
        result = await tool.execute()
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, tool):
        _create_test_task("task001", "Task 1")
        _create_test_task("task002", "Task 2", status="running")
        result = await tool.execute(status="running")
        data = json.loads(result)
        assert data["total"] == 1
        assert data["tasks"][0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_list_empty(self, tool):
        result = await tool.execute()
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_limit(self, tool):
        for i in range(10):
            _create_test_task(f"task{i:03d}", f"Task {i}")
        result = await tool.execute(limit=3)
        data = json.loads(result)
        assert len(data["tasks"]) <= 3


# ============================================================
# TaskUpdateTool 测试
# ============================================================


class TestTaskUpdateTool:
    """测试 TaskUpdateTool"""

    @pytest.fixture
    def tool(self):
        return TaskUpdateTool()

    @pytest.mark.asyncio
    async def test_update_status_valid(self, tool):
        _create_test_task("task001", "Task")
        result = await tool.execute(task_id="task001", status="running")
        data = json.loads(result)
        assert data["success"] is True
        assert data["new_status"] == "running"

    @pytest.mark.asyncio
    async def test_update_terminal_task_rejected(self, tool):
        _create_test_task("task001", "Task", status="done")
        result = await tool.execute(task_id="task001", status="running")
        data = json.loads(result)
        assert data["success"] is False
        assert "终态" in data["error"]

    @pytest.mark.asyncio
    async def test_update_invalid_transition(self, tool):
        _create_test_task("task001", "Task")  # pending
        result = await tool.execute(task_id="task001", status="done")
        data = json.loads(result)
        assert data["success"] is False
        assert "无效" in data["error"]

    @pytest.mark.asyncio
    async def test_update_progress(self, tool):
        _create_test_task("task001", "Task")
        result = await tool.execute(task_id="task001", progress=50)
        data = json.loads(result)
        assert data["success"] is True
        assert data["progress"] == 50

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, tool):
        result = await tool.execute(task_id="nonexistent")
        data = json.loads(result)
        assert data["success"] is False


# ============================================================
# TaskStopTool 测试
# ============================================================


class TestTaskStopTool:
    """测试 TaskStopTool"""

    @pytest.fixture
    def tool(self):
        return TaskStopTool()

    @pytest.mark.asyncio
    async def test_stop_running_task(self, tool):
        _create_test_task("task001", "Task", status="running")
        result = await tool.execute(task_id="task001")
        data = json.loads(result)
        assert data["success"] is True
        assert data["new_status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_stop_already_terminal(self, tool):
        _create_test_task("task001", "Task", status="done")
        result = await tool.execute(task_id="task001")
        data = json.loads(result)
        assert data["success"] is False
        assert "终态" in data["error"]

    @pytest.mark.asyncio
    async def test_stop_nonexistent(self, tool):
        result = await tool.execute(task_id="nonexistent")
        data = json.loads(result)
        assert data["success"] is False
        assert "不存在" in data["error"]


# ============================================================
# TaskOutputTool 测试
# ============================================================


class TestTaskOutputTool:
    """测试 TaskOutputTool"""

    @pytest.fixture
    def tool(self):
        return TaskOutputTool()

    @pytest.mark.asyncio
    async def test_output_done_task(self, tool):
        from app.modules.tools.task_store import get_task

        _create_test_task("task001", "Done Task", status="done")
        # 手动设置 result
        task = get_task("task001")
        task["result"] = "Task completed successfully"

        result = await tool.execute(task_id="task001")
        data = json.loads(result)
        assert data["success"] is True
        assert "Task completed" in data["output"]

    @pytest.mark.asyncio
    async def test_output_not_done_task(self, tool):
        _create_test_task("task001", "Running Task", status="running")
        result = await tool.execute(task_id="task001")
        data = json.loads(result)
        assert data["success"] is False
        assert "尚未完成" in data["error"]

    @pytest.mark.asyncio
    async def test_output_truncate(self, tool):
        from app.modules.tools.task_store import get_task

        _create_test_task("task001", "Task", status="done")
        task = get_task("task001")
        task["result"] = "A" * 200

        result = await tool.execute(task_id="task001", truncate=100)
        data = json.loads(result)
        assert data["success"] is True
        assert data["truncated"] is True
        assert len(data["output"]) <= 100 + len("...(已截断)")


# ============================================================
# TodoWriteTool 测试
# ============================================================


class TestTodoWriteTool:
    """测试 TodoWriteTool"""

    @pytest.fixture
    def tool(self):
        return TodoWriteTool()

    def test_todos_parameter_required(self, tool):
        assert "todos" in tool.required

    @pytest.mark.asyncio
    async def test_create_todos(self, tool):
        todos = json.dumps(
            [
                {"id": "1", "subject": "Fix bug", "status": "in_progress"},
                {"id": "2", "subject": "Add feature", "status": "pending"},
            ]
        )
        result = await tool.execute(todos=todos)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 2
        assert data["by_status"]["in_progress"] == 1
        assert data["by_status"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_update_existing_todos(self, tool):
        # First create
        todos = json.dumps([{"id": "1", "subject": "Task A", "status": "pending"}])
        await tool.execute(todos=todos)

        # Then update
        todos2 = json.dumps(
            [
                {"id": "1", "subject": "Task A", "status": "completed"},
                {"id": "2", "subject": "Task B", "status": "in_progress"},
            ]
        )
        result = await tool.execute(todos=todos2)
        data = json.loads(result)
        assert data["total"] == 2
        assert data["by_status"]["completed"] == 1

    @pytest.mark.asyncio
    async def test_clear_todos(self, tool):
        todos = json.dumps([{"id": "1", "subject": "Task", "status": "pending"}])
        await tool.execute(todos=todos)

        result = await tool.execute(todos="[]")
        data = json.loads(result)
        assert data["success"] is True
        assert data["todos"] == []

    @pytest.mark.asyncio
    async def test_invalid_json(self, tool):
        result = await tool.execute(todos="not json")
        data = json.loads(result)
        assert data["success"] is False
        assert "JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_missing_id_field(self, tool):
        todos = json.dumps([{"subject": "No ID", "status": "pending"}])
        result = await tool.execute(todos=todos)
        data = json.loads(result)
        assert data["success"] is False
        assert "id" in data["error"]

    @pytest.mark.asyncio
    async def test_invalid_status(self, tool):
        todos = json.dumps([{"id": "1", "subject": "Bad status", "status": "unknown"}])
        result = await tool.execute(todos=todos)
        data = json.loads(result)
        assert data["success"] is False
        assert "status" in data["error"]

    @pytest.mark.asyncio
    async def test_session_isolation(self, tool):
        todos_a = json.dumps([{"id": "1", "subject": "Session A", "status": "pending"}])
        todos_b = json.dumps(
            [{"id": "2", "subject": "Session B", "status": "completed"}]
        )

        await tool.execute(todos=todos_a, session_id="session-a")
        await tool.execute(todos=todos_b, session_id="session-b")

        # Verify isolation
        from app.modules.tools.todo_write import _agent_todos

        assert len(_agent_todos["session-a"]) == 1
        assert _agent_todos["session-a"][0]["subject"] == "Session A"
        assert len(_agent_todos["session-b"]) == 1
        assert _agent_todos["session-b"][0]["subject"] == "Session B"


# ============================================================
# TaskStore 单元测试
# ============================================================


class TestTaskStoreUnit:
    """测试 task_store 状态转换"""

    def test_valid_transition_pending_to_running(self):
        from app.modules.tools.task_store import create_task, update_task_status

        create_task("t1", "Task")
        ok = update_task_status("t1", "running")
        assert ok is True

    def test_invalid_transition_pending_to_done(self):
        from app.modules.tools.task_store import create_task, update_task_status

        create_task("t2", "Task")
        ok = update_task_status("t2", "done")
        assert ok is False

    def test_terminal_no_update(self):
        from app.modules.tools.task_store import create_task, update_task_status

        create_task("t3", "Task")
        update_task_status("t3", "running")
        update_task_status("t3", "done")
        # done is terminal, can't change
        ok = update_task_status("t3", "running")
        assert ok is False

    def test_get_task_count(self):
        from app.modules.tools.task_store import create_task, get_task_count

        create_task("t1", "Task 1")
        create_task("t2", "Task 2")
        stats = get_task_count()
        assert stats["total"] == 2

    def test_remove_task(self):
        from app.modules.tools.task_store import create_task, get_task, remove_task

        create_task("t1", "Task")
        assert remove_task("t1") is True
        assert get_task("t1") is None
        assert remove_task("nonexistent") is False
