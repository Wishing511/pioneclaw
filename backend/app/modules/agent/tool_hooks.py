"""
Tool Hooks 工具拦截系统

借鉴 PraisonAI Tool Hooks：
- BEFORE_TOOL: 工具执行前拦截，可修改参数或跳过
- AFTER_TOOL: 工具执行后拦截，可修改结果
- ON_ERROR: 工具执行出错时拦截，可重试或返回默认值

使用场景：
- 日志记录
- 参数验证
- 结果缓存
- 错误重试
- 权限检查
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(Enum):
    """Hook 事件类型

    借鉴 PraisonAI HookEvent
    """

    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    ON_ERROR = "on_error"


@dataclass
class HookContext:
    """Hook 执行上下文

    借鉴 PraisonAI HookContext
    """

    tool_name: str
    tool_args: dict[str, Any]
    tool_result: Any = None
    error: Exception | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # 控制标志
    skip_execution: bool = False  # BEFORE_TOOL 可设置，跳过实际执行
    retry_count: int = 0
    max_retries: int = 3


@dataclass
class HookResult:
    """Hook 处理结果"""

    # 修改后的参数（BEFORE_TOOL）
    modified_args: dict[str, Any] | None = None

    # 修改后的结果（AFTER_TOOL）
    modified_result: Any = None

    # 是否跳过执行（BEFORE_TOOL）
    skip_execution: bool = False

    # 是否重试（ON_ERROR）
    should_retry: bool = False

    # 返回默认值（ON_ERROR）
    default_value: Any = None

    # 是否继续执行后续 hooks
    continue_chain: bool = True


class ToolHook:
    """工具 Hook

    借鉴 PraisonAI ToolHook
    """

    def __init__(
        self,
        event: HookEvent,
        callback: Callable[[HookContext], HookResult | dict | None],
        tool_filter: list[str] | None = None,  # 只对特定工具生效
        priority: int = 100,  # 优先级，数字越小越先执行
    ):
        """
        Args:
            event: Hook 事件类型
            callback: Hook 回调函数
            tool_filter: 工具名过滤列表，None 表示所有工具
            priority: 执行优先级
        """
        self.event = event
        self.callback = callback
        self.tool_filter = tool_filter
        self.priority = priority

    def should_apply(self, tool_name: str) -> bool:
        """检查是否应该应用此 Hook"""
        if self.tool_filter is None:
            return True
        return tool_name in self.tool_filter

    async def execute(self, context: HookContext) -> HookResult | None:
        """执行 Hook"""
        try:
            result = self.callback(context)

            # 处理异步回调
            if asyncio.iscoroutine(result):
                result = await result

            # 处理不同返回类型
            if result is None:
                return HookResult()
            elif isinstance(result, HookResult):
                return result
            elif isinstance(result, dict):
                return HookResult(
                    modified_args=result.get("modified_args"),
                    modified_result=result.get("modified_result"),
                    skip_execution=result.get("skip_execution", False),
                    should_retry=result.get("should_retry", False),
                    default_value=result.get("default_value"),
                )
            else:
                logger.warning(f"Hook returned unexpected type: {type(result)}")
                return HookResult()

        except Exception as e:
            logger.error(f"Hook execution error: {e}")
            # 重新抛出异常，让调用者处理
            raise


class ToolHookRunner:
    """Tool Hook 执行器

    管理多个 Hook，按优先级执行
    """

    def __init__(self):
        self._hooks: dict[HookEvent, list[ToolHook]] = {
            HookEvent.BEFORE_TOOL: [],
            HookEvent.AFTER_TOOL: [],
            HookEvent.ON_ERROR: [],
        }

    def register(self, hook: ToolHook) -> None:
        """注册 Hook"""
        self._hooks[hook.event].append(hook)
        # 按优先级排序
        self._hooks[hook.event].sort(key=lambda h: h.priority)

    def unregister(self, hook: ToolHook) -> None:
        """注销 Hook"""
        if hook in self._hooks[hook.event]:
            self._hooks[hook.event].remove(hook)

    def clear(self, event: HookEvent | None = None) -> None:
        """清除 Hook"""
        if event:
            self._hooks[event].clear()
        else:
            for e in HookEvent:
                self._hooks[e].clear()

    async def run_before(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: HookContext | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """执行 BEFORE_TOOL hooks

        Args:
            tool_name: 工具名
            args: 工具参数
            context: Hook 上下文

        Returns:
            tuple: (修改后的参数, 是否跳过执行)
        """
        ctx = context or HookContext(tool_name=tool_name, tool_args=args)
        ctx.tool_name = tool_name
        ctx.tool_args = args

        current_args = args.copy()

        for hook in self._hooks[HookEvent.BEFORE_TOOL]:
            if not hook.should_apply(tool_name):
                continue

            result = await hook.execute(ctx)

            if result:
                # 应用参数修改
                if result.modified_args:
                    current_args.update(result.modified_args)
                    ctx.tool_args = current_args

                # 检查是否跳过执行
                if result.skip_execution:
                    ctx.skip_execution = True
                    return current_args, True

                # 检查是否中断链
                if not result.continue_chain:
                    break

        return current_args, False

    async def run_after(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        context: HookContext | None = None,
    ) -> Any:
        """执行 AFTER_TOOL hooks

        Args:
            tool_name: 工具名
            args: 工具参数
            result: 工具执行结果
            context: Hook 上下文

        Returns:
            Any: 修改后的结果
        """
        ctx = context or HookContext(tool_name=tool_name, tool_args=args)
        ctx.tool_name = tool_name
        ctx.tool_args = args
        ctx.tool_result = result

        current_result = result

        for hook in self._hooks[HookEvent.AFTER_TOOL]:
            if not hook.should_apply(tool_name):
                continue

            hook_result = await hook.execute(ctx)

            if hook_result:
                # 应用结果修改
                if hook_result.modified_result is not None:
                    current_result = hook_result.modified_result
                    ctx.tool_result = current_result

                # 检查是否中断链
                if not hook_result.continue_chain:
                    break

        return current_result

    async def run_on_error(
        self,
        tool_name: str,
        args: dict[str, Any],
        error: Exception,
        context: HookContext | None = None,
    ) -> tuple[bool, Any]:
        """执行 ON_ERROR hooks

        Args:
            tool_name: 工具名
            args: 工具参数
            error: 错误
            context: Hook 上下文

        Returns:
            tuple: (是否重试, 默认值)
        """
        ctx = context or HookContext(tool_name=tool_name, tool_args=args)
        ctx.tool_name = tool_name
        ctx.tool_args = args
        ctx.error = error

        should_retry = False
        default_value = None

        for hook in self._hooks[HookEvent.ON_ERROR]:
            if not hook.should_apply(tool_name):
                continue

            result = await hook.execute(ctx)

            if result:
                if result.should_retry:
                    should_retry = True
                    ctx.retry_count += 1

                if result.default_value is not None:
                    default_value = result.default_value

                if not result.continue_chain:
                    break

        return should_retry, default_value

    async def execute_with_hooks(
        self,
        tool_name: str,
        tool_func: Callable,
        args: dict[str, Any],
        context: HookContext | None = None,
    ) -> Any:
        """带 Hook 的工具执行

        完整流程：BEFORE -> 执行 -> AFTER（或 ON_ERROR）

        Args:
            tool_name: 工具名
            tool_func: 工具函数
            args: 工具参数
            context: Hook 上下文

        Returns:
            Any: 工具执行结果
        """
        ctx = context or HookContext(tool_name=tool_name, tool_args=args)

        # 1. BEFORE_TOOL
        modified_args, skip = await self.run_before(tool_name, args, ctx)

        if skip:
            logger.info(f"Tool {tool_name} execution skipped by hook")
            return None

        # 2. 执行工具（带重试）
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # 执行工具函数
                result = tool_func(**modified_args)
                if asyncio.iscoroutine(result):
                    result = await result

                # 3. AFTER_TOOL
                final_result = await self.run_after(
                    tool_name, modified_args, result, ctx
                )
                return final_result

            except Exception as e:
                logger.error(f"Tool {tool_name} execution error: {e}")

                # 4. ON_ERROR
                should_retry, default_value = await self.run_on_error(
                    tool_name, modified_args, e, ctx
                )

                if should_retry and retry_count < max_retries - 1:
                    retry_count += 1
                    logger.info(
                        f"Retrying tool {tool_name} (attempt {retry_count + 1})"
                    )
                    continue

                if default_value is not None:
                    return default_value

                raise

        raise RuntimeError(f"Tool {tool_name} failed after {max_retries} retries")


# ==================== 预置 Hooks ====================


class builtin_hooks:
    """预置 Hook 函数"""

    @staticmethod
    def log_execution(
        event: HookEvent = HookEvent.AFTER_TOOL,
        logger_func: Callable | None = None,
    ) -> ToolHook:
        """日志记录 Hook

        Args:
            event: 监听的事件
            logger_func: 自定义日志函数

        Returns:
            ToolHook
        """
        _log = logger_func or logger.info

        def callback(ctx: HookContext) -> HookResult:
            if event == HookEvent.BEFORE_TOOL:
                _log(f"[BEFORE] Tool: {ctx.tool_name}, Args: {ctx.tool_args}")
            elif event == HookEvent.AFTER_TOOL:
                _log(
                    f"[AFTER] Tool: {ctx.tool_name}, Result: {str(ctx.tool_result)[:100]}"
                )
            elif event == HookEvent.ON_ERROR:
                _log(f"[ERROR] Tool: {ctx.tool_name}, Error: {ctx.error}")
            return HookResult()

        return ToolHook(event=event, callback=callback)

    @staticmethod
    def validate_args(
        schema: dict[str, Any],
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """参数验证 Hook

        Args:
            schema: JSON Schema 格式的参数验证规则
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """

        def callback(ctx: HookContext) -> HookResult:
            # 简单验证：检查必需参数
            required = schema.get("required", [])
            properties = schema.get("properties", {})

            missing = []
            for req_field in required:
                if req_field not in ctx.tool_args or ctx.tool_args[req_field] is None:
                    missing.append(req_field)

            if missing:
                raise ValueError(f"Missing required arguments: {missing}")

            # 类型检查（简化版）
            for prop_name, field_schema in properties.items():
                if prop_name in ctx.tool_args:
                    expected_type = field_schema.get("type")
                    value = ctx.tool_args[prop_name]

                    if expected_type == "string" and not isinstance(value, str):
                        raise TypeError(f"Argument '{field}' must be string")
                    elif expected_type == "number" and not isinstance(
                        value, (int, float)
                    ):
                        raise TypeError(f"Argument '{field}' must be number")
                    elif expected_type == "boolean" and not isinstance(value, bool):
                        raise TypeError(f"Argument '{field}' must be boolean")
                    elif expected_type == "array" and not isinstance(value, list):
                        raise TypeError(f"Argument '{field}' must be array")
                    elif expected_type == "object" and not isinstance(value, dict):
                        raise TypeError(f"Argument '{field}' must be object")

            return HookResult()

        return ToolHook(
            event=HookEvent.BEFORE_TOOL,
            callback=callback,
            tool_filter=tool_filter,
        )

    @staticmethod
    def cache_result(
        cache: dict[str, Any] | None = None,
        key_func: Callable[[str, dict], str] | None = None,
        ttl_seconds: int = 3600,
    ) -> ToolHook:
        """结果缓存 Hook

        Args:
            cache: 缓存字典
            key_func: 缓存键生成函数
            ttl_seconds: 缓存过期时间

        Returns:
            ToolHook
        """
        _cache = cache or {}
        import time

        def callback(ctx: HookContext) -> HookResult:
            # 生成缓存键
            if key_func:
                cache_key = key_func(ctx.tool_name, ctx.tool_args)
            else:
                import hashlib
                import json

                args_str = json.dumps(ctx.tool_args, sort_keys=True)
                args_hash = hashlib.md5(args_str.encode()).hexdigest()
                cache_key = f"{ctx.tool_name}:{args_hash}"

            # BEFORE: 检查缓存
            if cache_key in _cache:
                cached = _cache[cache_key]
                if time.time() - cached["timestamp"] < ttl_seconds:
                    logger.debug(f"Cache hit for {cache_key}")
                    return HookResult(
                        skip_execution=True,
                        modified_result=cached["value"],
                    )

            # AFTER: 存入缓存（通过 metadata 传递）
            if ctx.tool_result is not None:
                _cache[cache_key] = {
                    "value": ctx.tool_result,
                    "timestamp": time.time(),
                }
                logger.debug(f"Cached result for {cache_key}")

            return HookResult()

        # 需要两个 Hook：BEFORE 检查缓存，AFTER 存入缓存
        # 这里返回 AFTER hook，BEFORE 逻辑通过检查 tool_result 判断
        return ToolHook(
            event=HookEvent.AFTER_TOOL,
            callback=callback,
        )

    @staticmethod
    def retry_on_error(
        max_retries: int = 3,
        retry_delay: float = 1.0,
        exceptions: tuple = (Exception,),
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """错误重试 Hook

        Args:
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            exceptions: 可重试的异常类型
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """
        import time

        def callback(ctx: HookContext) -> HookResult:
            if isinstance(ctx.error, exceptions):
                if ctx.retry_count < max_retries:
                    time.sleep(retry_delay)
                    return HookResult(should_retry=True)
            return HookResult()

        return ToolHook(
            event=HookEvent.ON_ERROR,
            callback=callback,
            tool_filter=tool_filter,
        )

    @staticmethod
    def rate_limit(
        max_calls: int = 10,
        window_seconds: float = 60.0,
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """速率限制 Hook

        Args:
            max_calls: 时间窗口内最大调用次数
            window_seconds: 时间窗口（秒）
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """
        import time

        call_times: list[float] = []

        def callback(ctx: HookContext) -> HookResult:
            now = time.time()

            # 清理过期记录
            nonlocal call_times
            call_times = [t for t in call_times if now - t < window_seconds]

            if len(call_times) >= max_calls:
                raise RuntimeError(
                    f"Rate limit exceeded: {max_calls} calls per {window_seconds}s"
                )

            call_times.append(now)
            return HookResult()

        return ToolHook(
            event=HookEvent.BEFORE_TOOL,
            callback=callback,
            tool_filter=tool_filter,
        )

    @staticmethod
    def timeout(
        timeout_seconds: float = 30.0,
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """超时控制 Hook

        注意：这只是一个标记 Hook，实际超时需要在 execute_with_hooks 中实现

        Args:
            timeout_seconds: 超时时间
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """

        def callback(ctx: HookContext) -> HookResult:
            ctx.metadata["timeout"] = timeout_seconds
            return HookResult()

        return ToolHook(
            event=HookEvent.BEFORE_TOOL,
            callback=callback,
            tool_filter=tool_filter,
        )

    @staticmethod
    def transform_args(
        transformer: Callable[[dict[str, Any]], dict[str, Any]],
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """参数转换 Hook

        Args:
            transformer: 参数转换函数
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """

        def callback(ctx: HookContext) -> HookResult:
            new_args = transformer(ctx.tool_args)
            return HookResult(modified_args=new_args)

        return ToolHook(
            event=HookEvent.BEFORE_TOOL,
            callback=callback,
            tool_filter=tool_filter,
        )

    @staticmethod
    def transform_result(
        transformer: Callable[[Any], Any],
        tool_filter: list[str] | None = None,
    ) -> ToolHook:
        """结果转换 Hook

        Args:
            transformer: 结果转换函数
            tool_filter: 工具名过滤

        Returns:
            ToolHook
        """

        def callback(ctx: HookContext) -> HookResult:
            new_result = transformer(ctx.tool_result)
            return HookResult(modified_result=new_result)

        return ToolHook(
            event=HookEvent.AFTER_TOOL,
            callback=callback,
            tool_filter=tool_filter,
        )


# ==================== 装饰器 ====================


def hook(
    event: HookEvent,
    tool_filter: list[str] | None = None,
    priority: int = 100,
):
    """Hook 装饰器

    将函数转换为 ToolHook

    Example:
        @hook(HookEvent.BEFORE_TOOL, tool_filter=["search"])
        async def log_search(ctx: HookContext):
            print(f"Searching: {ctx.tool_args}")
    """

    def decorator(func: Callable) -> ToolHook:
        return ToolHook(
            event=event,
            callback=func,
            tool_filter=tool_filter,
            priority=priority,
        )

    return decorator
