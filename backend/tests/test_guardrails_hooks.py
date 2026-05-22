"""
阶段 CC 测试 — Guardrails 输出验证 + Tool Hooks 工具拦截

覆盖：
- GuardrailConfig 配置
- Guardrail 验证（LLM + 函数）
- GuardrailExecutor 重试逻辑
- builtin_validators 预置验证器
- HookEvent 事件类型
- ToolHook 创建和配置
- ToolHookRunner 执行流程
- builtin_hooks 预置 Hooks
- AgentLoop Guardrails 集成
- AgentLoop Tool Hooks 集成
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agent.guardrails import (
    Guardrail,
    GuardrailConfig,
    GuardrailExecutor,
    GuardrailFailedError,
    ValidationResult,
    builtin_validators,
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

# ==================== GuardrailConfig 测试 ====================


class TestGuardrailConfig:
    def test_defaults(self):
        config = GuardrailConfig(validator=lambda x: True)
        assert config.max_retries == 3
        assert config.on_failure == "retry"
        assert config.default_value is None
        assert config.description == ""

    def test_custom_values(self):
        config = GuardrailConfig(
            validator="must be JSON",
            max_retries=5,
            on_failure="default_value",
            default_value={"error": "default"},
            description="JSON validator",
        )
        assert config.validator == "must be JSON"
        assert config.max_retries == 5
        assert config.on_failure == "default_value"
        assert config.default_value == {"error": "default"}


# ==================== ValidationResult 测试 ====================


class TestValidationResult:
    def test_valid_result(self):
        result = ValidationResult(valid=True, reason="OK")
        assert result.valid is True
        assert result.reason == "OK"
        assert result.details is None

    def test_invalid_result(self):
        result = ValidationResult(
            valid=False,
            reason="Missing required field",
            details={"missing": ["name", "email"]},
        )
        assert result.valid is False
        assert "Missing" in result.reason
        assert result.details["missing"] == ["name", "email"]


# ==================== Guardrail 函数验证测试 ====================


class TestGuardrailFunctionValidation:
    @pytest.mark.asyncio
    async def test_bool_validator_true(self):
        def validator(output):
            return True

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        result = await guardrail.validate("some output")
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_bool_validator_false(self):
        def validator(output):
            return False

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        result = await guardrail.validate("some output")
        assert result.valid is False

    @pytest.mark.asyncio
    async def test_tuple_validator(self):
        def validator(output):
            return (True, "All good")

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        result = await guardrail.validate("output")
        assert result.valid is True
        assert result.reason == "All good"

    @pytest.mark.asyncio
    async def test_dict_validator(self):
        def validator(output):
            return {
                "valid": False,
                "reason": "Invalid format",
                "details": {"field": "name"},
            }

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        result = await guardrail.validate("output")
        assert result.valid is False
        assert result.details["field"] == "name"

    @pytest.mark.asyncio
    async def test_validator_exception(self):
        def validator(output):
            raise ValueError("Validator crashed")

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        result = await guardrail.validate("output")
        assert result.valid is False
        assert "Validator crashed" in result.reason


# ==================== Guardrail LLM 验证测试 ====================


class TestGuardrailLLMValidation:
    @pytest.mark.asyncio
    async def test_llm_validation_pass(self):
        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(return_value="PASS")

        config = GuardrailConfig(validator="output must contain 'success'")
        guardrail = Guardrail(config, llm=mock_llm)
        result = await guardrail.validate("The operation was a success")

        assert result.valid is True

    @pytest.mark.asyncio
    async def test_llm_validation_fail(self):
        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(return_value="FAIL: Output is empty")

        config = GuardrailConfig(validator="output must not be empty")
        guardrail = Guardrail(config, llm=mock_llm)
        result = await guardrail.validate("")

        assert result.valid is False
        assert "empty" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_llm_validation_no_llm(self):
        config = GuardrailConfig(validator="some constraint")
        guardrail = Guardrail(config, llm=None)
        result = await guardrail.validate("output")

        # 没有 LLM 时默认通过
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_llm_validation_async_generate(self):
        mock_llm = MagicMock()
        # 使用 AsyncMock 处理异步方法
        mock_llm.generate = AsyncMock(return_value="PASS")
        mock_llm.chat = AsyncMock(return_value="PASS")

        config = GuardrailConfig(validator="check output")
        guardrail = Guardrail(config, llm=mock_llm)
        result = await guardrail.validate("output")

        # 应该通过（无论使用哪个方法）
        assert result.valid is True


# ==================== GuardrailExecutor 测试 ====================


class TestGuardrailExecutor:
    @pytest.mark.asyncio
    async def test_execute_with_validation_pass(self):
        def validator(output):
            return True

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        executor = GuardrailExecutor([guardrail])

        async def func():
            return "result"

        result = await executor.execute_with_validation(func)
        assert result == "result"

    @pytest.mark.asyncio
    async def test_execute_with_validation_retry(self):
        call_count = 0

        def validator(output):
            return output == "success"

        config = GuardrailConfig(validator=validator, max_retries=3)
        guardrail = Guardrail(config)
        executor = GuardrailExecutor([guardrail])

        async def func(context=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return "fail"
            return "success"

        result = await executor.execute_with_validation(func)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_execute_with_validation_max_retries(self):
        def validator(output):
            return False  # 永远失败

        config = GuardrailConfig(validator=validator, max_retries=2)
        guardrail = Guardrail(config)
        executor = GuardrailExecutor([guardrail])

        async def func():
            return "fail"

        with pytest.raises(GuardrailFailedError):
            await executor.execute_with_validation(func)

    @pytest.mark.asyncio
    async def test_execute_with_validation_default_value(self):
        def validator(output):
            return False

        config = GuardrailConfig(
            validator=validator,
            max_retries=2,
            on_failure="default_value",
            default_value={"status": "fallback"},
        )
        guardrail = Guardrail(config)
        executor = GuardrailExecutor([guardrail])

        async def func():
            return "fail"

        result = await executor.execute_with_validation(func)
        assert result == {"status": "fallback"}

    @pytest.mark.asyncio
    async def test_validate_only(self):
        def validator(output):
            return len(output) > 5

        config = GuardrailConfig(validator=validator)
        guardrail = Guardrail(config)
        executor = GuardrailExecutor([guardrail])

        result = await executor.validate_only("short")
        assert result.valid is False

        result = await executor.validate_only("long enough")
        assert result.valid is True


# ==================== builtin_validators 测试 ====================


class TestBuiltinValidators:
    def test_is_json_valid_string(self):
        valid, reason = builtin_validators.is_json('{"name": "test"}')
        assert valid is True

    def test_is_json_invalid_string(self):
        valid, reason = builtin_validators.is_json("not json")
        assert valid is False

    def test_is_json_dict(self):
        valid, reason = builtin_validators.is_json({"already": "dict"})
        assert valid is True

    def test_has_fields_present(self):
        validator = builtin_validators.has_fields("name", "email")
        valid, reason = validator({"name": "x", "email": "y"})
        assert valid is True

    def test_has_fields_missing(self):
        validator = builtin_validators.has_fields("name", "email", "phone")
        valid, reason = validator({"name": "x", "email": "y"})
        assert valid is False
        assert "phone" in reason

    def test_is_non_empty_string(self):
        valid, reason = builtin_validators.is_non_empty("hello")
        assert valid is True

    def test_is_non_empty_none(self):
        valid, reason = builtin_validators.is_non_empty(None)
        assert valid is False

    def test_is_non_empty_empty_string(self):
        valid, reason = builtin_validators.is_non_empty("   ")
        assert valid is False

    def test_max_length_within(self):
        validator = builtin_validators.max_length(10)
        valid, reason = validator("short")
        assert valid is True

    def test_max_length_exceeded(self):
        validator = builtin_validators.max_length(5)
        valid, reason = validator("too long string")
        assert valid is False

    def test_matches_regex_match(self):
        validator = builtin_validators.matches_regex(r"\d{4}-\d{2}-\d{2}")
        valid, reason = validator("2024-01-15")
        assert valid is True

    def test_matches_regex_no_match(self):
        validator = builtin_validators.matches_regex(r"\d{4}-\d{2}-\d{2}")
        valid, reason = validator("invalid")
        assert valid is False

    def test_contains_all(self):
        validator = builtin_validators.contains("hello", "world")
        valid, reason = validator("hello beautiful world")
        assert valid is True

    def test_contains_missing(self):
        validator = builtin_validators.contains("foo", "bar")
        valid, reason = validator("only foo here")
        assert valid is False


# ==================== HookEvent 测试 ====================


class TestHookEvent:
    def test_event_values(self):
        assert HookEvent.BEFORE_TOOL.value == "before_tool"
        assert HookEvent.AFTER_TOOL.value == "after_tool"
        assert HookEvent.ON_ERROR.value == "on_error"

    def test_all_events_exist(self):
        events = {e.value for e in HookEvent}
        assert events == {"before_tool", "after_tool", "on_error"}


# ==================== HookContext 测试 ====================


class TestHookContext:
    def test_defaults(self):
        ctx = HookContext(tool_name="test", tool_args={"x": 1})
        assert ctx.tool_result is None
        assert ctx.error is None
        assert ctx.skip_execution is False
        assert ctx.retry_count == 0

    def test_custom_values(self):
        ctx = HookContext(
            tool_name="search",
            tool_args={"query": "test"},
            tool_result="found",
            agent_id="agent-1",
            agent_name="Researcher",
            conversation_id="conv-1",
        )
        assert ctx.tool_name == "search"
        assert ctx.agent_name == "Researcher"


# ==================== HookResult 测试 ====================


class TestHookResult:
    def test_defaults(self):
        result = HookResult()
        assert result.modified_args is None
        assert result.modified_result is None
        assert result.skip_execution is False
        assert result.continue_chain is True

    def test_modified_args(self):
        result = HookResult(modified_args={"x": 2})
        assert result.modified_args == {"x": 2}


# ==================== ToolHook 测试 ====================


class TestToolHook:
    def test_creation(self):
        def callback(ctx):
            return HookResult()

        hook = ToolHook(HookEvent.BEFORE_TOOL, callback)
        assert hook.event == HookEvent.BEFORE_TOOL
        assert hook.priority == 100
        assert hook.tool_filter is None

    def test_tool_filter_match(self):
        hook = ToolHook(
            HookEvent.BEFORE_TOOL,
            lambda ctx: HookResult(),
            tool_filter=["search", "query"],
        )
        assert hook.should_apply("search") is True
        assert hook.should_apply("other") is False

    def test_tool_filter_none(self):
        hook = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult())
        assert hook.should_apply("any_tool") is True

    @pytest.mark.asyncio
    async def test_execute_sync_callback(self):
        def callback(ctx):
            return HookResult(skip_execution=True)

        hook = ToolHook(HookEvent.BEFORE_TOOL, callback)
        ctx = HookContext(tool_name="test", tool_args={})
        result = await hook.execute(ctx)

        assert result.skip_execution is True

    @pytest.mark.asyncio
    async def test_execute_async_callback(self):
        async def callback(ctx):
            return HookResult(modified_args={"x": 2})

        hook = ToolHook(HookEvent.BEFORE_TOOL, callback)
        ctx = HookContext(tool_name="test", tool_args={"x": 1})
        result = await hook.execute(ctx)

        assert result.modified_args == {"x": 2}

    @pytest.mark.asyncio
    async def test_execute_dict_return(self):
        def callback(ctx):
            return {"skip_execution": True}

        hook = ToolHook(HookEvent.BEFORE_TOOL, callback)
        ctx = HookContext(tool_name="test", tool_args={})
        result = await hook.execute(ctx)

        assert result.skip_execution is True

    @pytest.mark.asyncio
    async def test_execute_none_return(self):
        def callback(ctx):
            return None

        hook = ToolHook(HookEvent.BEFORE_TOOL, callback)
        ctx = HookContext(tool_name="test", tool_args={})
        result = await hook.execute(ctx)

        assert result.skip_execution is False
        assert result.continue_chain is True


# ==================== ToolHookRunner 测试 ====================


class TestToolHookRunner:
    def test_register(self):
        runner = ToolHookRunner()
        hook = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult())
        runner.register(hook)

        assert len(runner._hooks[HookEvent.BEFORE_TOOL]) == 1

    def test_register_priority_order(self):
        runner = ToolHookRunner()
        hook1 = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult(), priority=100)
        hook2 = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult(), priority=50)

        runner.register(hook1)
        runner.register(hook2)

        # 优先级低的先执行
        assert runner._hooks[HookEvent.BEFORE_TOOL][0] == hook2

    def test_unregister(self):
        runner = ToolHookRunner()
        hook = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult())
        runner.register(hook)
        runner.unregister(hook)

        assert len(runner._hooks[HookEvent.BEFORE_TOOL]) == 0

    def test_clear(self):
        runner = ToolHookRunner()
        runner.register(ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult()))
        runner.register(ToolHook(HookEvent.AFTER_TOOL, lambda ctx: HookResult()))
        runner.clear()

        assert len(runner._hooks[HookEvent.BEFORE_TOOL]) == 0
        assert len(runner._hooks[HookEvent.AFTER_TOOL]) == 0

    @pytest.mark.asyncio
    async def test_run_before(self):
        runner = ToolHookRunner()
        hook = ToolHook(
            HookEvent.BEFORE_TOOL,
            lambda ctx: HookResult(modified_args={"added": True}),
        )
        runner.register(hook)

        args, skip = await runner.run_before("test", {"x": 1})
        assert args["x"] == 1
        assert args["added"] is True
        assert skip is False

    @pytest.mark.asyncio
    async def test_run_before_skip(self):
        runner = ToolHookRunner()
        hook = ToolHook(
            HookEvent.BEFORE_TOOL,
            lambda ctx: HookResult(skip_execution=True),
        )
        runner.register(hook)

        args, skip = await runner.run_before("test", {"x": 1})
        assert skip is True

    @pytest.mark.asyncio
    async def test_run_after(self):
        runner = ToolHookRunner()
        hook = ToolHook(
            HookEvent.AFTER_TOOL,
            lambda ctx: HookResult(modified_result="modified"),
        )
        runner.register(hook)

        result = await runner.run_after("test", {}, "original")
        assert result == "modified"

    @pytest.mark.asyncio
    async def test_run_on_error(self):
        runner = ToolHookRunner()
        hook = ToolHook(
            HookEvent.ON_ERROR,
            lambda ctx: HookResult(should_retry=True),
        )
        runner.register(hook)

        should_retry, default = await runner.run_on_error(
            "test", {}, ValueError("error")
        )
        assert should_retry is True

    @pytest.mark.asyncio
    async def test_execute_with_hooks_success(self):
        runner = ToolHookRunner()

        # BEFORE hook 修改参数
        runner.register(
            ToolHook(
                HookEvent.BEFORE_TOOL,
                lambda ctx: HookResult(modified_args={"x": 10}),
            )
        )

        # AFTER hook 修改结果
        runner.register(
            ToolHook(
                HookEvent.AFTER_TOOL,
                lambda ctx: HookResult(modified_result=ctx.tool_result * 2),
            )
        )

        def tool_func(x):
            return x + 5

        result = await runner.execute_with_hooks("test", tool_func, {"x": 1})

        # 流程：args {x:1} -> BEFORE {x:10} -> func(10) -> 15 -> AFTER -> 30
        assert result == 30

    @pytest.mark.asyncio
    async def test_execute_with_hooks_error_and_retry(self):
        runner = ToolHookRunner()
        call_count = 0

        def error_hook(ctx):
            if ctx.retry_count < 1:
                return HookResult(should_retry=True)
            return HookResult(default_value="fallback")

        runner.register(ToolHook(HookEvent.ON_ERROR, error_hook))

        def failing_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        result = await runner.execute_with_hooks("test", failing_func, {})

        assert result == "fallback"
        assert call_count == 2  # 初始 + 1次重试


# ==================== builtin_hooks 测试 ====================


class TestBuiltinHooks:
    @pytest.mark.asyncio
    async def test_log_execution(self):
        logs = []

        def logger_func(msg):
            logs.append(msg)

        hook = builtin_hooks.log_execution(
            event=HookEvent.BEFORE_TOOL,
            logger_func=logger_func,
        )

        ctx = HookContext(tool_name="test", tool_args={"x": 1})
        await hook.execute(ctx)

        assert len(logs) == 1
        assert "test" in logs[0]

    @pytest.mark.asyncio
    async def test_validate_args_valid(self):
        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        hook = builtin_hooks.validate_args(schema)
        hook.event = HookEvent.BEFORE_TOOL

        ctx = HookContext(tool_name="test", tool_args={"name": "valid"})
        result = await hook.execute(ctx)

        assert result.skip_execution is False

    @pytest.mark.asyncio
    async def test_validate_args_missing_required(self):
        schema = {"required": ["name", "email"]}
        hook = builtin_hooks.validate_args(schema)
        hook.event = HookEvent.BEFORE_TOOL

        ctx = HookContext(tool_name="test", tool_args={"name": "x"})

        with pytest.raises(ValueError, match="Missing"):
            await hook.execute(ctx)

    @pytest.mark.asyncio
    async def test_validate_args_wrong_type(self):
        schema = {
            "required": ["count"],
            "properties": {"count": {"type": "number"}},
        }
        hook = builtin_hooks.validate_args(schema)
        hook.event = HookEvent.BEFORE_TOOL

        ctx = HookContext(tool_name="test", tool_args={"count": "not a number"})

        with pytest.raises(TypeError, match="must be number"):
            await hook.execute(ctx)

    @pytest.mark.asyncio
    async def test_rate_limit(self):
        hook = builtin_hooks.rate_limit(max_calls=2, window_seconds=60)
        hook.event = HookEvent.BEFORE_TOOL

        # 前两次应该成功
        ctx1 = HookContext(tool_name="test", tool_args={})
        ctx2 = HookContext(tool_name="test", tool_args={})

        result1 = await hook.execute(ctx1)
        result2 = await hook.execute(ctx2)

        assert result1.skip_execution is False
        assert result2.skip_execution is False

        # 第三次应该失败
        ctx3 = HookContext(tool_name="test", tool_args={})
        with pytest.raises(RuntimeError, match="Rate limit"):
            await hook.execute(ctx3)

    @pytest.mark.asyncio
    async def test_transform_args(self):
        def transformer(args):
            return {"query": args.get("q", "").upper()}

        hook = builtin_hooks.transform_args(transformer)
        hook.event = HookEvent.BEFORE_TOOL

        ctx = HookContext(tool_name="test", tool_args={"q": "hello"})
        result = await hook.execute(ctx)

        assert result.modified_args == {"query": "HELLO"}

    @pytest.mark.asyncio
    async def test_transform_result(self):
        def transformer(result):
            return result.upper()

        hook = builtin_hooks.transform_result(transformer)
        hook.event = HookEvent.AFTER_TOOL

        ctx = HookContext(tool_name="test", tool_args={}, tool_result="hello")
        result = await hook.execute(ctx)

        assert result.modified_result == "HELLO"


# ==================== hook 装饰器测试 ====================


class TestHookDecorator:
    def test_decorator(self):
        @hook(HookEvent.BEFORE_TOOL, tool_filter=["search"])
        def my_hook(ctx):
            return HookResult(skip_execution=True)

        assert isinstance(my_hook, ToolHook)
        assert my_hook.event == HookEvent.BEFORE_TOOL
        assert my_hook.tool_filter == ["search"]

    def test_decorator_with_priority(self):
        @hook(HookEvent.AFTER_TOOL, priority=50)
        def my_hook(ctx):
            return HookResult()

        assert my_hook.priority == 50


# ==================== AgentLoop Guardrails 集成测试 ====================


class TestAgentLoopGuardrails:
    def test_guardrails_parameter(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        guardrail = Guardrail(GuardrailConfig(validator=lambda x: True))

        loop = AgentLoop(provider=provider, guardrails=[guardrail])
        assert len(loop._guardrails) == 1

    def test_add_guardrail(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        guardrail = Guardrail(GuardrailConfig(validator=lambda x: True))
        loop.add_guardrail(guardrail)

        assert len(loop._guardrails) == 1

    @pytest.mark.asyncio
    async def test_validate_output(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        guardrail = Guardrail(GuardrailConfig(validator=lambda x: len(x) > 5))
        loop = AgentLoop(provider=provider, guardrails=[guardrail])

        result = await loop.validate_output("short")
        assert result.valid is False

        result = await loop.validate_output("long enough")
        assert result.valid is True

    @pytest.mark.asyncio
    async def test_execute_with_validation(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()

        call_count = 0

        def validator(output):
            return output == "success"

        guardrail = Guardrail(GuardrailConfig(validator=validator, max_retries=3))
        loop = AgentLoop(provider=provider, guardrails=[guardrail])

        async def func(context=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return "fail"
            return "success"

        result = await loop.execute_with_validation(func)
        assert result == "success"
        assert call_count == 2


# ==================== AgentLoop Tool Hooks 集成测试 ====================


class TestAgentLoopToolHooks:
    def test_tool_hooks_parameter(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        runner = ToolHookRunner()
        runner.register(ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult()))

        loop = AgentLoop(provider=provider, tool_hooks=runner)
        assert len(loop.get_tool_hooks()) == 1

    def test_add_tool_hook(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        hook = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult())
        loop.add_tool_hook(hook)

        assert len(loop.get_tool_hooks()) == 1

    def test_remove_tool_hook(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        hook = ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult())
        loop.add_tool_hook(hook)
        loop.remove_tool_hook(hook)

        assert len(loop.get_tool_hooks()) == 0

    def test_clear_tool_hooks(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        loop = AgentLoop(provider=provider)

        loop.add_tool_hook(ToolHook(HookEvent.BEFORE_TOOL, lambda ctx: HookResult()))
        loop.add_tool_hook(ToolHook(HookEvent.AFTER_TOOL, lambda ctx: HookResult()))

        loop.clear_tool_hooks(HookEvent.BEFORE_TOOL)
        hooks = loop.get_tool_hooks()
        assert len(hooks) == 1
        assert hooks[0].event == HookEvent.AFTER_TOOL

    @pytest.mark.asyncio
    async def test_tool_hooks_in_execution(self):
        from app.modules.agent.loop import AgentLoop

        provider = MagicMock()
        provider.chat_stream = AsyncMock(return_value=iter([]))

        # 创建 mock 工具注册表
        tools = MagicMock()
        tools.get_definitions = MagicMock(return_value=[])
        tools.execute = AsyncMock(return_value="original result")

        # 创建 Hook 修改结果
        runner = ToolHookRunner()
        runner.register(
            ToolHook(
                HookEvent.AFTER_TOOL,
                lambda ctx: HookResult(modified_result="modified result"),
            )
        )

        loop = AgentLoop(provider=provider, tools=tools, tool_hooks=runner)

        result = await loop._execute_tool("test_tool", {"x": 1})
        assert result == "modified result"
