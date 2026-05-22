"""
Skills Loader - 技能加载管理

借鉴自 CountBot 的 skills.py，实现技能文件的加载、启用/禁用。

功能：
1. 加载技能文件（工作空间 + 内置 + 外部）
2. 解析 YAML frontmatter 元数据
3. 启用/禁用技能
4. 检查技能依赖
5. 构建技能摘要
"""

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _xml_escape(text: str) -> str:
    """XML 特殊字符转义"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _is_same_or_nested_path(path: Path, base: Path) -> bool:
    """判断 path 是否等于 base 或位于 base 之内"""
    try:
        normalized_path = os.path.normcase(str(path))
        normalized_base = os.path.normcase(str(base))
        return os.path.commonpath([normalized_path, normalized_base]) == normalized_base
    except ValueError:
        return False


@dataclass
class SkillMetadata:
    """技能元数据"""

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    always: bool = False
    dependencies: list[str] = field(default_factory=list)
    requires: dict[str, Any] = field(default_factory=dict)
    install: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Skill:
    """技能数据类"""

    name: str
    path: Path
    content: str
    enabled: bool = True
    source: str = "workspace"  # "workspace", "builtin", "openclaw"
    metadata: SkillMetadata = field(default_factory=SkillMetadata)
    auto_load: bool = False

    def __post_init__(self):
        """初始化后解析元数据"""
        if isinstance(self.metadata, dict):
            self.metadata = SkillMetadata(**self.metadata)
        self.auto_load = self.metadata.always

    def get_summary(self) -> str:
        """获取技能摘要"""
        title = self.metadata.title or self.name
        desc = self.metadata.description

        if desc:
            return f"- **{title}**: {desc}"
        return f"- **{title}**"

    def check_requirements(self) -> bool:
        """检查技能依赖是否满足"""
        requires = self.metadata.requires

        # 检查二进制依赖
        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                return False

        # 检查环境变量
        return all(os.environ.get(env_var) for env_var in requires.get("env", []))

    def check_install_status(self) -> dict:
        """检查技能安装状态（借鉴 OpenClaw SKILL.md install 规范）"""
        result = {
            "installed": True,
            "missing_bins": [],
            "missing_env": [],
            "steps": self.metadata.install,
        }
        requires = self.metadata.requires
        for bin_name in requires.get("bins", []):
            if not shutil.which(bin_name):
                result["missing_bins"].append(bin_name)
                result["installed"] = False
        for env_name in requires.get("env", []):
            if not os.environ.get(env_name):
                result["missing_env"].append(env_name)
                result["installed"] = False
        return result

    def get_missing_requirements(self) -> str:
        """获取缺失的依赖描述"""
        requires = self.metadata.requires
        missing = []

        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                missing.append(f"CLI: {binary}")

        for env_var in requires.get("env", []):
            if not os.environ.get(env_var):
                missing.append(f"ENV: {env_var}")

        return ", ".join(missing)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "path": str(self.path),
            "enabled": self.enabled,
            "source": self.source,
            "auto_load": self.auto_load,
            "metadata": {
                "title": self.metadata.title,
                "description": self.metadata.description,
                "tags": self.metadata.tags,
                "always": self.metadata.always,
                "dependencies": self.metadata.dependencies,
                "requires": self.metadata.requires,
                "install": self.metadata.install,
            },
        }


class SkillsLoader:
    """
    技能加载器

    管理技能文件的加载、启用/禁用
    """

    def __init__(
        self,
        skills_dir: Path,
        builtin_skills_dir: Path | None = None,
        external_skills_dirs: list[Path] | None = None,
    ):
        """
        初始化 SkillsLoader

        Args:
            skills_dir: 工作空间技能文件存储目录
            builtin_skills_dir: 内置技能目录
            external_skills_dirs: 外部技能目录
        """
        self.workspace_skills = Path(skills_dir)
        self.workspace_skills.mkdir(parents=True, exist_ok=True)

        self.builtin_skills = builtin_skills_dir
        self.external_skills_dirs = external_skills_dirs

        self.skills: dict[str, Skill] = {}

        # 加载禁用配置
        self.config_file = self.workspace_skills.parent / ".skills_config.json"
        self.disabled_skills = self._load_disabled_skills()

        self._load_all_skills()

        logger.info(f"Loaded {len(self.skills)} skills")

    def _load_disabled_skills(self) -> set[str]:
        """从配置文件加载禁用的技能列表"""
        if not self.config_file.exists():
            return set()

        try:
            config = json.loads(self.config_file.read_text(encoding="utf-8"))
            return set(config.get("disabled_skills", []))
        except Exception as e:
            logger.warning(f"Failed to load skills config: {e}")
            return set()

    def _save_disabled_skills(self) -> None:
        """保存禁用的技能列表到配置文件"""
        try:
            config = {"disabled_skills": list(self.disabled_skills)}
            self.config_file.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Failed to save skills config: {e}")

    def _discover_external_skill_dirs(self) -> list[Path]:
        """发现外部技能目录"""
        if self.external_skills_dirs is not None:
            return [Path(p) for p in self.external_skills_dirs]

        home = Path.home()
        candidates = [
            home / ".openclaw" / "skills",
            home / "skills",
        ]

        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.insert(0, Path(userprofile) / ".openclaw" / "skills")

        return candidates

    def _iter_skill_files(self, skills_root: Path):
        """遍历技能目录中的 SKILL.md 文件"""
        if not skills_root.exists() or not skills_root.is_dir():
            return

        try:
            skill_dirs = sorted(
                skills_root.iterdir(), key=lambda item: item.name.lower()
            )
        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to scan skills directory {skills_root}: {e}")
            return

        for skill_dir in skill_dirs:
            try:
                if not skill_dir.is_dir():
                    continue
            except (OSError, PermissionError):
                continue

            skill_file = skill_dir / "SKILL.md"
            try:
                if skill_file.is_file():
                    yield skill_dir.name, skill_file
            except (OSError, PermissionError):
                pass

    @staticmethod
    def _simple_yaml_parse(yaml_text: str) -> dict:
        """简易 YAML 解析（支持多行 > 和 |）"""
        result: dict = {}
        lines = yaml_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if ":" not in line:
                i += 1
                continue
            key, _, raw = line.partition(":")
            key = key.strip()
            value = raw.strip().strip("\"'")
            # YAML 多行块: > 或 |
            if value in (">", "|", ">-", "|-"):
                parts = []
                while i + 1 < len(lines) and (
                    lines[i + 1].startswith("  ") or lines[i + 1].startswith("\t")
                ):
                    i += 1
                    parts.append(lines[i].strip())
                value = " ".join(parts)
            result[key] = value
            i += 1
        return result

    def _parse_metadata(self, content: str) -> SkillMetadata:
        """解析技能文件的元数据（YAML frontmatter）"""
        metadata = SkillMetadata()

        # 解析 YAML frontmatter
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                yaml_content = match.group(1)
                data = self._simple_yaml_parse(yaml_content)

                if "title" in data:
                    metadata.title = data["title"]
                if "description" in data:
                    metadata.description = data["description"]
                if "always" in data:
                    metadata.always = data["always"].lower() in ("true", "yes", "1")
                if "tags" in data:
                    tags_str = data["tags"]
                    if tags_str.startswith("[") and tags_str.endswith("]"):
                        metadata.tags = [
                            t.strip().strip("\"'")
                            for t in tags_str[1:-1].split(",")
                            if t.strip()
                        ]
                if "install" in data:
                    try:
                        install_data = json.loads(data["install"])
                        if isinstance(install_data, list):
                            metadata.install = install_data
                    except (json.JSONDecodeError, TypeError):
                        pass
                if "metadata" in data:
                    try:
                        meta_data = json.loads(data["metadata"])
                        if isinstance(meta_data, dict):
                            skill_meta = meta_data.get("PioneClaw", {})
                            if "requires" in skill_meta:
                                metadata.requires = skill_meta["requires"]
                            if "always" in skill_meta:
                                metadata.always = skill_meta["always"]
                            if "install" in skill_meta:
                                metadata.install = skill_meta["install"]
                    except (json.JSONDecodeError, TypeError):
                        pass

        return metadata

    def _register_skill(self, name: str, skill_file: Path, source: str) -> None:
        """注册技能"""
        if name in self.skills:
            return

        try:
            enabled = (
                False if source == "openclaw" else name not in self.disabled_skills
            )
            content = skill_file.read_text(encoding="utf-8")
            metadata = self._parse_metadata(content)

            skill = Skill(
                name=name,
                path=skill_file,
                content=content,
                source=source,
                enabled=enabled,
                metadata=metadata,
            )

            self.skills[name] = skill
            logger.debug(f"Loaded {source} skill: {name}")
        except Exception as e:
            logger.warning(f"Failed to load {source} skill {skill_file.parent}: {e}")

    def _load_all_skills(self) -> None:
        """加载所有技能文件"""
        try:
            # 1. 加载工作空间技能（优先级最高）
            for name, skill_file in self._iter_skill_files(self.workspace_skills):
                self._register_skill(name, skill_file, "workspace")

            # 2. 加载内置技能
            if self.builtin_skills:
                for name, skill_file in self._iter_skill_files(self.builtin_skills):
                    self._register_skill(name, skill_file, "builtin")

            # 3. 加载外部技能
            for external_dir in self._discover_external_skill_dirs():
                for name, skill_file in self._iter_skill_files(external_dir):
                    self._register_skill(name, skill_file, "openclaw")

            logger.debug(f"Loaded {len(self.skills)} skills total")

        except Exception as e:
            logger.error(f"Failed to load skills: {e}")

    def list_skills(
        self,
        enabled_only: bool = False,
        filter_unavailable: bool = False,
    ) -> list[dict]:
        """
        列出所有技能

        Args:
            enabled_only: 是否只返回已启用的技能
            filter_unavailable: 是否过滤掉依赖未满足的技能

        Returns:
            list: 技能信息列表
        """
        skills = []

        for _name, skill in self.skills.items():
            if enabled_only and not skill.enabled:
                continue

            if filter_unavailable and not skill.check_requirements():
                continue

            skills.append(skill.to_dict())

        return skills

    def get_skill(self, name: str) -> Skill | None:
        """获取指定技能"""
        return self.skills.get(name)

    def read_skill(self, name: str) -> str:
        """读取技能内容"""
        skill = self.get_skill(name)
        if not skill:
            raise ValueError(f"Skill '{name}' not found")
        return skill.content

    def enable_skill(self, name: str) -> bool:
        """启用技能"""
        skill = self.get_skill(name)
        if not skill:
            logger.warning(f"Cannot enable skill '{name}': not found")
            return False

        self.disabled_skills.discard(name)
        skill.enabled = True
        self._save_disabled_skills()
        logger.info(f"Enabled skill: {name}")
        return True

    def disable_skill(self, name: str) -> bool:
        """禁用技能"""
        skill = self.get_skill(name)
        if not skill:
            logger.warning(f"Cannot disable skill '{name}': not found")
            return False

        self.disabled_skills.add(name)
        skill.enabled = False
        self._save_disabled_skills()
        logger.info(f"Disabled skill: {name}")
        return True

    def toggle_skill(self, name: str, enabled: bool) -> bool:
        """切换技能启用状态"""
        if enabled:
            return self.enable_skill(name)
        else:
            return self.disable_skill(name)

    def get_always_skills(self) -> list[str]:
        """获取标记为 always=true 且满足依赖的技能"""
        result = []
        for name, skill in self.skills.items():
            if skill.enabled and skill.metadata.always and skill.check_requirements():
                result.append(name)
        return result

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """加载特定技能用于包含在 agent 上下文中"""
        parts = []
        for name in skill_names:
            skill = self.get_skill(name)
            if skill:
                content = self._strip_frontmatter(skill.content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """构建所有已启用技能的摘要"""
        if not self.skills:
            return ""

        lines = []
        for name, skill in sorted(self.skills.items()):
            if not skill.enabled:
                continue

            if not skill.check_requirements():
                continue

            desc = skill.metadata.description
            title = skill.metadata.title or name
            desc = " ".join(str(desc or "").split())

            title_suffix = ""
            if title and title != name:
                title_suffix = f" | {title}"

            if desc:
                lines.append(f"- {name}{title_suffix}: {desc}")
            else:
                lines.append(f"- {name}{title_suffix}")

        return "\n".join(lines) if lines else ""

    def build_skills_xml(self) -> str:
        """生成 XML 格式的技能描述，LLM 解析更可靠

        借鉴 OpenClaw formatSkillsForPrompt
        """
        if not self.skills:
            return ""

        lines = ["<available_skills>"]
        for name, skill in sorted(self.skills.items()):
            if not skill.enabled:
                continue
            if not skill.check_requirements():
                continue

            lines.append(f'  <skill name="{_xml_escape(name)}">')
            desc = skill.metadata.description
            if desc:
                lines.append(f"    <description>{_xml_escape(desc)}</description>")
            if skill.metadata.always:
                lines.append("    <always>true</always>")
            if skill.metadata.tags:
                lines.append(
                    f"    <tags>{_xml_escape(', '.join(skill.metadata.tags))}</tags>"
                )
            location = str(skill.path.parent.name)
            if location:
                lines.append(f"    <location>{_xml_escape(location)}</location>")
            lines.append("  </skill>")

        lines.append("</available_skills>")
        return "\n".join(lines)

    def _strip_frontmatter(self, content: str) -> str:
        """从 markdown 内容中移除 YAML frontmatter"""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def add_skill(self, name: str, content: str) -> bool:
        """添加新技能"""
        try:
            skill_dir = self.workspace_skills / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file = skill_dir / "SKILL.md"

            if skill_file.exists():
                logger.warning(f"Skill '{name}' already exists")
                return False

            skill_file.write_text(content, encoding="utf-8")

            metadata = self._parse_metadata(content)
            skill = Skill(
                name=name,
                path=skill_file,
                content=content,
                enabled=True,
                source="workspace",
                metadata=metadata,
            )

            self.disabled_skills.discard(name)
            self.skills[name] = skill
            logger.info(f"Added new skill: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add skill '{name}': {e}")
            return False

    def update_skill(self, name: str, content: str) -> bool:
        """更新技能"""
        skill = self.get_skill(name)
        if not skill:
            logger.warning(f"Cannot update skill '{name}': not found")
            return False

        if skill.source != "workspace":
            logger.warning(f"Cannot update skill '{name}': not a workspace skill")
            return False

        try:
            skill.path.write_text(content, encoding="utf-8")
            skill.content = content
            skill.metadata = self._parse_metadata(content)
            skill.auto_load = skill.metadata.always

            logger.info(f"Updated skill: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to update skill '{name}': {e}")
            return False

    def delete_skill(self, name: str) -> bool:
        """删除技能"""
        skill = self.get_skill(name)
        if not skill:
            logger.warning(f"Cannot delete skill '{name}': not found")
            return False

        if skill.source != "workspace":
            logger.warning(f"Cannot delete skill '{name}': not a workspace skill")
            return False

        try:
            skill_dir = skill.path.parent
            if skill_dir.exists() and skill_dir.parent == self.workspace_skills:
                shutil.rmtree(skill_dir)

            self.disabled_skills.discard(name)
            del self.skills[name]
            logger.info(f"Deleted skill: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete skill '{name}': {e}")
            return False

    def reload(self) -> None:
        """重新加载所有技能"""
        logger.info("Reloading all skills")
        self.disabled_skills = self._load_disabled_skills()
        self.skills.clear()
        self._load_all_skills()

    def get_stats(self) -> dict[str, int]:
        """获取技能统计信息"""
        return {
            "total": len(self.skills),
            "enabled": len([s for s in self.skills.values() if s.enabled]),
            "disabled": len([s for s in self.skills.values() if not s.enabled]),
            "auto_load": len([s for s in self.skills.values() if s.auto_load]),
        }


# ==================== 全局实例 ====================

_global_skills_loader: SkillsLoader | None = None


def _default_builtin_skills_dir() -> Path:
    """获取默认的内置技能目录"""
    return Path(__file__).resolve().parent / "skills" / "builtin"


def get_skills_loader(
    skills_dir: Path | None = None,
    builtin_skills_dir: Path | None = None,
) -> SkillsLoader:
    """获取全局技能加载器实例"""
    global _global_skills_loader

    if _global_skills_loader is None:
        if skills_dir is None:
            # 项目根目录的 skills/，而非 cwd 下的 skills/
            project_root = Path(__file__).resolve().parents[4]
            skills_dir = project_root / "skills"
        if builtin_skills_dir is None:
            builtin_skills_dir = _default_builtin_skills_dir()
        _global_skills_loader = SkillsLoader(
            skills_dir, builtin_skills_dir=builtin_skills_dir
        )

    return _global_skills_loader


def init_skills_loader(
    skills_dir: Path,
    builtin_skills_dir: Path | None = None,
) -> SkillsLoader:
    """初始化全局技能加载器"""
    global _global_skills_loader
    if builtin_skills_dir is None:
        builtin_skills_dir = _default_builtin_skills_dir()
    _global_skills_loader = SkillsLoader(
        skills_dir, builtin_skills_dir=builtin_skills_dir
    )
    return _global_skills_loader
