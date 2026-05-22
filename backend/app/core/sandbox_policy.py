"""
工具级沙箱策略

借鉴 OpenClaw sandbox-tool-policy.ts

核心思路：
- 每个 Agent 可配置工具 allow/deny 策略
- deny 优先级最高，被禁止的工具一定不可执行
- 如果设置了 allow 列表，只有列表中的工具可执行
- also_allow 在 allow 列表基础上追加，无 allow 时隐式允许所有 + 额外列表
- AgentLoop 执行工具前检查策略，被拒工具返回错误消息
"""

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolPolicyConfig(BaseModel):
    """工具策略配置

    借鉴 OpenClaw sandbox-tool-policy.ts 的 SandboxToolPolicyConfig:
    - allow: 允许的工具列表（空列表=允许所有）
    - also_allow: 额外允许（在 allow 基础上追加）
    - deny: 禁止的工具列表（优先级最高）
    """

    allow: list[str] = []
    also_allow: list[str] = []
    deny: list[str] = []


class ToolPolicy:
    """工具执行策略检查器

    借鉴 OpenClaw pickSandboxToolPolicy

    规则：
    1. deny 列表中的工具永远不可执行（最高优先级）
    2. 如果有 allow 列表，工具必须在 allow ∪ also_allow 中
    3. 如果没有 allow 列表但有 also_allow，所有工具 + also_allow 中的工具均可
    4. 如果 allow 和 also_allow 都为空，所有非 deny 工具均可
    """

    def __init__(self, config: ToolPolicyConfig | None = None):
        self.config = config or ToolPolicyConfig()

    def is_allowed(self, tool_name: str) -> tuple:
        """检查工具是否允许执行

        Returns:
            (allowed: bool, reason: str)
        """
        # 规则1: deny 优先级最高
        if tool_name in self.config.deny:
            return False, f"Tool '{tool_name}' is denied by policy"

        # 规则2: 有 allow 列表时，必须在 allow ∪ also_allow 中
        if self.config.allow:
            allowed_set = set(self.config.allow) | set(self.config.also_allow)
            if tool_name not in allowed_set:
                return False, f"Tool '{tool_name}' is not in allow list"
            return True, ""

        # 规则3和4: 无 allow 列表，所有非 deny 工具均可
        return True, ""

    def get_allowed_tools(self, all_tools: list[str]) -> list[str]:
        """从所有工具中过滤出允许的工具"""
        result = []
        for tool_name in all_tools:
            allowed, _ = self.is_allowed(tool_name)
            if allowed:
                result.append(tool_name)
        return result

    def get_denied_tools(self, all_tools: list[str]) -> list[str]:
        """从所有工具中过滤出被禁止的工具"""
        result = []
        for tool_name in all_tools:
            allowed, _ = self.is_allowed(tool_name)
            if not allowed:
                result.append(tool_name)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow": self.config.allow,
            "also_allow": self.config.also_allow,
            "deny": self.config.deny,
        }


def resolve_tool_policy(
    agent_config: dict[str, Any] | None = None,
) -> ToolPolicy | None:
    """从 Agent 配置解析工具策略

    Agent.config JSON 中增加 tool_policy 键：
    ```json
    {
      "tool_policy": {
        "deny": ["shell", "filesystem"],
        "allow": ["echo", "time", "web_search"]
      }
    }
    ```

    Args:
        agent_config: Agent 的 config 字典

    Returns:
        ToolPolicy 实例，如果未配置则返回 None（无限制）
    """
    if not agent_config or "tool_policy" not in agent_config:
        return None

    try:
        policy_config = ToolPolicyConfig(**agent_config["tool_policy"])
        return ToolPolicy(policy_config)
    except Exception as e:
        logger.warning(f"Failed to parse tool_policy from agent config: {e}")
        return None
