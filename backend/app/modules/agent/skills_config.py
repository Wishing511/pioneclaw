"""
Skills Config Manager - 技能配置管理器

借鉴自 CountBot 的 skills_config.py，实现技能配置的读写和管理。

功能：
1. 读写技能的 config.json 配置文件
2. 配置状态检查
3. 自动修复配置（添加缺失字段）
4. 配置帮助文档加载
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.modules.agent.skills_schema import (
    SkillsSchemaRegistry,
    get_schema_registry,
)

logger = logging.getLogger(__name__)


@dataclass
class ConfigStatus:
    """配置状态"""

    skill_name: str
    status: str  # no_schema, not_configured, invalid_format, missing_fields, valid
    has_schema: bool = False
    has_config: bool = False
    missing_fields: list[str] = None
    errors: list[str] = None

    def __post_init__(self):
        if self.missing_fields is None:
            self.missing_fields = []
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "status": self.status,
            "has_schema": self.has_schema,
            "has_config": self.has_config,
            "missing_fields": self.missing_fields,
            "errors": self.errors,
        }


class SkillsConfigManager:
    """
    技能配置管理器

    管理技能配置文件的读写、验证和修复
    """

    def __init__(
        self,
        skills_dir: Path,
        schema_registry: SkillsSchemaRegistry | None = None,
    ):
        """
        初始化配置管理器

        Args:
            skills_dir: 技能目录路径
            schema_registry: Schema 注册表（可选）
        """
        self.skills_dir = skills_dir
        self.schema_registry = schema_registry or get_schema_registry(skills_dir)

    def get_config_path(self, skill_name: str) -> Path:
        """获取技能配置文件路径"""
        return self.skills_dir / skill_name / "config.json"

    def has_config(self, skill_name: str) -> bool:
        """检查技能是否有配置文件"""
        return self.get_config_path(skill_name).exists()

    def load_config(self, skill_name: str) -> dict | None:
        """
        加载技能配置

        Args:
            skill_name: 技能名称

        Returns:
            dict: 配置内容，如果不存在或格式错误则返回 None
        """
        config_path = self.get_config_path(skill_name)

        if not config_path.exists():
            logger.debug(f"Config file not found for skill: {skill_name}")
            return None

        try:
            content = config_path.read_text(encoding="utf-8")
            config = json.loads(content)
            logger.debug(f"Loaded config for skill: {skill_name}")
            return config
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file for {skill_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load config for {skill_name}: {e}")
            return None

    def save_config(self, skill_name: str, config: dict) -> bool:
        """
        保存技能配置

        Args:
            skill_name: 技能名称
            config: 配置内容

        Returns:
            bool: 是否成功
        """
        config_path = self.get_config_path(skill_name)

        try:
            # 确保目录存在
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # 添加元数据
            config_with_meta = {
                **config,
                "_updated_at": datetime.now().isoformat(),
            }

            # 保存配置
            config_path.write_text(
                json.dumps(config_with_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.info(f"Saved config for skill: {skill_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to save config for {skill_name}: {e}")
            return False

    def delete_config(self, skill_name: str) -> bool:
        """删除技能配置"""
        config_path = self.get_config_path(skill_name)

        if not config_path.exists():
            return False

        try:
            config_path.unlink()
            logger.info(f"Deleted config for skill: {skill_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete config for {skill_name}: {e}")
            return False

    def get_config_status(self, skill_name: str) -> ConfigStatus:
        """
        检查配置状态

        Args:
            skill_name: 技能名称

        Returns:
            ConfigStatus: 配置状态
        """
        # 1. 检查是否有 Schema
        has_schema = self.schema_registry.has_schema(skill_name)

        if not has_schema:
            return ConfigStatus(
                skill_name=skill_name,
                status="no_schema",
                has_schema=False,
                has_config=self.has_config(skill_name),
            )

        # 2. 检查配置文件是否存在
        has_config = self.has_config(skill_name)

        if not has_config:
            return ConfigStatus(
                skill_name=skill_name,
                status="not_configured",
                has_schema=True,
                has_config=False,
            )

        # 3. 检查 JSON 格式
        config = self.load_config(skill_name)

        if config is None:
            return ConfigStatus(
                skill_name=skill_name,
                status="invalid_format",
                has_schema=True,
                has_config=True,
                errors=["配置文件格式错误，不是有效的 JSON"],
            )

        # 4. 验证配置
        is_valid, errors = self.schema_registry.validate_config(skill_name, config)

        if not is_valid:
            return ConfigStatus(
                skill_name=skill_name,
                status="missing_fields",
                has_schema=True,
                has_config=True,
                missing_fields=errors,
            )

        return ConfigStatus(
            skill_name=skill_name,
            status="valid",
            has_schema=True,
            has_config=True,
        )

    def auto_fix_config(self, skill_name: str) -> tuple[bool, list[str]]:
        """
        自动修复配置文件
        - 添加缺失的字段（使用默认值）
        - 保留现有的有效字段

        Args:
            skill_name: 技能名称

        Returns:
            tuple: (success, changes)
        """
        schema = self.schema_registry.get_schema(skill_name)
        if not schema:
            return False, ["技能没有定义 Schema"]

        # 加载现有配置
        config = self.load_config(skill_name)
        if config is None:
            # 创建新配置
            config = {}

        # 添加缺失字段
        changes = []
        self._add_missing_fields(config, schema.fields, changes)

        if not changes:
            return True, ["配置已经完整，无需修复"]

        # 保存修复后的配置
        success = self.save_config(skill_name, config)
        return success, changes

    def _add_missing_fields(
        self,
        config: dict,
        fields: list,
        changes: list[str],
        prefix: str = "",
    ) -> None:
        """递归添加缺失的字段"""
        for field_def in fields:
            key = field_def.key
            full_key = f"{prefix}{key}" if prefix else key

            if key not in config:
                # 字段缺失
                if field_def.type == "object":
                    # 创建空对象
                    obj = {}
                    if field_def.fields:
                        self._add_missing_fields(
                            obj, field_def.fields, changes, f"{full_key}."
                        )
                    config[key] = obj
                    changes.append(f"添加了字段 '{full_key}'（对象）")
                elif field_def.default is not None:
                    config[key] = field_def.default
                    changes.append(f"添加了字段 '{full_key}' = {field_def.default}")
                elif field_def.required:
                    # 必填字段没有默认值
                    config[key] = self._get_empty_value(field_def.type)
                    changes.append(f"添加了必填字段 '{full_key}'（空值）")
            elif field_def.type == "object" and isinstance(config[key], dict):
                # 递归处理嵌套对象
                self._add_missing_fields(
                    config[key], field_def.fields, changes, f"{full_key}."
                )

    def _get_empty_value(self, field_type: str) -> Any:
        """获取字段类型的空值"""
        if field_type == "number":
            return 0
        elif field_type == "boolean":
            return False
        elif field_type == "object":
            return {}
        else:
            return ""

    def get_field_value(self, skill_name: str, field_key: str) -> Any | None:
        """
        获取配置中的单个字段值

        Args:
            skill_name: 技能名称
            field_key: 字段键（支持点号分隔的嵌套字段，如 "api.key"）

        Returns:
            Any: 字段值，如果不存在则返回 None
        """
        config = self.load_config(skill_name)
        if config is None:
            return None

        # 支持嵌套字段
        keys = field_key.split(".")
        value = config

        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return None
            else:
                return None

        return value

    def set_field_value(
        self,
        skill_name: str,
        field_key: str,
        value: Any,
    ) -> bool:
        """
        设置配置中的单个字段值

        Args:
            skill_name: 技能名称
            field_key: 字段键（支持点号分隔的嵌套字段）
            value: 字段值

        Returns:
            bool: 是否成功
        """
        config = self.load_config(skill_name) or {}

        # 支持嵌套字段
        keys = field_key.split(".")
        current = config

        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

        return self.save_config(skill_name, config)

    def get_help_content(self, skill_name: str) -> str | None:
        """
        获取配置帮助文档内容

        Args:
            skill_name: 技能名称

        Returns:
            str: 帮助文档内容，如果不存在则返回 None
        """
        help_path = self.skills_dir / skill_name / "config.help.md"

        if not help_path.exists():
            return None

        try:
            return help_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to load help content for {skill_name}: {e}")
            return None

    def list_configs(self) -> dict[str, dict]:
        """
        列出所有技能的配置状态

        Returns:
            dict: {skill_name: config_status}
        """
        result = {}

        if not self.skills_dir.exists():
            return result

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            status = self.get_config_status(skill_name)
            result[skill_name] = status.to_dict()

        return result


# ==================== 全局实例 ====================

_global_config_manager: SkillsConfigManager | None = None


def get_config_manager(skills_dir: Path | None = None) -> SkillsConfigManager:
    """获取全局配置管理器实例"""
    global _global_config_manager

    if _global_config_manager is None:
        if skills_dir is None:
            skills_dir = Path.cwd() / "skills"
        _global_config_manager = SkillsConfigManager(skills_dir)

    return _global_config_manager


def init_config_manager(skills_dir: Path) -> SkillsConfigManager:
    """初始化全局配置管理器"""
    global _global_config_manager
    _global_config_manager = SkillsConfigManager(skills_dir)
    return _global_config_manager
