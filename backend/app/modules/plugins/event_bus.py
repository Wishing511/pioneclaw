"""
EventBus - 异步事件总线

支持：
- 基于主题的订阅/发布
- 通配符匹配 (tool.* 匹配 tool.start, tool.complete 等)
- 异步事件处理器
- 优先级排序
- 取消订阅
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 事件处理器类型：接收 (event_type, data) 的异步或同步函数
EventHandler = Callable[[str, dict], Any]


@dataclass
class _Subscription:
    """订阅记录"""

    handler: EventHandler
    topic_pattern: str  # 原始模式字符串
    priority: int = 0  # 数值越大越先执行
    _id: int = field(default_factory=lambda: id(object()))  # 用于取消订阅


class EventBus:
    """
    异步事件总线

    用法:
        bus = EventBus()
        bus.subscribe("tool.start", on_tool_start)
        bus.subscribe("tool.*", on_any_tool_event)
        await bus.publish("tool.start", {"tool_name": "search"})
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[_Subscription]] = {}
        self._wildcard_subscriptions: list[_Subscription] = []
        self._counter: int = 0

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """检查主题模式是否匹配"""
        if pattern == topic:
            return True
        if pattern == "*":
            return True  # * 匹配所有主题
        if pattern.endswith(".*"):
            prefix = pattern[:-2]  # 去掉 .*
            return topic == prefix or topic.startswith(prefix + ".")
        # 支持更复杂的通配符：用 . 分隔的 ** 匹配多层
        if "**" in pattern:
            regex = re.escape(pattern).replace(r"\*\*", ".+")
            return bool(re.fullmatch(regex, topic))
        return False

    def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        priority: int = 0,
    ) -> str:
        """
        订阅事件

        Args:
            topic: 主题模式，支持 "tool.*" 通配符
            handler: 事件处理器
            priority: 优先级（数值越大越先执行）

        Returns:
            str: 订阅 ID，可用于取消订阅
        """
        self._counter += 1
        sub_id = f"sub_{self._counter}"
        sub = _Subscription(
            handler=handler,
            topic_pattern=topic,
            priority=priority,
            _id=self._counter,
        )

        if "*" in topic:
            self._wildcard_subscriptions.append(sub)
            self._wildcard_subscriptions.sort(key=lambda s: -s.priority)
        else:
            self._subscriptions.setdefault(topic, []).append(sub)
            self._subscriptions[topic].sort(key=lambda s: -s.priority)

        logger.debug(
            f"[EventBus] 订阅: {topic} -> {handler.__name__} (sub_id={sub_id})"
        )
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """
        取消订阅

        Args:
            sub_id: 订阅时返回的 ID

        Returns:
            bool: 是否成功取消
        """
        # 尝试在精确匹配中查找
        for topic, subs in self._subscriptions.items():
            for i, sub in enumerate(subs):
                if f"sub_{sub._id}" == sub_id:
                    subs.pop(i)
                    logger.debug(f"[EventBus] 取消订阅: {topic} (sub_id={sub_id})")
                    return True

        # 尝试在通配符中查找
        for i, sub in enumerate(self._wildcard_subscriptions):
            if f"sub_{sub._id}" == sub_id:
                self._wildcard_subscriptions.pop(i)
                logger.debug(
                    f"[EventBus] 取消通配符订阅: {sub.topic_pattern} (sub_id={sub_id})"
                )
                return True

        return False

    def unsubscribe_all(self, topic: str | None = None) -> int:
        """
        取消所有订阅（或指定主题的订阅）

        Args:
            topic: 可选，只取消此主题的订阅

        Returns:
            int: 取消的订阅数量
        """
        count = 0
        if topic is None:
            count = sum(len(s) for s in self._subscriptions.values())
            count += len(self._wildcard_subscriptions)
            self._subscriptions.clear()
            self._wildcard_subscriptions.clear()
        else:
            if topic in self._subscriptions:
                count += len(self._subscriptions.pop(topic))
            to_remove = [
                s for s in self._wildcard_subscriptions if s.topic_pattern == topic
            ]
            for s in to_remove:
                self._wildcard_subscriptions.remove(s)
                count += 1

        logger.debug(f"[EventBus] 取消了 {count} 个订阅")
        return count

    async def publish(self, topic: str, data: dict | None = None) -> int:
        """
        发布事件

        Args:
            topic: 事件主题
            data: 事件数据

        Returns:
            int: 处理该事件的处理器数量
        """
        if data is None:
            data = {}

        # 收集所有匹配的处理器
        handlers: list[_Subscription] = []

        # 精确匹配
        if topic in self._subscriptions:
            handlers.extend(self._subscriptions[topic])

        # 通配符匹配
        for sub in self._wildcard_subscriptions:
            if self._topic_matches(sub.topic_pattern, topic):
                handlers.append(sub)

        # 按优先级排序（已在 subscribe 时排好，但合并后需重排）
        handlers.sort(key=lambda s: -s.priority)

        # 执行处理器
        fired = 0
        for sub in handlers:
            try:
                result = sub.handler(topic, data)
                if asyncio.iscoroutine(result):
                    await result
                fired += 1
            except Exception as exc:
                logger.warning(
                    f"[EventBus] 处理器 '{sub.handler.__name__}' "
                    f"处理事件 '{topic}' 失败: {exc}"
                )

        return fired

    def get_subscriptions(self, topic: str | None = None) -> list[dict]:
        """
        获取订阅信息

        Args:
            topic: 可选，筛选指定主题

        Returns:
            List[dict]: 订阅列表
        """
        result = []

        for t, subs in self._subscriptions.items():
            if topic and t != topic:
                continue
            for sub in subs:
                result.append(
                    {
                        "sub_id": f"sub_{sub._id}",
                        "topic": t,
                        "handler": sub.handler.__name__,
                        "priority": sub.priority,
                        "wildcard": False,
                    }
                )

        for sub in self._wildcard_subscriptions:
            if topic and sub.topic_pattern != topic:
                continue
            result.append(
                {
                    "sub_id": f"sub_{sub._id}",
                    "topic": sub.topic_pattern,
                    "handler": sub.handler.__name__,
                    "priority": sub.priority,
                    "wildcard": True,
                }
            )

        return result
