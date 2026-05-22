"""
技能系统增强测试
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.modules.agent.skills import Skill, SkillMetadata, SkillsLoader


@pytest.fixture
def temp_skills_dir():
    """创建临时技能目录"""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def builtin_skills_dir():
    """创建内置技能目录"""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


def _create_skill_file(skill_dir: Path, name: str, content: str):
    """创建技能文件"""
    skill_path = skill_dir / name
    skill_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text(content, encoding="utf-8")


class TestSkillMetadata:
    """测试技能元数据"""

    def test_default_metadata(self):
        """测试默认元数据"""
        meta = SkillMetadata()
        assert meta.title == ""
        assert meta.description == ""
        assert meta.always is False
        assert meta.tags == []
        assert meta.dependencies == []
        assert meta.requires == {}

    def test_custom_metadata(self):
        """测试自定义元数据"""
        meta = SkillMetadata(
            title="Test Skill",
            description="A test skill",
            always=True,
            tags=["tool", "auto"],
            dependencies=["other_skill"],
            requires={"bins": ["python"], "env": ["API_KEY"]},
        )
        assert meta.title == "Test Skill"
        assert meta.always is True
        assert "tool" in meta.tags


class TestSkill:
    """测试技能数据类"""

    def test_skill_creation(self, temp_skills_dir):
        """测试技能创建"""
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test content",
        )
        assert skill.name == "test"
        assert skill.content == "test content"
        assert skill.enabled is True
        assert skill.source == "workspace"
        assert skill.auto_load is False

    def test_skill_auto_load_from_always(self, temp_skills_dir):
        """测试 always=true 时自动加载"""
        skill = Skill(
            name="auto_skill",
            path=temp_skills_dir / "auto" / "SKILL.md",
            content="auto content",
            metadata=SkillMetadata(always=True),
        )
        assert skill.auto_load is True

    def test_skill_get_summary(self, temp_skills_dir):
        """测试技能摘要"""
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(title="Test Skill", description="A description"),
        )
        summary = skill.get_summary()
        assert "Test Skill" in summary
        assert "A description" in summary

    def test_skill_check_requirements_bins(self, temp_skills_dir):
        """测试二进制依赖检查"""
        # python 通常存在
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(requires={"bins": ["python"]}),
        )
        assert skill.check_requirements() is True

        # 不存在的二进制
        skill2 = Skill(
            name="test2",
            path=temp_skills_dir / "test2" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(requires={"bins": ["nonexistent_binary_xyz"]}),
        )
        assert skill2.check_requirements() is False

    def test_skill_check_requirements_env(self, temp_skills_dir):
        """测试环境变量依赖检查"""
        # 不存在的环境变量
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(requires={"env": ["NONEXISTENT_ENV_VAR_XYZ"]}),
        )
        assert skill.check_requirements() is False

    def test_skill_get_missing_requirements(self, temp_skills_dir):
        """测试获取缺失依赖描述"""
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(
                requires={
                    "bins": ["nonexistent_binary_xyz"],
                    "env": ["NONEXISTENT_ENV_VAR_XYZ"],
                }
            ),
        )
        missing = skill.get_missing_requirements()
        assert "nonexistent_binary_xyz" in missing
        assert "NONEXISTENT_ENV_VAR_XYZ" in missing

    def test_skill_to_dict(self, temp_skills_dir):
        """测试转换为字典"""
        skill = Skill(
            name="test",
            path=temp_skills_dir / "test" / "SKILL.md",
            content="test",
            metadata=SkillMetadata(title="Test", always=True),
        )
        d = skill.to_dict()
        assert d["name"] == "test"
        assert d["auto_load"] is True
        assert d["metadata"]["always"] is True


class TestSkillsLoader:
    """测试技能加载器"""

    def test_load_empty_dir(self, temp_skills_dir):
        """测试加载空目录"""
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        assert len(loader.skills) == 0

    def test_load_workspace_skill(self, temp_skills_dir):
        """测试加载工作空间技能"""
        _create_skill_file(
            temp_skills_dir,
            "my-skill",
            "---\ntitle: My Skill\ndescription: Test\n---\n\nContent here",
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        assert "my-skill" in loader.skills
        assert loader.skills["my-skill"].metadata.title == "My Skill"
        assert loader.skills["my-skill"].source == "workspace"

    def test_load_builtin_skill(self, temp_skills_dir, builtin_skills_dir):
        """测试加载内置技能"""
        _create_skill_file(
            builtin_skills_dir,
            "builtin-skill",
            "---\ntitle: Built-in\n---\n\nBuilt-in content",
        )
        loader = SkillsLoader(temp_skills_dir, builtin_skills_dir=builtin_skills_dir)
        assert "builtin-skill" in loader.skills
        assert loader.skills["builtin-skill"].source == "builtin"

    def test_workspace_priority(self, temp_skills_dir, builtin_skills_dir):
        """测试工作空间优先级高于内置"""
        _create_skill_file(
            temp_skills_dir, "same-name", "---\ntitle: Workspace Version\n---\n\nWS"
        )
        _create_skill_file(
            builtin_skills_dir,
            "same-name",
            "---\ntitle: Builtin Version\n---\n\nBuilt-in",
        )
        loader = SkillsLoader(temp_skills_dir, builtin_skills_dir=builtin_skills_dir)
        assert loader.skills["same-name"].metadata.title == "Workspace Version"

    def test_parse_frontmatter_always(self, temp_skills_dir):
        """测试解析 always 字段"""
        _create_skill_file(
            temp_skills_dir,
            "auto-skill",
            "---\ntitle: Auto\ndescription: Auto load\nalways: true\n---\n\nContent",
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        skill = loader.get_skill("auto-skill")
        assert skill is not None
        assert skill.metadata.always is True
        assert skill.auto_load is True

    def test_parse_frontmatter_tags(self, temp_skills_dir):
        """测试解析 tags 字段"""
        _create_skill_file(
            temp_skills_dir,
            "tagged-skill",
            "---\ntitle: Tagged\ntags: [tool, auto]\n---\n\nContent",
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        skill = loader.get_skill("tagged-skill")
        assert skill is not None
        assert "tool" in skill.metadata.tags
        assert "auto" in skill.metadata.tags

    def test_parse_frontmatter_requires(self, temp_skills_dir):
        """测试解析 requires 字段"""
        content = '---\ntitle: Dep Skill\nmetadata: {"PioneClaw": {"requires": {"bins": ["git"], "env": ["GITHUB_TOKEN"]}}}\n---\n\nContent'
        _create_skill_file(temp_skills_dir, "dep-skill", content)
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        skill = loader.get_skill("dep-skill")
        assert skill is not None
        assert "git" in skill.metadata.requires.get("bins", [])
        assert "GITHUB_TOKEN" in skill.metadata.requires.get("env", [])

    def test_add_skill(self, temp_skills_dir):
        """测试添加技能"""
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        result = loader.add_skill("new-skill", "---\ntitle: New\n---\n\nNew content")
        assert result is True
        assert "new-skill" in loader.skills
        assert loader.skills["new-skill"].metadata.title == "New"

    def test_add_duplicate_skill(self, temp_skills_dir):
        """测试添加重复技能"""
        _create_skill_file(
            temp_skills_dir, "existing", "---\ntitle: Existing\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        result = loader.add_skill("existing", "New content")
        assert result is False

    def test_update_skill(self, temp_skills_dir):
        """测试更新技能"""
        _create_skill_file(
            temp_skills_dir, "my-skill", "---\ntitle: Old\n---\n\nOld content"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        result = loader.update_skill("my-skill", "---\ntitle: New\n---\n\nNew content")
        assert result is True
        assert loader.skills["my-skill"].metadata.title == "New"

    def test_delete_skill(self, temp_skills_dir):
        """测试删除技能"""
        _create_skill_file(
            temp_skills_dir, "to-delete", "---\ntitle: Delete Me\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        result = loader.delete_skill("to-delete")
        assert result is True
        assert "to-delete" not in loader.skills

    def test_enable_disable_skill(self, temp_skills_dir):
        """测试启用/禁用技能"""
        _create_skill_file(
            temp_skills_dir, "toggle-skill", "---\ntitle: Toggle\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        # 禁用
        result = loader.disable_skill("toggle-skill")
        assert result is True
        assert loader.skills["toggle-skill"].enabled is False

        # 启用
        result = loader.enable_skill("toggle-skill")
        assert result is True
        assert loader.skills["toggle-skill"].enabled is True

    def test_toggle_skill(self, temp_skills_dir):
        """测试切换技能状态"""
        _create_skill_file(
            temp_skills_dir, "toggle-skill", "---\ntitle: Toggle\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        result = loader.toggle_skill("toggle-skill", False)
        assert result is True
        assert loader.skills["toggle-skill"].enabled is False

    def test_get_always_skills(self, temp_skills_dir):
        """测试获取 always 技能列表"""
        _create_skill_file(
            temp_skills_dir,
            "auto-skill",
            "---\ntitle: Auto\nalways: true\n---\n\nContent",
        )
        _create_skill_file(
            temp_skills_dir, "normal-skill", "---\ntitle: Normal\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        always_skills = loader.get_always_skills()
        assert "auto-skill" in always_skills
        assert "normal-skill" not in always_skills

    def test_load_skills_for_context(self, temp_skills_dir):
        """测试加载技能上下文"""
        _create_skill_file(
            temp_skills_dir,
            "ctx-skill",
            "---\ntitle: Context\n---\n\nContext content here",
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        context = loader.load_skills_for_context(["ctx-skill"])
        assert "Context content here" in context
        assert "---" not in context  # frontmatter 应被移除

    def test_build_skills_summary(self, temp_skills_dir):
        """测试构建技能摘要"""
        _create_skill_file(
            temp_skills_dir,
            "skill-a",
            "---\ntitle: Skill A\ndescription: First skill\n---\n\nContent A",
        )
        _create_skill_file(
            temp_skills_dir,
            "skill-b",
            "---\ntitle: Skill B\ndescription: Second skill\n---\n\nContent B",
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        summary = loader.build_skills_summary()
        assert "Skill A" in summary
        assert "First skill" in summary
        assert "Skill B" in summary

    def test_reload(self, temp_skills_dir):
        """测试热重载"""
        _create_skill_file(
            temp_skills_dir, "existing", "---\ntitle: Existing\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        assert "existing" in loader.skills

        # 添加新技能文件
        _create_skill_file(
            temp_skills_dir, "new-after-init", "---\ntitle: New\n---\n\nNew content"
        )

        # 重载前不应该有新技能
        assert "new-after-init" not in loader.skills

        # 重载
        loader.reload()
        assert "new-after-init" in loader.skills
        assert "existing" in loader.skills

    def test_get_stats(self, temp_skills_dir):
        """测试获取统计"""
        _create_skill_file(
            temp_skills_dir, "skill-a", "---\ntitle: A\nalways: true\n---\n\nContent"
        )
        _create_skill_file(temp_skills_dir, "skill-b", "---\ntitle: B\n---\n\nContent")
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        stats = loader.get_stats()
        assert stats["total"] == 2
        assert stats["enabled"] == 2
        assert stats["auto_load"] == 1

    def test_list_skills_enabled_only(self, temp_skills_dir):
        """测试只列出已启用的技能"""
        _create_skill_file(
            temp_skills_dir, "enabled-skill", "---\ntitle: Enabled\n---\n\nContent"
        )
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        loader.disable_skill("enabled-skill")

        skills = loader.list_skills(enabled_only=True)
        assert len(skills) == 0

    def test_strip_frontmatter(self, temp_skills_dir):
        """测试移除 frontmatter"""
        loader = SkillsLoader(temp_skills_dir, external_skills_dirs=[])
        content = "---\ntitle: Test\n---\n\nActual content"
        stripped = loader._strip_frontmatter(content)
        assert stripped == "Actual content"
        assert "---" not in stripped

        content_no_fm = "Just content"
        assert loader._strip_frontmatter(content_no_fm) == "Just content"
