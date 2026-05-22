"""
阶段 AA 测试 — 技能 XML 注入 + 插件 SDK 化

覆盖：
- _xml_escape 特殊字符转义
- build_skills_xml XML 格式输出
- SkillMetadata.install 字段
- Skill.check_install_status 安装状态检查
- _parse_metadata 解析 install 字段
- PioneClawPlugin 基类生命周期
- plugin_metadata 装饰器
- PluginEvent / EventType
- plugin_runtime API
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.modules.agent.skills import (
    Skill,
    SkillMetadata,
    SkillsLoader,
    _xml_escape,
)
from app.modules.plugins.sdk import (
    EventType,
    PioneClawPlugin,
    PluginEvent,
    clear_runtime_context,
    get_config,
    get_db_session,
    get_event_bus,
    plugin_metadata,
    set_runtime_context,
)

# ==================== XML 转义 ====================


class TestXmlEscape:
    def test_ampersand(self):
        assert _xml_escape("a&b") == "a&amp;b"

    def test_lt_gt(self):
        assert _xml_escape("<x>") == "&lt;x&gt;"

    def test_quotes(self):
        assert _xml_escape('"hi"') == "&quot;hi&quot;"
        assert _xml_escape("'hi'") == "&apos;hi&apos;"

    def test_no_escape_needed(self):
        assert _xml_escape("hello world") == "hello world"

    def test_mixed(self):
        result = _xml_escape('a<b&c"d')
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&quot;" in result

    def test_non_string(self):
        assert _xml_escape(123) == "123"


# ==================== build_skills_xml ====================


class TestBuildSkillsXml:
    def _make_loader_with_skills(self, skills_data):
        """创建带指定技能的 SkillsLoader"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            for name, meta in skills_data.items():
                skill_dir = skills_dir / name
                skill_dir.mkdir()
                frontmatter = "---\n"
                frontmatter += f"title: {meta.get('title', '')}\n"
                frontmatter += f"description: {meta.get('description', '')}\n"
                if meta.get("always"):
                    frontmatter += "always: true\n"
                if meta.get("tags"):
                    frontmatter += f"tags: [{', '.join(meta['tags'])}]\n"
                frontmatter += "---\nContent here"
                (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")

            loader = SkillsLoader(skills_dir)
            return loader.build_skills_xml()

    def test_empty_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = SkillsLoader(Path(tmpdir) / "skills", external_skills_dirs=[])
            assert loader.build_skills_xml() == ""

    def test_single_skill_xml(self):
        xml = self._make_loader_with_skills(
            {"weather": {"title": "Weather", "description": "查询天气", "always": True}}
        )
        assert "<available_skills>" in xml
        assert "</available_skills>" in xml
        assert 'name="weather"' in xml
        assert "<description>查询天气</description>" in xml
        assert "<always>true</always>" in xml

    def test_multiple_skills(self):
        xml = self._make_loader_with_skills(
            {
                "weather": {"description": "天气查询"},
                "translator": {"description": "翻译工具"},
            }
        )
        assert 'name="weather"' in xml
        assert 'name="translator"' in xml

    def test_skill_with_tags(self):
        xml = self._make_loader_with_skills(
            {"weather": {"description": "天气", "tags": ["weather", "查询"]}}
        )
        assert "<tags>weather, 查询</tags>" in xml

    def test_no_always_when_false(self):
        xml = self._make_loader_with_skills(
            {"tool": {"description": "A tool", "always": False}}
        )
        assert "<always>" not in xml

    def test_xml_escape_in_description(self):
        xml = self._make_loader_with_skills(
            {"test": {"description": 'Use a <b>bold</b> & "quote"'}}
        )
        assert "&lt;b&gt;" in xml
        assert "&amp;" in xml
        assert "&quot;" in xml

    def test_disabled_skill_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            skill_dir = skills_dir / "disabled_skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\ndescription: test\n---\nContent", encoding="utf-8"
            )
            loader = SkillsLoader(skills_dir)
            loader.disable_skill("disabled_skill")
            xml = loader.build_skills_xml()
            assert "disabled_skill" not in xml


# ==================== SkillMetadata.install ====================


class TestSkillMetadataInstall:
    def test_default_empty(self):
        meta = SkillMetadata()
        assert meta.install == []

    def test_with_install_steps(self):
        meta = SkillMetadata(install=[{"run": "pip install requests"}])
        assert len(meta.install) == 1
        assert meta.install[0]["run"] == "pip install requests"


# ==================== check_install_status ====================


class TestCheckInstallStatus:
    def test_installed_no_requires(self):
        skill = Skill(
            name="test",
            path=Path("/tmp/test"),
            content="",
            metadata=SkillMetadata(requires={}),
        )
        status = skill.check_install_status()
        assert status["installed"] is True
        assert status["missing_bins"] == []
        assert status["missing_env"] == []

    def test_missing_bin(self):
        skill = Skill(
            name="test",
            path=Path("/tmp/test"),
            content="",
            metadata=SkillMetadata(requires={"bins": ["nonexistent_tool_xyz"]}),
        )
        status = skill.check_install_status()
        assert status["installed"] is False
        assert "nonexistent_tool_xyz" in status["missing_bins"]

    def test_missing_env(self):
        with patch.dict(os.environ, {}, clear=True):
            skill = Skill(
                name="test",
                path=Path("/tmp/test"),
                content="",
                metadata=SkillMetadata(requires={"env": ["MY_MISSING_VAR_123"]}),
            )
            status = skill.check_install_status()
            assert status["installed"] is False
            assert "MY_MISSING_VAR_123" in status["missing_env"]

    def test_install_steps_included(self):
        steps = [{"run": "pip install foo"}, {"run": "echo done"}]
        skill = Skill(
            name="test",
            path=Path("/tmp/test"),
            content="",
            metadata=SkillMetadata(install=steps),
        )
        status = skill.check_install_status()
        assert status["steps"] == steps

    def test_present_bin(self):
        skill = Skill(
            name="test",
            path=Path("/tmp/test"),
            content="",
            metadata=SkillMetadata(requires={"bins": ["python"]}),
        )
        status = skill.check_install_status()
        # python should exist on PATH
        assert "python" not in status["missing_bins"]


# ==================== _parse_metadata install ====================


class TestParseInstallMetadata:
    def test_parse_install_json(self):
        content = '---\ninstall: [{"run": "pip install requests"}]\n---\nBody'
        loader = SkillsLoader(Path(tempfile.mkdtemp()) / "skills")
        meta = loader._parse_metadata(content)
        assert len(meta.install) == 1
        assert meta.install[0]["run"] == "pip install requests"

    def test_parse_install_via_metadata_json(self):
        content = (
            '---\nmetadata: {"PioneClaw": {"install": [{"run": "echo hi"}]}}\n---\nBody'
        )
        loader = SkillsLoader(Path(tempfile.mkdtemp()) / "skills")
        meta = loader._parse_metadata(content)
        assert len(meta.install) == 1

    def test_invalid_install_ignored(self):
        content = "---\ninstall: not a list\n---\nBody"
        loader = SkillsLoader(Path(tempfile.mkdtemp()) / "skills")
        meta = loader._parse_metadata(content)
        assert meta.install == []


# ==================== PioneClawPlugin 基类 ====================


class TestPioneClawPlugin:
    def test_default_metadata(self):
        plugin = PioneClawPlugin()
        assert plugin.plugin_id == ""
        assert plugin.plugin_name == ""
        assert plugin.version == "1.0.0"
        assert plugin.description == ""
        assert plugin.dependencies == []

    @pytest.mark.asyncio
    async def test_lifecycle_hooks(self):
        plugin = PioneClawPlugin()
        # 默认钩子不报错
        await plugin.on_load()
        await plugin.on_unload()
        await plugin.on_error(Exception("test"))

    @pytest.mark.asyncio
    async def test_on_event(self):
        plugin = PioneClawPlugin()
        event = PluginEvent(type=EventType.TOOL_START, data={"tool": "search"})
        await plugin.on_event(event)  # 默认实现不报错

    def test_get_info(self):
        plugin = PioneClawPlugin()
        plugin.plugin_id = "test-plugin"
        plugin.plugin_name = "Test"
        info = plugin.get_info()
        assert info["plugin_id"] == "test-plugin"
        assert info["plugin_name"] == "Test"
        assert info["version"] == "1.0.0"

    def test_subclass_override(self):
        class MyPlugin(PioneClawPlugin):
            plugin_id = "my-plugin"
            plugin_name = "My Plugin"
            version = "2.0.0"

            async def on_load(self):
                self.loaded = True

        p = MyPlugin()
        assert p.plugin_id == "my-plugin"
        assert p.version == "2.0.0"


# ==================== plugin_metadata 装饰器 ====================


class TestPluginMetadata:
    def test_decorator_sets_metadata(self):
        @plugin_metadata(id="test", name="Test Plugin", version="1.2.3")
        class MyPlugin(PioneClawPlugin):
            pass

        assert MyPlugin.plugin_id == "test"
        assert MyPlugin.plugin_name == "Test Plugin"
        assert MyPlugin.version == "1.2.3"

    def test_decorator_extra_kwargs(self):
        @plugin_metadata(id="test", name="Test", author="Me")
        class MyPlugin(PioneClawPlugin):
            pass

        assert MyPlugin.author == "Me"

    def test_decorator_instance(self):
        @plugin_metadata(id="test", name="Test")
        class MyPlugin(PioneClawPlugin):
            pass

        p = MyPlugin()
        assert p.plugin_id == "test"
        assert p.plugin_name == "Test"


# ==================== EventType ====================


class TestEventType:
    def test_values(self):
        assert EventType.AGENT_START.value == "agent.start"
        assert EventType.TOOL_START.value == "tool.start"
        assert EventType.TOOL_BLOCKED.value == "tool.blocked"
        assert EventType.WORKFLOW_WAITING.value == "workflow.waiting"
        assert EventType.SKILL_LOADED.value == "skill.loaded"
        assert EventType.PLUGIN_LOADED.value == "plugin.loaded"
        assert EventType.SYSTEM_STARTUP.value == "system.startup"
        assert EventType.CUSTOM.value == "custom"

    def test_is_string_enum(self):
        assert isinstance(EventType.TOOL_START, str)


# ==================== PluginEvent ====================


class TestPluginEvent:
    def test_basic(self):
        event = PluginEvent(type=EventType.TOOL_START, data={"tool": "search"})
        assert event.type == EventType.TOOL_START
        assert event.data == {"tool": "search"}
        assert event.source == ""

    def test_with_source(self):
        event = PluginEvent(type="custom", data={}, source="my-plugin")
        assert event.source == "my-plugin"

    def test_to_dict(self):
        event = PluginEvent(
            type=EventType.AGENT_COMPLETE,
            data={"result": "ok"},
            source="agent-1",
            event_id="evt-123",
        )
        d = event.to_dict()
        assert d["type"] == "agent.complete"
        assert d["data"]["result"] == "ok"
        assert d["source"] == "agent-1"
        assert d["event_id"] == "evt-123"


# ==================== plugin_runtime ====================


class TestPluginRuntime:
    def setup_method(self):
        clear_runtime_context()

    def test_get_event_bus_none(self):
        assert get_event_bus() is None

    def test_set_and_get_event_bus(self):
        bus = object()
        set_runtime_context(event_bus=bus)
        assert get_event_bus() is bus

    def test_get_config_empty(self):
        assert get_config() == {}

    def test_get_config_with_key(self):
        set_runtime_context(config={"db_url": "sqlite:///test.db"})
        assert get_config("db_url") == "sqlite:///test.db"
        assert get_config("missing", "default") == "default"

    def test_get_db_session(self):
        assert get_db_session() is None

        def factory():
            return "session"

        set_runtime_context(db_session_factory=factory)
        assert get_db_session() is factory

    def test_clear_runtime_context(self):
        set_runtime_context(event_bus=object(), config={"k": "v"})
        clear_runtime_context()
        assert get_event_bus() is None
        assert get_config() == {}
