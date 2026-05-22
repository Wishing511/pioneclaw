"""
UU.2 内置技能测试

覆盖：6 个 builtin 技能的加载、frontmatter 解析、activate 和 list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.modules.agent.skills import SkillsLoader

# ── 辅助：获取 builtin 目录路径 ────────────────────────────────


def _get_builtin_dir() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "app"
        / "modules"
        / "agent"
        / "skills"
        / "builtin"
    )


# ── 辅助：创建临时的 SkillsLoader 实例 ─────────────────────────


def _create_loader_with_builtin() -> SkillsLoader:
    import tempfile

    from app.modules.agent.skills import SkillsLoader

    tmpdir = Path(tempfile.mkdtemp())
    builtin_dir = _get_builtin_dir()
    return SkillsLoader(skills_dir=tmpdir, builtin_skills_dir=builtin_dir)


# ============================================================
# 测试 1: 所有 6 个技能能被加载
# ============================================================


class TestBuiltinSkillsLoad:
    """测试 SkillsLoader 能发现并加载 6 个 builtin 技能"""

    EXPECTED_SKILLS = {
        "commit",
        "simplify",
        "verify",
        "remember",
        "update-config",
        "loop",
    }

    def test_all_six_skills_discovered(self):
        loader = _create_loader_with_builtin()
        names = set(loader.skills.keys())
        assert self.EXPECTED_SKILLS.issubset(names), (
            f"Missing: {self.EXPECTED_SKILLS - names}"
        )

    def test_each_skill_is_enabled(self):
        loader = _create_loader_with_builtin()
        for name in self.EXPECTED_SKILLS:
            skill = loader.get_skill(name)
            assert skill is not None, f"Skill '{name}' not found"
            assert skill.enabled is True, f"Skill '{name}' not enabled"

    def test_each_skill_source_is_builtin(self):
        loader = _create_loader_with_builtin()
        for name in self.EXPECTED_SKILLS:
            skill = loader.get_skill(name)
            assert skill.source == "builtin", f"Skill '{name}' source={skill.source}"


# ============================================================
# 测试 2: Frontmatter 解析正确
# ============================================================


class TestBuiltinSkillFrontmatter:
    """测试每个技能的 title/description/tags 正确解析"""

    def test_commit_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("commit")
        assert skill.metadata.title == "Git 提交"
        assert "commit" in skill.metadata.description.lower()
        assert "git" in skill.metadata.tags

    def test_simplify_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("simplify")
        assert (
            "审查" in skill.metadata.title or "review" in skill.metadata.title.lower()
        )
        assert "review" in skill.metadata.tags or "code-quality" in skill.metadata.tags

    def test_verify_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("verify")
        assert "验证" in skill.metadata.title or "test" in skill.metadata.title.lower()
        assert len(skill.metadata.tags) > 0

    def test_remember_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("remember")
        assert (
            "记忆" in skill.metadata.title or "memory" in skill.metadata.title.lower()
        )
        assert len(skill.metadata.tags) > 0

    def test_update_config_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("update-config")
        assert (
            "配置" in skill.metadata.title or "config" in skill.metadata.title.lower()
        )
        assert len(skill.metadata.tags) > 0

    def test_loop_metadata(self):
        loader = _create_loader_with_builtin()
        skill = loader.get_skill("loop")
        assert (
            "定时" in skill.metadata.title
            or "循环" in skill.metadata.title
            or "loop" in skill.metadata.title.lower()
        )
        assert len(skill.metadata.tags) > 0

    def test_no_skill_has_always_true(self):
        """内置技能不应默认自动加载"""
        loader = _create_loader_with_builtin()
        for name in TestBuiltinSkillsLoad.EXPECTED_SKILLS:
            skill = loader.get_skill(name)
            assert skill.metadata.always is False, f"Skill '{name}' has always=true"


# ============================================================
# 测试 3: 技能内容不为空
# ============================================================


class TestBuiltinSkillContent:
    """测试每个技能有实质性内容（去除 frontmatter 后）"""

    def test_each_skill_has_body_content(self):
        loader = _create_loader_with_builtin()
        for name in TestBuiltinSkillsLoad.EXPECTED_SKILLS:
            skill = loader.get_skill(name)
            body = loader._strip_frontmatter(skill.content)
            assert len(body) > 50, f"Skill '{name}' body too short: {len(body)} chars"


# ============================================================
# 测试 4: SkillTool activate 返回非空内容
# ============================================================


class TestBuiltinSkillActivate:
    """测试 SkillTool activate 对每个 builtin 技能返回正确内容"""

    @pytest.mark.asyncio
    async def test_activate_commit(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="commit")
        data = json.loads(result)
        assert data["success"] is True
        assert data["name"] == "commit"
        assert "Co-Authored-By" in data["content"]

    @pytest.mark.asyncio
    async def test_activate_simplify(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="simplify")
        data = json.loads(result)
        assert data["success"] is True
        assert "审查" in data["content"] or "DRY" in data["content"]

    @pytest.mark.asyncio
    async def test_activate_verify(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="verify")
        data = json.loads(result)
        assert data["success"] is True
        assert "pytest" in data["content"].lower() or "测试" in data["content"]

    @pytest.mark.asyncio
    async def test_activate_remember(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="remember")
        data = json.loads(result)
        assert data["success"] is True
        assert "memory" in data["content"].lower() or "记忆" in data["content"]

    @pytest.mark.asyncio
    async def test_activate_update_config(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="update-config")
        data = json.loads(result)
        assert data["success"] is True
        assert "config" in data["content"].lower() or "配置" in data["content"]

    @pytest.mark.asyncio
    async def test_activate_loop(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="activate", skill_name="loop")
        data = json.loads(result)
        assert data["success"] is True
        assert "cron" in data["content"].lower() or "定时" in data["content"]


# ============================================================
# 测试 5: SkillTool list 包含 builtin 技能
# ============================================================


class TestBuiltinSkillList:
    """测试 SkillTool list 返回包含 builtin 技能"""

    @pytest.mark.asyncio
    async def test_list_includes_builtin(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="list")
        data = json.loads(result)
        assert data["success"] is True
        names = [s["name"] for s in data["skills"]]
        for expected in TestBuiltinSkillsLoad.EXPECTED_SKILLS:
            assert expected in names, f"'{expected}' missing from list"

    @pytest.mark.asyncio
    async def test_list_shows_builtin_source(self, monkeypatch):
        import app.modules.tools.skill as skill_module
        from app.modules.tools.skill import SkillTool

        loader = _create_loader_with_builtin()
        skill_module.get_skills_loader = lambda: loader

        tool = SkillTool()
        result = await tool.execute(action="list")
        data = json.loads(result)
        builtin_skills = [s for s in data["skills"] if s["source"] == "builtin"]
        assert len(builtin_skills) >= 6


# ============================================================
# 测试 6: 内置技能目录存在且包含 6 个子目录
# ============================================================


class TestBuiltinDirectoryStructure:
    """测试 builtin 目录结构正确"""

    def test_builtin_dir_exists(self):
        builtin_dir = _get_builtin_dir()
        assert builtin_dir.is_dir(), f"Directory not found: {builtin_dir}"

    def test_six_skill_subdirs(self):
        builtin_dir = _get_builtin_dir()
        subdirs = [d for d in builtin_dir.iterdir() if d.is_dir()]
        names = {d.name for d in subdirs}
        assert TestBuiltinSkillsLoad.EXPECTED_SKILLS.issubset(names)

    def test_each_has_skill_md(self):
        builtin_dir = _get_builtin_dir()
        for name in TestBuiltinSkillsLoad.EXPECTED_SKILLS:
            skill_file = builtin_dir / name / "SKILL.md"
            assert skill_file.is_file(), f"Missing: {skill_file}"
