"""
阶段 X 测试 — 分层上下文文件 + Prompt Caching

覆盖：
- CONTEXT_FILE_ORDER 优先级
- ContextFileLoader（workspace > builtin、load_all、load_stable、load_dynamic、缓存）
- IdentityFile 解析（parse_identity_md、merge_identity_content）
- PromptCacheStrategy（compute_stable_prefix、split_for_caching、has_stable_prefix_changed、format_for_llm）
- ContextBuilder 集成（context_file_loader、prompt_cache 属性）
"""

import tempfile
from pathlib import Path

from app.modules.agent.context import ContextBuilder
from app.modules.agent.context_files import (
    CONTEXT_FILE_ORDER,
    DYNAMIC_FILES,
    STABLE_FILES,
    ContextFileLoader,
    IdentityFile,
    PromptCacheStrategy,
    merge_identity_content,
    parse_identity_md,
)

# ==================== 上下文文件优先级 ====================


class TestContextFileOrder:
    def test_order_keys(self):
        assert "agents.md" in CONTEXT_FILE_ORDER
        assert "soul.md" in CONTEXT_FILE_ORDER
        assert "identity.md" in CONTEXT_FILE_ORDER
        assert "user.md" in CONTEXT_FILE_ORDER
        assert "tools.md" in CONTEXT_FILE_ORDER
        assert "bootstrap.md" in CONTEXT_FILE_ORDER
        assert "memory.md" in CONTEXT_FILE_ORDER

    def test_order_values(self):
        assert CONTEXT_FILE_ORDER["agents.md"] < CONTEXT_FILE_ORDER["soul.md"]
        assert CONTEXT_FILE_ORDER["soul.md"] < CONTEXT_FILE_ORDER["identity.md"]
        assert CONTEXT_FILE_ORDER["identity.md"] < CONTEXT_FILE_ORDER["user.md"]
        assert CONTEXT_FILE_ORDER["user.md"] < CONTEXT_FILE_ORDER["tools.md"]
        assert CONTEXT_FILE_ORDER["tools.md"] < CONTEXT_FILE_ORDER["bootstrap.md"]
        assert CONTEXT_FILE_ORDER["bootstrap.md"] < CONTEXT_FILE_ORDER["memory.md"]

    def test_stable_files(self):
        assert "agents.md" in STABLE_FILES
        assert "soul.md" in STABLE_FILES
        assert "identity.md" in STABLE_FILES
        assert "memory.md" not in STABLE_FILES

    def test_dynamic_files(self):
        assert "memory.md" in DYNAMIC_FILES
        assert "bootstrap.md" in DYNAMIC_FILES
        assert "agents.md" not in DYNAMIC_FILES


# ==================== IdentityFile 解析 ====================


class TestParseIdentityMd:
    def test_parse_list_format(self):
        content = "- Name: Alice\n- Emoji: 🤖\n- Theme: 科技感\n- Creature: AI 助手\n- Vibe: 专业"
        identity = parse_identity_md(content)
        assert identity.name == "Alice"
        assert identity.emoji == "🤖"
        assert identity.theme == "科技感"
        assert identity.creature == "AI 助手"
        assert identity.vibe == "专业"

    def test_parse_plain_format(self):
        content = "Name: Bob\nEmoji: 🐱\nVibe: 温暖"
        identity = parse_identity_md(content)
        assert identity.name == "Bob"
        assert identity.emoji == "🐱"
        assert identity.vibe == "温暖"

    def test_parse_empty(self):
        identity = parse_identity_md("")
        assert identity.name is None
        assert identity.emoji is None

    def test_parse_partial(self):
        content = "- Name: Eve\n- Theme: 赛博朋克"
        identity = parse_identity_md(content)
        assert identity.name == "Eve"
        assert identity.theme == "赛博朋克"
        assert identity.vibe is None

    def test_to_prompt(self):
        identity = IdentityFile(name="小爪", emoji="🐾", vibe="专业但温暖")
        prompt = identity.to_prompt()
        assert "小爪" in prompt
        assert "🐾" in prompt
        assert "专业但温暖" in prompt
        assert "# 身份信息" in prompt

    def test_to_prompt_empty(self):
        identity = IdentityFile()
        prompt = identity.to_prompt()
        assert prompt == ""

    def test_to_dict(self):
        identity = IdentityFile(name="A", emoji="B", theme="C")
        d = identity.to_dict()
        assert d["name"] == "A"
        assert d["emoji"] == "B"
        assert d["theme"] == "C"
        assert d["creature"] is None


class TestMergeIdentityContent:
    def test_update_existing(self):
        existing = "- Name: Alice\n- Emoji: 🤖\n- Vibe: 专业"
        result = merge_identity_content(existing, {"Name": "Bob"})
        assert "Bob" in result
        assert "🤖" in result

    def test_add_new_field(self):
        existing = "- Name: Alice\n- Emoji: 🤖"
        result = merge_identity_content(existing, {"Vibe": "温暖"})
        assert "温暖" in result
        assert "Alice" in result

    def test_empty_existing(self):
        result = merge_identity_content("", {"Name": "Eve", "Emoji": "🌟"})
        assert "Eve" in result
        assert "🌟" in result


# ==================== ContextFileLoader ====================


class TestContextFileLoader:
    def test_load_builtin_files(self):
        """加载内置默认上下文文件"""
        loader = ContextFileLoader()
        # 内置 contexts/ 目录有 agents.md
        content = loader.load_file("agents.md")
        assert content is not None
        assert "Agent" in content or "行为" in content

    def test_load_builtin_identity(self):
        """加载内置 IDENTITY.md"""
        loader = ContextFileLoader()
        content = loader.load_file("identity.md")
        assert content is not None
        assert "小助手" in content

    def test_load_nonexistent(self):
        loader = ContextFileLoader()
        content = loader.load_file("nonexistent.md")
        assert content is None

    def test_workspace_overrides_builtin(self):
        """workspace 文件优先于 builtin"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 写入 workspace 版本的 agents.md
            workspace_file = Path(tmpdir) / "agents.md"
            workspace_file.write_text("workspace agents content", encoding="utf-8")

            loader = ContextFileLoader(workspace_path=tmpdir)
            content = loader.load_file("agents.md")
            assert content == "workspace agents content"

    def test_load_all_sorted(self):
        """load_all 返回按优先级排序的列表"""
        loader = ContextFileLoader()
        results = loader.load_all()
        assert len(results) > 0
        # 验证排序
        priorities = [r[2] for r in results]
        assert priorities == sorted(priorities)

    def test_load_stable_only(self):
        """load_stable 只返回稳定层文件"""
        loader = ContextFileLoader()
        results = loader.load_stable()
        for filename, _, _ in results:
            assert filename in STABLE_FILES

    def test_load_dynamic_only(self):
        """load_dynamic 只返回动态层文件"""
        loader = ContextFileLoader()
        results = loader.load_dynamic()
        for filename, _, _ in results:
            assert filename in DYNAMIC_FILES

    def test_caching(self):
        """文件缓存生效"""
        loader = ContextFileLoader()
        content1 = loader.load_file("agents.md")
        content2 = loader.load_file("agents.md")
        assert content1 == content2
        assert "agents.md" in loader._cache

    def test_clear_cache(self):
        loader = ContextFileLoader()
        loader.load_file("agents.md")
        assert "agents.md" in loader._cache
        loader.clear_cache()
        assert "agents.md" not in loader._cache

    def test_load_identity(self):
        """load_identity 解析 IDENTITY.md"""
        loader = ContextFileLoader()
        identity = loader.load_identity()
        assert identity is not None
        assert identity.name is not None

    def test_load_identity_nonexistent(self):
        """workspace 无 identity.md 时返回 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ContextFileLoader(workspace_path=tmpdir)
            loader._cache.clear()
            # 临时覆盖 builtin_path 为空目录
            loader.builtin_path = tmpdir
            identity = loader.load_identity()
            assert identity is None

    def test_build_system_prompt(self):
        """build_system_prompt 组装完整提示词"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建最小化的 workspace 上下文
            (Path(tmpdir) / "agents.md").write_text("Agent rules", encoding="utf-8")
            (Path(tmpdir) / "soul.md").write_text("Soul content", encoding="utf-8")

            loader = ContextFileLoader(workspace_path=tmpdir)
            prompt = loader.build_system_prompt(
                persona_prompt="## identity.md\nMy name is Test",
                skills_text="Tool: echo",
                memory_text="Recent memory entry",
            )

            assert "Agent rules" in prompt
            assert "Soul content" in prompt
            assert "My name is Test" in prompt
            assert "Tool: echo" in prompt
            assert "Recent memory entry" in prompt

    def test_build_system_prompt_persona_replaces_identity(self):
        """persona_prompt 替换 identity.md"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "identity.md").write_text(
                "- Name: Builtin", encoding="utf-8"
            )

            loader = ContextFileLoader(workspace_path=tmpdir)
            prompt = loader.build_system_prompt(
                persona_prompt="Dynamic identity: Custom"
            )

            # persona_prompt 应该替换 builtin identity.md
            assert "Dynamic identity: Custom" in prompt


# ==================== PromptCacheStrategy ====================


class TestPromptCacheStrategy:
    def test_compute_stable_prefix(self):
        strategy = PromptCacheStrategy()
        prompt = "Stable content here\n\n## memory.md\nDynamic content"
        stable = strategy.compute_stable_prefix(prompt)
        assert "Stable content" in stable
        assert "Dynamic content" not in stable
        assert strategy.stable_prefix_hash is not None

    def test_compute_stable_prefix_no_boundary(self):
        """无分界标记时，整个 prompt 视为稳定层"""
        strategy = PromptCacheStrategy()
        prompt = "All content is stable"
        stable = strategy.compute_stable_prefix(prompt)
        assert stable == prompt

    def test_split_for_caching(self):
        strategy = PromptCacheStrategy()
        prompt = "Stable\n\n## memory.md\nDynamic"
        split = strategy.split_for_caching(prompt)
        assert "Stable" in split["stable"]
        assert "Dynamic" in split["dynamic"]
        assert "## memory.md" in split["dynamic"]

    def test_split_for_caching_no_boundary(self):
        strategy = PromptCacheStrategy()
        prompt = "All stable"
        split = strategy.split_for_caching(prompt)
        assert split["stable"] == prompt
        assert split["dynamic"] == ""

    def test_has_stable_prefix_changed_first_call(self):
        """首次调用总是返回 True"""
        strategy = PromptCacheStrategy()
        assert (
            strategy.has_stable_prefix_changed("Stable\n\n## memory.md\nDynamic")
            is True
        )

    def test_has_stable_prefix_changed_same(self):
        """相同稳定层返回 False"""
        strategy = PromptCacheStrategy()
        prompt = "Stable\n\n## memory.md\nDynamic v1"
        strategy.has_stable_prefix_changed(prompt)
        # 只改动态层
        prompt2 = "Stable\n\n## memory.md\nDynamic v2"
        assert strategy.has_stable_prefix_changed(prompt2) is False

    def test_has_stable_prefix_changed_different(self):
        """稳定层变化返回 True"""
        strategy = PromptCacheStrategy()
        strategy.has_stable_prefix_changed("Stable A\n\n## memory.md\nDynamic")
        assert (
            strategy.has_stable_prefix_changed("Stable B\n\n## memory.md\nDynamic")
            is True
        )

    def test_format_for_llm_anthropic(self):
        """Anthropic provider 返回 content blocks"""
        strategy = PromptCacheStrategy()
        prompt = "Stable\n\n## memory.md\nDynamic"
        result = strategy.format_for_llm(prompt, provider="anthropic")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert result[1].get("cache_control") is None

    def test_format_for_llm_non_anthropic(self):
        """非 Anthropic provider 返回原始字符串"""
        strategy = PromptCacheStrategy()
        prompt = "Stable\n\n## memory.md\nDynamic"
        result = strategy.format_for_llm(prompt, provider="openai")
        assert isinstance(result, str)
        assert result == prompt

    def test_format_for_llm_no_dynamic(self):
        """无动态层时返回原始字符串"""
        strategy = PromptCacheStrategy()
        prompt = "All stable content"
        result = strategy.format_for_llm(prompt, provider="anthropic")
        assert isinstance(result, str)
        assert result == prompt


# ==================== ContextBuilder 集成 ====================


class TestContextBuilderIntegration:
    def test_has_context_file_loader(self):
        """ContextBuilder 默认创建 ContextFileLoader"""
        builder = ContextBuilder(workspace=Path("/tmp"))
        assert builder.context_file_loader is not None
        assert isinstance(builder.context_file_loader, ContextFileLoader)

    def test_has_prompt_cache(self):
        """ContextBuilder 默认创建 PromptCacheStrategy"""
        builder = ContextBuilder(workspace=Path("/tmp"))
        assert builder.prompt_cache is not None
        assert isinstance(builder.prompt_cache, PromptCacheStrategy)

    def test_custom_context_file_loader(self):
        """可传入自定义 ContextFileLoader"""
        loader = ContextFileLoader(workspace_path="/custom")
        builder = ContextBuilder(workspace=Path("/tmp"), context_file_loader=loader)
        assert builder.context_file_loader is loader

    def test_custom_prompt_cache(self):
        cache = PromptCacheStrategy()
        builder = ContextBuilder(workspace=Path("/tmp"), prompt_cache_strategy=cache)
        assert builder.prompt_cache is cache

    def test_build_system_prompt_still_works(self):
        """原有 build_system_prompt 接口不变"""
        builder = ContextBuilder(workspace=Path("/tmp"))
        prompt = builder.build_system_prompt()
        assert prompt  # 非空
        assert "核心身份" in prompt or "小助手" in prompt
