"""
执行上下文 — 让工具能获取当前用户/会话信息
"""

from contextvars import ContextVar

current_user_id: ContextVar[int] = ContextVar("current_user_id", default=0)
current_runner_id: ContextVar[int] = ContextVar("current_runner_id", default=0)
