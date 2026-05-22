"""
分层上下文文件系统 + Prompt Caching

借鉴 OpenClaw system-prompt.ts + identity-file.ts + identity.ts

核心思路：
- 系统提示词由分层上下文文件组装，而非单一字符串
- 上下文文件按优先级排序：agents.md(10) → soul.md(20) → identity.md(30) → user.md(40) → tools.md(50) → bootstrap.md(60) → memory.md(70)
- workspace 目录下的文件优先于 builtin 默认文件
- IDENTITY.md 支持结构化字段解析（name/emoji/theme/creature/vibe）
- Prompt Caching：将系统提示词分为稳定层（可缓存）和动态层（每次变化）
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ==================== 上下文文件优先级（借鉴 OpenClaw system-prompt.ts）====================

CONTEXT_FILE_ORDER: dict[str, int] = {
    "agents.md": 10,  # Agent 行为规范
    "soul.md": 20,  # 核心人格/灵魂
    "identity.md": 30,  # 身份信息（名称、emoji、vibe）
    "user.md": 40,  # 用户信息（姓名、偏好）
    "tools.md": 50,  # 工具使用规范
    "bootstrap.md": 60,  # 引导/启动指令
    "memory.md": 70,  # 记忆注入（动态层）
}

# 稳定层文件（变化少，适合 prompt caching）
STABLE_FILES = {"agents.md", "soul.md", "identity.md", "user.md", "tools.md"}

# 动态层文件（每次请求可能变化）
DYNAMIC_FILES = {"memory.md", "bootstrap.md"}


# ==================== IDENTITY.md 文件解析（借鉴 OpenClaw identity-file.ts）====================


@dataclass
class IdentityFile:
    """IDENTITY.md 文件内容

    借鉴 OpenClaw identity-file.ts 的 AgentIdentityFile:
    - name: Agent 名称
    - emoji: Agent 表情符号
    - theme: 主题风格
    - creature: 物种/角色类型
    - vibe: 氛围/态度
    - avatar: 头像（URL 或文件路径）
    """

    name: str | None = None
    emoji: str | None = None
    theme: str | None = None
    creature: str | None = None
    vibe: str | None = None
    avatar: str | None = None
    raw_content: str = ""

    def to_prompt(self) -> str:
        """转换为提示词文本"""
        lines = ["# 身份信息", ""]
        if self.name:
            lines.append(f"- 名称: {self.name}")
        if self.emoji:
            lines.append(f"- Emoji: {self.emoji}")
        if self.theme:
            lines.append(f"- 主题: {self.theme}")
        if self.creature:
            lines.append(f"- 类型: {self.creature}")
        if self.vibe:
            lines.append(f"- 氛围: {self.vibe}")
        return "\n".join(lines) if len(lines) > 2 else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "emoji": self.emoji,
            "theme": self.theme,
            "creature": self.creature,
            "vibe": self.vibe,
            "avatar": self.avatar,
        }


def parse_identity_md(content: str) -> IdentityFile:
    """解析 IDENTITY.md 文件内容

    支持格式：
    - Name: Alice
    - Emoji: 🤖
    - Theme: 科技感
    - Creature: AI 助手
    - Vibe: 专业但不冰冷

    借鉴 OpenClaw identity-file.ts 的 parseIdentityMarkdownContent
    """
    identity = IdentityFile(raw_content=content)

    # 解析 markdown 列表项："- Key: Value"
    field_map = {
        "name": "name",
        "emoji": "emoji",
        "theme": "theme",
        "creature": "creature",
        "vibe": "vibe",
        "avatar": "avatar",
    }

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 匹配 "- Key: Value" 或 "Key: Value"
        match = re.match(r"^(?:-\s+)?(\w+)\s*:\s*(.+)$", line)
        if match:
            key = match.group(1).lower()
            value = match.group(2).strip()
            if key in field_map:
                setattr(identity, field_map[key], value)

    return identity


def merge_identity_content(existing: str, updates: dict[str, str]) -> str:
    """合并 IDENTITY.md 内容（更新指定字段，保留其余）

    借鉴 OpenClaw mergeIdentityMarkdownContent
    """
    lines = existing.split("\n") if existing else []
    updated_keys = set()
    new_lines = []

    for line in lines:
        match = re.match(r"^(?:-\s+)?(\w+)\s*:\s*(.+)$", line.strip())
        if match:
            key = match.group(1).lower()
            if key in updates:
                prefix = "- " if line.strip().startswith("-") else ""
                new_lines.append(f"{prefix}{key.capitalize()}: {updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # 添加新字段
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"- {key.capitalize()}: {value}")

    return "\n".join(new_lines)


# ==================== 分层上下文文件加载器 ====================


class ContextFileLoader:
    """分层上下文文件加载器

    借鉴 OpenClaw system-prompt.ts 的多层上下文加载：
    - 从 workspace 目录加载上下文文件
    - workspace 文件优先于 builtin 默认文件
    - 按优先级排序组装
    """

    def __init__(
        self, workspace_path: str | None = None, builtin_path: str | None = None
    ):
        """
        Args:
            workspace_path: 用户工作空间目录（优先加载）
            builtin_path: 内置默认上下文文件目录
        """
        self.workspace_path = Path(workspace_path) if workspace_path else None
        self.builtin_path = builtin_path or str(Path(__file__).parent / "contexts")
        self._cache: dict[str, str] = {}

    def load_file(self, filename: str) -> str | None:
        """加载单个上下文文件（workspace 优先于 builtin）"""
        if filename in self._cache:
            return self._cache[filename]

        # 优先从 workspace 加载
        if self.workspace_path:
            workspace_file = self.workspace_path / filename
            if workspace_file.exists():
                content = workspace_file.read_text(encoding="utf-8")
                self._cache[filename] = content
                return content

        # 降级到 builtin
        builtin_file = Path(self.builtin_path) / filename
        if builtin_file.exists():
            content = builtin_file.read_text(encoding="utf-8")
            self._cache[filename] = content
            return content

        return None

    def load_all(self) -> list[tuple[str, str, int]]:
        """加载所有上下文文件

        Returns:
            [(filename, content, priority), ...] 按优先级排序
        """
        results = []
        for filename, priority in CONTEXT_FILE_ORDER.items():
            content = self.load_file(filename)
            if content:
                results.append((filename, content, priority))
        results.sort(key=lambda x: x[2])
        return results

    def load_stable(self) -> list[tuple[str, str, int]]:
        """加载稳定层文件（适合 prompt caching）"""
        results = []
        for filename, priority in CONTEXT_FILE_ORDER.items():
            if filename in STABLE_FILES:
                content = self.load_file(filename)
                if content:
                    results.append((filename, content, priority))
        results.sort(key=lambda x: x[2])
        return results

    def load_dynamic(self) -> list[tuple[str, str, int]]:
        """加载动态层文件（每次请求可能变化）"""
        results = []
        for filename, priority in CONTEXT_FILE_ORDER.items():
            if filename in DYNAMIC_FILES:
                content = self.load_file(filename)
                if content:
                    results.append((filename, content, priority))
        results.sort(key=lambda x: x[2])
        return results

    def load_identity(self) -> IdentityFile | None:
        """加载并解析 IDENTITY.md 文件"""
        content = self.load_file("identity.md")
        if content:
            return parse_identity_md(content)
        return None

    def clear_cache(self) -> None:
        """清除文件缓存"""
        self._cache.clear()

    def build_system_prompt(
        self,
        persona_prompt: str = "",
        skills_text: str = "",
        memory_text: str = "",
        dynamic_sections: list[str] | None = None,
    ) -> str:
        """组装完整系统提示词

        稳定层在前（可被 prompt caching 缓存），动态层在后（每次变化）

        Args:
            persona_prompt: PersonaConfig 生成的身份提示词
            skills_text: 技能列表文本
            memory_text: 记忆上下文文本
            dynamic_sections: 额外的动态内容段

        Returns:
            完整的系统提示词
        """
        parts = []

        # 稳定层：从上下文文件加载
        for filename, content, _ in self.load_stable():
            if filename == "identity.md" and persona_prompt:
                # identity.md 被 PersonaConfig 的动态身份替换
                parts.append(persona_prompt)
            else:
                parts.append(f"## {filename}\n{content}")

        # 如果没有 identity.md 但有 persona_prompt，追加
        if persona_prompt and not any(
            f == "identity.md" for f, _, _ in self.load_stable()
        ):
            parts.append(persona_prompt)

        # 技能注入
        if skills_text:
            parts.append(f"## tools.md\n{skills_text}")

        # 动态层
        if memory_text:
            parts.append(f"## memory.md\n{memory_text}")

        # 其他动态段
        if dynamic_sections:
            parts.extend(dynamic_sections)

        return "\n\n".join(parts)


# ==================== Prompt Caching 策略 ====================


class PromptCacheStrategy:
    """Prompt Caching 策略

    借鉴 OpenClaw system-prompt.ts 的 prompt caching：
    - 将系统提示词分为稳定层（identity, skills, tools 规范）和动态层（memory, conversation）
    - 稳定层内容变化少，可被 LLM 的 prompt caching 缓存
    - 动态层每次请求可能变化，放在末尾
    - 通过稳定前缀哈希判断是否需要重新缓存
    """

    def __init__(self):
        self._stable_prefix_hash: str | None = None

    @property
    def stable_prefix_hash(self) -> str | None:
        return self._stable_prefix_hash

    def compute_stable_prefix(
        self, system_prompt: str, cache_boundary: str = "## memory.md"
    ) -> str:
        """计算稳定前缀

        Args:
            system_prompt: 完整系统提示词
            cache_boundary: 稳定层和动态层的分界标记

        Returns:
            稳定层文本
        """
        boundary_idx = system_prompt.find(cache_boundary)
        if boundary_idx > 0:
            stable = system_prompt[:boundary_idx].rstrip()
        else:
            stable = system_prompt

        self._stable_prefix_hash = hashlib.md5(stable.encode("utf-8")).hexdigest()
        return stable

    def split_for_caching(
        self, system_prompt: str, cache_boundary: str = "## memory.md"
    ) -> dict[str, str]:
        """将系统提示词分为稳定层和动态层

        Args:
            system_prompt: 完整系统提示词
            cache_boundary: 分界标记

        Returns:
            {"stable": "...", "dynamic": "..."}
        """
        boundary_idx = system_prompt.find(cache_boundary)
        if boundary_idx > 0:
            return {
                "stable": system_prompt[:boundary_idx].rstrip(),
                "dynamic": system_prompt[boundary_idx:],
            }
        return {"stable": system_prompt, "dynamic": ""}

    def has_stable_prefix_changed(
        self, system_prompt: str, cache_boundary: str = "## memory.md"
    ) -> bool:
        """检查稳定前缀是否变化

        Returns:
            True = 稳定层变化了，需要重新缓存
            False = 稳定层没变，可以复用缓存
        """
        boundary_idx = system_prompt.find(cache_boundary)
        if boundary_idx > 0:
            stable = system_prompt[:boundary_idx].rstrip()
        else:
            stable = system_prompt

        new_hash = hashlib.md5(stable.encode("utf-8")).hexdigest()

        if self._stable_prefix_hash is None:
            self._stable_prefix_hash = new_hash
            return True

        changed = new_hash != self._stable_prefix_hash
        self._stable_prefix_hash = new_hash
        return changed

    def format_for_llm(
        self,
        system_prompt: str,
        cache_boundary: str = "## memory.md",
        provider: str = "anthropic",
    ) -> Any:
        """格式化系统提示词用于 LLM 调用（含 cache_control 标记）

        支持 Anthropic Claude 的 ephemeral cache_control 标记。
        其他 provider 暂不标记（无副作用）。

        Args:
            system_prompt: 完整系统提示词
            cache_boundary: 分界标记
            provider: LLM provider 名称

        Returns:
            格式化后的 content（字符串或 content blocks）
        """
        if provider != "anthropic":
            return system_prompt

        split = self.split_for_caching(system_prompt, cache_boundary)
        stable = split["stable"]
        dynamic = split["dynamic"]

        if not dynamic:
            return system_prompt

        # Anthropic content blocks 格式：稳定层标记 ephemeral cache
        content_blocks = [
            {
                "type": "text",
                "text": stable,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": dynamic,
            },
        ]
        return content_blocks
