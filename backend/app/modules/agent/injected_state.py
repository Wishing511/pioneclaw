"""
Injected State 工具状态注入系统

借鉴 PraisonAI Injected State：
- Injected[T] 类型标记：参数标记为注入类型，不暴露在 schema 中
- AgentState：注入到工具的 Agent 运行时状态
- inject_state：运行时注入状态的函数包装器

使用场景：
- 工具需要访问 Agent ID 或 Session ID
- 工具需要访问对话历史
- 工具需要访问工具调用历史
- 工具需要访问 Agent 记忆

示例：
    def my_tool(
        query: str,  # 正常参数，出现在 schema 中
        agent_state: Injected[AgentState],  # 注入参数，不出现在 schema 中
    ) -> str:
        return f"Query from {agent_state.agent_name}: {query}"
"""

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    Any,
    Generic,
    TypeVar,
    get_args,
    get_origin,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Injected(Generic[T]):
    """类型标记，用于注入 Agent 状态

    借鉴 PraisonAI Injected[T] 模式

    当工具参数类型为 Injected[T] 时：
    1. 该参数不出现在工具的公开 JSON Schema 中
    2. 运行时自动注入当前 Agent 的状态

    Example:
        def search_tool(
            query: str,
            session_id: Injected[str],  # 自动注入，不暴露
        ) -> str:
            return f"Searching in session {session_id}: {query}"
    """

    pass


def is_injected_type(param_type: Any) -> bool:
    """检查参数类型是否为 Injected[T]"""
    origin = get_origin(param_type)
    if origin is None:
        # 检查是否是 Injected 的子类
        try:
            return isinstance(param_type, type) and issubclass(param_type, Injected)
        except TypeError:
            return False
    # 检查 origin 是否是 Injected
    try:
        return origin is Injected or (
            isinstance(origin, type) and issubclass(origin, Injected)
        )
    except TypeError:
        return False


def get_injected_inner_type(param_type: Any) -> type | None:
    """获取 Injected[T] 中的 T 类型"""
    args = get_args(param_type)
    if args:
        return args[0]
    return None


@dataclass
class AgentState:
    """注入到工具的 Agent 运行时状态

    借鉴 PraisonAI AgentState
    """

    agent_id: str
    agent_name: str
    session_id: str | None = None
    user_id: int | None = None
    conversation_id: str | None = None

    # 对话信息
    last_user_message: str | None = None
    last_assistant_message: str | None = None
    message_count: int = 0

    # 工具调用历史
    tool_history: list[dict[str, Any]] = field(default_factory=list)
    tool_call_count: int = 0

    # 记忆引用
    memory: Any | None = None  # MemoryStore 或类似对象

    # 上下文信息
    context: dict[str, Any] | None = None

    # 自定义元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_tool_history_by_name(self, tool_name: str) -> list[dict[str, Any]]:
        """获取指定工具的调用历史"""
        return [h for h in self.tool_history if h.get("tool_name") == tool_name]

    def get_last_tool_result(self, tool_name: str | None = None) -> Any | None:
        """获取最后一次工具调用结果"""
        if not self.tool_history:
            return None
        if tool_name:
            for h in reversed(self.tool_history):
                if h.get("tool_name") == tool_name:
                    return h.get("result")
            return None
        return self.tool_history[-1].get("result")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "last_user_message": self.last_user_message,
            "last_assistant_message": self.last_assistant_message,
            "message_count": self.message_count,
            "tool_call_count": self.tool_call_count,
            "metadata": self.metadata,
        }


@dataclass
class InjectedContext:
    """注入上下文容器

    存储当前 Agent 状态，用于工具执行时注入
    """

    state: AgentState
    tools_registry: Any | None = None
    provider: Any | None = None

    # 额外注入对象
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """从 extra 中获取值"""
        return self.extra.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置 extra 值"""
        self.extra[key] = value


class StateInjector:
    """状态注入器

    管理工具参数的状态注入
    """

    def __init__(self):
        self._current_context: InjectedContext | None = None

    def set_context(self, context: InjectedContext) -> None:
        """设置当前注入上下文"""
        self._current_context = context

    def clear_context(self) -> None:
        """清除当前上下文"""
        self._current_context = None

    def get_context(self) -> InjectedContext | None:
        """获取当前上下文"""
        return self._current_context

    def get_state(self) -> AgentState | None:
        """获取当前 Agent 状态"""
        if self._current_context:
            return self._current_context.state
        return None

    def inject_into_args(
        self,
        tool_func: Callable,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """注入状态到工具参数

        扫描工具函数的参数，找出 Injected[T] 类型的参数，
        从当前上下文中获取对应值并注入。

        Args:
            tool_func: 工具函数
            args: 原始参数字典

        Returns:
            Dict: 注入后的参数字典
        """
        if not self._current_context:
            return args

        sig = inspect.signature(tool_func)
        injected_args = dict(args)

        for param_name, param in sig.parameters.items():
            if param_name in injected_args:
                continue  # 已有值，跳过

            param_type = param.annotation
            if param_type is inspect.Parameter.empty:
                continue

            if is_injected_type(param_type):
                inner_type = get_injected_inner_type(param_type)

                # 根据 inner_type 注入对应值
                injected_value = self._resolve_injection(inner_type, param_name)

                if injected_value is not None:
                    injected_args[param_name] = injected_value
                elif param.default is not inspect.Parameter.empty:
                    injected_args[param_name] = param.default

        return injected_args

    def _resolve_injection(
        self,
        inner_type: type | None,
        param_name: str,
    ) -> Any:
        """解析注入值"""
        if self._current_context is None:
            return None

        state = self._current_context.state

        # AgentState 类型
        if inner_type is AgentState or inner_type is None:
            return state

        # str 类型（通常用于 session_id, agent_id 等）
        if inner_type is str:
            if param_name == "session_id":
                return state.session_id
            elif param_name == "agent_id":
                return state.agent_id
            elif param_name == "conversation_id":
                return state.conversation_id
            elif param_name == "user_id":
                return str(state.user_id) if state.user_id else None
            elif param_name == "last_user_message":
                return state.last_user_message

        # int 类型
        if inner_type is int:
            if param_name == "user_id":
                return state.user_id
            elif param_name == "message_count":
                return state.message_count
            elif param_name == "tool_call_count":
                return state.tool_call_count

        # list 类型
        if inner_type is list or (
            hasattr(inner_type, "__origin__") and inner_type.__origin__ is list
        ):
            if param_name == "tool_history":
                return state.tool_history

        # dict 类型
        if inner_type is dict or (
            hasattr(inner_type, "__origin__") and inner_type.__origin__ is dict
        ):
            if param_name == "metadata":
                return state.metadata
            elif param_name == "context":
                return state.context

        # 从 extra 中查找
        return self._current_context.get(param_name)

    def wrap_tool(self, tool_func: Callable) -> Callable:
        """包装工具函数，自动注入状态

        Args:
            tool_func: 原始工具函数

        Returns:
            Callable: 包装后的函数
        """

        def wrapper(**kwargs):
            # 注入状态
            injected_kwargs = self.inject_into_args(tool_func, kwargs)

            # 调用原始函数
            result = tool_func(**injected_kwargs)

            # 处理异步
            if asyncio.iscoroutine(result):
                return result

            return result

        # 保留原始函数的元数据
        wrapper.__name__ = tool_func.__name__
        wrapper.__doc__ = tool_func.__doc__
        wrapper.__wrapped__ = tool_func

        return wrapper

    def filter_schema_for_llm(self, schema: dict[str, Any]) -> dict[str, Any]:
        """过滤 schema，移除 Injected 参数

        Injected 参数不应该出现在 LLM 可见的 schema 中

        Args:
            schema: 原始 JSON Schema

        Returns:
            Dict: 过滤后的 Schema
        """
        if not isinstance(schema, dict):
            return schema

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 检查哪些属性是 Injected 类型
        filtered_properties = {}
        filtered_required = []

        for prop_name, prop_schema in properties.items():
            # 检查是否有 x-injected 标记
            if prop_schema.get("x-injected") is True:
                continue  # 跳过 Injected 参数

            filtered_properties[prop_name] = prop_schema
            if prop_name in required:
                filtered_required.append(prop_name)

        result = dict(schema)
        result["properties"] = filtered_properties
        result["required"] = filtered_required

        return result


# 全局注入器实例
_global_injector: StateInjector | None = None


def get_state_injector() -> StateInjector:
    """获取全局状态注入器"""
    global _global_injector
    if _global_injector is None:
        _global_injector = StateInjector()
    return _global_injector


def reset_state_injector() -> None:
    """重置全局注入器"""
    global _global_injector
    _global_injector = None


# ==================== 装饰器 ====================


def injectable(func: Callable) -> Callable:
    """标记函数为可注入

    被 @injectable 装饰的函数会在调用时自动注入 Injected 参数

    Example:
        @injectable
        def my_tool(
            query: str,
            state: Injected[AgentState],
        ) -> str:
            return f"Query from {state.agent_name}: {query}"
    """
    injector = get_state_injector()
    return injector.wrap_tool(func)


def with_state(**injections: Any) -> Callable:
    """装饰器：注入特定状态值

    Example:
        @with_state(session_id="default-session")
        def my_tool(query: str, session_id: Injected[str]) -> str:
            return f"Session: {session_id}, Query: {query}"
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(**kwargs):
            # 合并注入值
            for key, value in injections.items():
                if key not in kwargs:
                    kwargs[key] = value
            return func(**kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__wrapped__ = func
        return wrapper

    return decorator


# ==================== 工具 Schema 辅助 ====================


def mark_injected_in_schema(
    schema: dict[str, Any],
    injected_params: list[str],
) -> dict[str, Any]:
    """在 schema 中标记 Injected 参数

    Args:
        schema: 原始 schema
        injected_params: 需要标记为 Injected 的参数名列表

    Returns:
        Dict: 标记后的 schema
    """
    result = dict(schema)
    properties = result.get("properties", {})

    for param_name in injected_params:
        if param_name in properties:
            properties[param_name] = dict(properties[param_name])
            properties[param_name]["x-injected"] = True

    # 从 required 中移除 Injected 参数
    required = result.get("required", [])
    result["required"] = [r for r in required if r not in injected_params]

    return result
