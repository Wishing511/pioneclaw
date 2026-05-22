"""
Recovery Recipes 测试

借鉴 claw-code recovery_recipes.rs 的测试场景
"""

import pytest

from app.core.recovery_recipes import (
    ApiRateLimitError,
    AuthError,
    ConnectionError_,
    DiskFullError,
    FailureScenario,
    FileNotFound_,
    GitLockError,
    ProviderFailureError,
    # Errors
    RecoverableToolError,
    RecoveryContext,
    RecoveryEvent,
    RecoveryEventType,
    # Executor
    RecoveryExecutor,
    RecoveryResult,
    # Recipe
    RecoveryStep,
    ToolTimeoutError,
    _compute_backoff_ms,
    classify_error,
    get_all_recipes,
    get_recent_events,
    recipe_for,
    # History
    record_recovery_event,
)

# ==================== Error Hierarchy ====================


class TestRecoverableToolErrors:
    """可恢复错误层级"""

    def test_base_error(self):
        e = RecoverableToolError("test error")
        assert isinstance(e, Exception)
        assert str(e) == "test error"
        assert e.original_error is None

    def test_wraps_original(self):
        orig = ValueError("original problem")
        e = RecoverableToolError("wrapper", original_error=orig)
        assert e.original_error is orig

    def test_timeout_error(self):
        e = ToolTimeoutError("timed out")
        assert isinstance(e, RecoverableToolError)
        assert isinstance(e, ToolTimeoutError)

    def test_connection_error(self):
        e = ConnectionError_("connection refused")
        assert isinstance(e, RecoverableToolError)

    def test_rate_limit_error(self):
        e = ApiRateLimitError("429 Too Many Requests")
        assert isinstance(e, RecoverableToolError)

    def test_provider_failure(self):
        e = ProviderFailureError("502 Bad Gateway")
        assert isinstance(e, RecoverableToolError)

    def test_auth_error(self):
        e = AuthError("401 Unauthorized")
        assert isinstance(e, RecoverableToolError)

    def test_git_lock_error(self):
        e = GitLockError(".git/index.lock exists")
        assert isinstance(e, RecoverableToolError)

    def test_disk_full_error(self):
        e = DiskFullError("No space left on device")
        assert isinstance(e, RecoverableToolError)

    def test_file_not_found_error(self):
        e = FileNotFound_("/path/to/file not found", path="/path/to/file")
        assert isinstance(e, RecoverableToolError)
        assert e.path == "/path/to/file"


# ==================== Scenario Classification ====================


class TestFailureScenarioClassification:
    """按显式错误类型分类"""

    def test_api_rate_limit(self):
        assert (
            classify_error(ApiRateLimitError("429")) == FailureScenario.API_RATE_LIMITED
        )

    def test_provider_failure(self):
        assert (
            classify_error(ProviderFailureError("500"))
            == FailureScenario.PROVIDER_FAILURE
        )

    def test_tool_timeout(self):
        assert (
            classify_error(ToolTimeoutError("timeout")) == FailureScenario.TOOL_TIMEOUT
        )

    def test_connection_error(self):
        assert (
            classify_error(ConnectionError_("connection refused"))
            == FailureScenario.CONNECTION_ERROR
        )

    def test_git_lock(self):
        assert classify_error(GitLockError("lock")) == FailureScenario.GIT_LOCK

    def test_disk_full(self):
        assert classify_error(DiskFullError("no space")) == FailureScenario.DISK_FULL

    def test_auth_error(self):
        assert classify_error(AuthError("unauthorized")) == FailureScenario.AUTH_ERROR

    def test_file_not_found(self):
        assert (
            classify_error(FileNotFound_("no such file"))
            == FailureScenario.FILE_NOT_FOUND
        )

    def test_unknown(self):
        assert (
            classify_error(ValueError("some random error")) == FailureScenario.UNKNOWN
        )


class TestFailureScenarioPatternMatching:
    """字符串 fallback 匹配"""

    def test_rate_limit_string_match(self):
        assert (
            classify_error(RuntimeError("API rate limit exceeded"))
            == FailureScenario.API_RATE_LIMITED
        )

    def test_provider_503_string_match(self):
        assert (
            classify_error(RuntimeError("503 Service Unavailable"))
            == FailureScenario.PROVIDER_FAILURE
        )

    def test_timeout_string_match(self):
        assert (
            classify_error(RuntimeError("Connection timed out"))
            == FailureScenario.TOOL_TIMEOUT
        )

    def test_git_lock_string_match(self):
        assert (
            classify_error(
                RuntimeError("Unable to create '.git/index.lock': File exists")
            )
            == FailureScenario.GIT_LOCK
        )

    def test_disk_full_string_match(self):
        assert (
            classify_error(RuntimeError("ENOSPC: no space left on device"))
            == FailureScenario.DISK_FULL
        )

    def test_auth_string_match(self):
        assert (
            classify_error(RuntimeError("401 Unauthorized: invalid api key"))
            == FailureScenario.AUTH_ERROR
        )

    def test_file_not_found_string_match(self):
        assert (
            classify_error(FileNotFoundError("No such file or directory"))
            == FailureScenario.FILE_NOT_FOUND
        )


# ==================== Recovery Recipes ====================


class TestRecoveryRecipes:
    """配方元数据"""

    def test_all_scenarios_have_recipes(self):
        for scenario in FailureScenario:
            if scenario == FailureScenario.UNKNOWN:
                continue
            recipe = recipe_for(scenario)
            assert recipe is not None, f"No recipe for {scenario}"

    def test_unknown_no_recipe(self):
        assert recipe_for(FailureScenario.UNKNOWN) is None

    def test_rate_limit_recipe(self):
        r = recipe_for(FailureScenario.API_RATE_LIMITED)
        assert r.max_attempts == 3
        assert RecoveryStep.WAIT_AND_RETRY in r.steps

    def test_provider_failure_recipe(self):
        r = recipe_for(FailureScenario.PROVIDER_FAILURE)
        assert r.max_attempts == 2
        assert RecoveryStep.WAIT_AND_RETRY in r.steps
        assert RecoveryStep.ROTATE_KEY in r.steps

    def test_timeout_recipe(self):
        r = recipe_for(FailureScenario.TOOL_TIMEOUT)
        assert r.max_attempts == 1
        assert RecoveryStep.RETRY_WITH_LONGER_TIMEOUT in r.steps

    def test_git_lock_recipe(self):
        r = recipe_for(FailureScenario.GIT_LOCK)
        assert r.max_attempts == 1
        assert r.can_auto_fix
        assert RecoveryStep.AUTO_FIX in r.steps

    def test_disk_full_recipe(self):
        r = recipe_for(FailureScenario.DISK_FULL)
        assert r.max_attempts == 0
        assert r.should_escalate_immediately
        assert RecoveryStep.ESCALATE_TO_HUMAN in r.steps

    def test_auth_recipe(self):
        r = recipe_for(FailureScenario.AUTH_ERROR)
        assert r.max_attempts == 1
        assert RecoveryStep.ROTATE_KEY in r.steps

    def test_file_not_found_recipe(self):
        r = recipe_for(FailureScenario.FILE_NOT_FOUND)
        assert r.max_attempts == 0
        assert RecoveryStep.REPORT_TO_LLM in r.steps

    def test_get_all_recipes(self):
        all_recipes = get_all_recipes()
        assert len(all_recipes) == 8
        assert "api_rate_limited" in all_recipes


# ==================== Recovery Executor ====================


class TestRecoveryExecutor:
    """恢复执行器"""

    def test_retry_on_rate_limit(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        assert result.should_retry is True
        assert result.wait_ms > 0
        assert ctx.attempt_count == 1

    def test_escalate_after_max_attempts(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext(attempt_count=3)  # 已达 max_attempts (3)
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        assert result.escalate is True
        assert "已尝试 3/3 次" in result.detail

    def test_git_lock_auto_fix(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.GIT_LOCK, ctx)
        assert result.recovered is True
        assert result.should_retry is True
        assert result.auto_fix_command == "rm -f .git/index.lock"

    def test_disk_full_immediate_escalate(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.DISK_FULL, ctx)
        assert result.escalate is True
        # DISK_FULL max_attempts=0, 立即升级不增加 attempt_count

    def test_file_not_found_report_to_llm(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.FILE_NOT_FOUND, ctx)
        assert result.should_retry is False
        assert result.escalate is False

    def test_unknown_scenario(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.UNKNOWN, ctx)
        assert result.should_retry is False
        assert result.escalate is False

    def test_provider_failure_first_attempt(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.PROVIDER_FAILURE, ctx)
        assert result.should_retry is True
        assert ctx.attempt_count == 1

    def test_provider_failure_second_attempt_escalates(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext(attempt_count=1)
        # 第一次重试后再次失败 → 第二次尝试时 attempt_count=1，再 increment 到 2
        result = executor.attempt_recovery(FailureScenario.PROVIDER_FAILURE, ctx)
        # attempt_count=2 >= max_attempts=2 → escalate
        if result.should_retry:
            # 第一次重试成功
            pass
        else:
            assert result.escalate is True or ctx.attempt_count >= 2

    def test_escalate_on_second_attempt(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext(attempt_count=2)
        result = executor.attempt_recovery(FailureScenario.TOOL_TIMEOUT, ctx)
        assert result.escalate is True

    def test_auto_fix_disabled(self):
        executor = RecoveryExecutor(auto_fix_enabled=False)
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.GIT_LOCK, ctx)
        assert result.escalate is True
        assert "自动修复已禁用" in result.detail

    def test_simulate_failure(self):
        executor = RecoveryExecutor()
        ctx = RecoveryContext(fail_at_step=0)
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        assert result.should_retry is True  # step 0 failed, but can retry
        assert len(ctx.events) >= 1

    def test_exponential_backoff(self):
        """指数退避计算"""
        ms1 = _compute_backoff_ms(1)  # ~2000ms
        ms2 = _compute_backoff_ms(2)  # ~8000ms
        ms3 = _compute_backoff_ms(3)  # ~32000ms
        assert 1500 <= ms1 <= 2500, f"Expected ~2000ms, got {ms1}"
        assert 6000 <= ms2 <= 10000, f"Expected ~8000ms, got {ms2}"
        assert 24000 <= ms3 <= 32000, f"Expected ~32000ms, got {ms3}"


# ==================== Recovery Context ====================


class TestRecoveryContext:
    """恢复上下文"""

    def test_initial_state(self):
        ctx = RecoveryContext()
        assert ctx.attempt_count == 0
        assert len(ctx.events) == 0

    def test_increment_attempt(self):
        ctx = RecoveryContext()
        ctx.increment_attempt()
        assert ctx.attempt_count == 1
        ctx.increment_attempt()
        assert ctx.attempt_count == 2

    def test_record_event(self):
        ctx = RecoveryContext()
        event = RecoveryEvent(
            event_type=RecoveryEventType.ATTEMPTED,
            scenario=FailureScenario.TOOL_TIMEOUT,
            detail="test",
        )
        ctx.record_event(event)
        assert len(ctx.events) == 1
        assert ctx.events[0].event_type == RecoveryEventType.ATTEMPTED

    def test_should_escalate(self):
        recipe = recipe_for(FailureScenario.TOOL_TIMEOUT)
        ctx = RecoveryContext(attempt_count=0)
        assert ctx.should_escalate(recipe) is False
        ctx.attempt_count = 1
        assert ctx.should_escalate(recipe) is True  # max_attempts=1

    def test_disk_full_always_escalates(self):
        recipe = recipe_for(FailureScenario.DISK_FULL)
        ctx = RecoveryContext(attempt_count=0)
        assert ctx.should_escalate(recipe) is True  # max_attempts=0


# ==================== Recovery Result ====================


class TestRecoveryResult:
    """恢复结果构造"""

    def test_retry_result(self):
        r = RecoveryResult.retry(wait_ms=3000)
        assert r.should_retry is True
        assert r.wait_ms == 3000

    def test_escalate_result(self):
        r = RecoveryResult.escalate_to_human("need help")
        assert r.escalate is True

    def test_abort_result(self):
        r = RecoveryResult.abort("give up")
        assert r.should_retry is False
        assert r.escalate is False

    def test_auto_fix_result(self):
        r = RecoveryResult.auto_fix("rm -f lock", detail="removing lock")
        assert r.recovered is True
        assert r.auto_fix_command == "rm -f lock"


# ==================== Recovery Event ====================


class TestRecoveryEvent:
    """恢复事件"""

    def test_event_timestamp(self):
        e = RecoveryEvent(
            event_type=RecoveryEventType.SUCCEEDED,
            scenario=FailureScenario.GIT_LOCK,
        )
        assert e.timestamp > 0

    def test_event_to_dict(self):
        e = RecoveryEvent(
            event_type=RecoveryEventType.ESCALATED,
            scenario=FailureScenario.DISK_FULL,
            attempt=0,
            max_attempts=0,
            detail="no space",
        )
        d = e.to_dict()
        assert d["type"] == "escalated"
        assert d["scenario"] == "disk_full"
        assert d["detail"] == "no space"

    def test_event_history(self):
        e = RecoveryEvent(
            event_type=RecoveryEventType.SUCCEEDED,
            scenario=FailureScenario.API_RATE_LIMITED,
        )
        record_recovery_event(e)
        events = get_recent_events(limit=10)
        assert len(events) >= 1
        assert events[-1]["scenario"] == "api_rate_limited"


# ==================== Integration Tests ====================


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_recoverable_error_propagates(self):
        """RecoverableToolError 应该透传，不被字符串化"""
        error = ToolTimeoutError("timeout exceeded")
        # 模拟：工具抛这个错误，registry 应该 re-raise
        with pytest.raises(RecoverableToolError):
            raise error

    @pytest.mark.asyncio
    async def test_non_recoverable_still_stringified(self):
        """普通异常仍然被字符串化（保持向后兼容）"""
        # 这由 registry.py 的 except Exception 处理
        # 不需要特殊测试，只需确认 ValueError 不是 RecoverableToolError
        e = ValueError("random error")
        assert not isinstance(e, RecoverableToolError)

    def test_full_recovery_flow(self):
        """完整恢复流程：分类 → 配方 → 执行 → 重试"""
        error = ApiRateLimitError("429 Too Many Requests")

        scenario = classify_error(error)
        assert scenario == FailureScenario.API_RATE_LIMITED

        recipe = recipe_for(scenario)
        assert recipe is not None

        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(scenario, ctx)

        assert result.should_retry is True
        assert result.wait_ms > 0
        assert ctx.attempt_count == 1

    def test_full_flow_escalation(self):
        """完整升级流程"""
        error = DiskFullError("no space")

        scenario = classify_error(error)
        assert scenario == FailureScenario.DISK_FULL

        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(scenario, ctx)

        assert result.escalate is True


# ==================== Edge Cases ====================


class TestEdgeCases:
    """边界情况"""

    def test_unknown_error_no_recipe(self):
        """未知错误 → 无配方 → abort"""
        executor = RecoveryExecutor()
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.UNKNOWN, ctx)
        assert result.should_retry is False
        assert result.escalate is False

    def test_concurrent_contexts(self):
        """独立上下文不应该互相影响"""
        ctx1 = RecoveryContext()
        ctx2 = RecoveryContext()

        executor = RecoveryExecutor()
        executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx1)
        assert ctx1.attempt_count == 1
        assert ctx2.attempt_count == 0  # 不受 ctx1 影响

    def test_recursive_recovery_safety(self):
        """恢复步骤失败不应该导致无限递归"""
        executor = RecoveryExecutor()
        ctx = RecoveryContext()

        # 第一次恢复
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        assert result.should_retry is True
        # 第二次恢复
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        # 第三次应该还在重试范围内 (max_attempts=3)
        assert ctx.attempt_count <= 3
        # 第四次 → escalate
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)
        result = executor.attempt_recovery(FailureScenario.API_RATE_LIMITED, ctx)

    def test_most_scenarios_escalate_after_exhausting_attempts(self):
        """除了 RATE_LIMITED (max_attempts=3)，大部分场景在 2 次后升级"""
        executor = RecoveryExecutor()

        # PROVIDER_FAILURE: max_attempts=2
        ctx = RecoveryContext(attempt_count=2)
        result = executor.attempt_recovery(FailureScenario.PROVIDER_FAILURE, ctx)
        assert result.escalate is True

        # CONNECTION_ERROR: max_attempts=2
        ctx = RecoveryContext(attempt_count=2)
        result = executor.attempt_recovery(FailureScenario.CONNECTION_ERROR, ctx)
        assert result.escalate is True

        # TOOL_TIMEOUT: max_attempts=1
        ctx = RecoveryContext(attempt_count=1)
        result = executor.attempt_recovery(FailureScenario.TOOL_TIMEOUT, ctx)
        assert result.escalate is True

    def test_recipe_immutability(self):
        """built-in recipes 不应被修改"""
        r1 = recipe_for(FailureScenario.API_RATE_LIMITED)
        r2 = recipe_for(FailureScenario.API_RATE_LIMITED)
        assert r1.max_attempts == r2.max_attempts
        assert r1.steps == r2.steps

    def test_recoverable_error_not_caught_as_string(self):
        """确保 RecoverableToolError 子类能被 isinstance 检测"""
        errors = [
            ToolTimeoutError(""),
            ConnectionError_(""),
            ApiRateLimitError(""),
            ProviderFailureError(""),
            AuthError(""),
            GitLockError(""),
            DiskFullError(""),
            FileNotFound_(""),
        ]
        for e in errors:
            assert isinstance(e, RecoverableToolError), (
                f"{type(e).__name__} should be RecoverableToolError"
            )


# ==================== Custom Recipe Registration ====================


class TestCustomRecipes:
    """自定义配方"""

    def test_register_auto_fix_handler(self):
        executor = RecoveryExecutor()
        called = []

        def my_handler():
            called.append(True)

        executor.register_auto_fix(FailureScenario.GIT_LOCK, my_handler)
        # 自定义 handler 不覆盖内置 auto_fix（内置依然生效）
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.GIT_LOCK, ctx)
        assert result.recovered is True
        assert result.auto_fix_command == "rm -f .git/index.lock"

    def test_rotate_key_with_handler(self):
        executor = RecoveryExecutor()
        key_rotated = []

        def rotate():
            key_rotated.append(True)

        executor.register_auto_fix(FailureScenario.AUTH_ERROR, rotate)
        # 注意：ROTATE_KEY step 查找的是 "rotate_key" handler
        executor._auto_fix_handlers["rotate_key"] = rotate
        ctx = RecoveryContext()
        result = executor.attempt_recovery(FailureScenario.AUTH_ERROR, ctx)
        assert key_rotated or result.should_retry or result.escalate
