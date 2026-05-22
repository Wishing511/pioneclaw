"""
Skills Schema - 技能配置 Schema 定义

借鉴自 CountBot 的 skills_schema.py，实现技能配置的字段定义和验证。

功能：
1. 定义技能配置字段（string, password, number, boolean, select, object）
2. 验证配置是否符合 Schema
3. 生成默认配置
4. 支持嵌套对象和可折叠分组
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SchemaField:
    """Schema 字段定义"""

    key: str
    type: str  # string, password, number, boolean, select, object, email
    label: str
    description: str = ""
    required: bool = False
    sensitive: bool = False  # 敏感字段（如 API Key）
    default: Any = None
    placeholder: str = ""
    help_text: str = ""
    help_url: str = ""

    # 数字类型
    min: float | None = None
    max: float | None = None

    # 选择类型
    options: list[dict[str, str]] = field(
        default_factory=list
    )  # [{"value": "x", "label": "Y"}]

    # 对象类型
    collapsible: bool = False
    fields: list["SchemaField"] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "required": self.required,
            "sensitive": self.sensitive,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "help_url": self.help_url,
        }

        if self.default is not None:
            result["default"] = self.default

        if self.type == "number":
            if self.min is not None:
                result["min"] = self.min
            if self.max is not None:
                result["max"] = self.max

        if self.type == "select":
            result["options"] = self.options

        if self.type == "object":
            result["collapsible"] = self.collapsible
            result["fields"] = [f.to_dict() for f in self.fields]

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SchemaField":
        """从字典创建"""
        field_obj = cls(
            key=data["key"],
            type=data["type"],
            label=data["label"],
            description=data.get("description", ""),
            required=data.get("required", False),
            sensitive=data.get("sensitive", False),
            default=data.get("default"),
            placeholder=data.get("placeholder", ""),
            help_text=data.get("help_text", ""),
            help_url=data.get("help_url", ""),
        )

        if field_obj.type == "number":
            field_obj.min = data.get("min")
            field_obj.max = data.get("max")

        if field_obj.type == "select":
            field_obj.options = data.get("options", [])

        if field_obj.type == "object":
            field_obj.collapsible = data.get("collapsible", False)
            field_obj.fields = [cls.from_dict(f) for f in data.get("fields", [])]

        return field_obj


@dataclass
class SkillSchema:
    """技能 Schema 定义"""

    skill_name: str
    version: str = "1.0.0"
    description: str = ""
    help_text: str = ""
    fields: list[SchemaField] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "skill_name": self.skill_name,
            "version": self.version,
            "description": self.description,
            "help_text": self.help_text,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillSchema":
        """从字典创建"""
        return cls(
            skill_name=data["skill_name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            help_text=data.get("help_text", ""),
            fields=[SchemaField.from_dict(f) for f in data.get("fields", [])],
        )


class SkillsSchemaRegistry:
    """
    技能 Schema 注册表

    管理 Schema 的加载、验证和存储
    """

    def __init__(self, skills_dir: Path):
        """
        初始化 Schema 注册表

        Args:
            skills_dir: 技能目录路径
        """
        self.skills_dir = skills_dir
        self.schemas: dict[str, SkillSchema] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        """从技能目录加载所有 Schema"""
        if not self.skills_dir.exists():
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            schema_file = skill_dir / "schema.json"
            if schema_file.exists():
                try:
                    data = self._load_json(schema_file)
                    schema = SkillSchema.from_dict(data)
                    self.schemas[schema.skill_name] = schema
                    logger.debug(f"Loaded schema for skill: {schema.skill_name}")
                except Exception as e:
                    logger.warning(f"Failed to load schema from {schema_file}: {e}")

    def _load_json(self, path: Path) -> dict:
        """加载 JSON 文件"""
        import json

        return json.loads(path.read_text(encoding="utf-8"))

    def _save_json(self, path: Path, data: dict) -> None:
        """保存 JSON 文件"""
        import json

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_schema(self, skill_name: str) -> SkillSchema | None:
        """获取技能 Schema"""
        return self.schemas.get(skill_name)

    def has_schema(self, skill_name: str) -> bool:
        """检查技能是否有 Schema"""
        return skill_name in self.schemas

    def list_schemas(self) -> list[str]:
        """列出所有有 Schema 的技能"""
        return list(self.schemas.keys())

    def register_schema(self, schema: SkillSchema) -> None:
        """注册 Schema"""
        self.schemas[schema.skill_name] = schema

        # 保存到文件
        schema_file = self.skills_dir / schema.skill_name / "schema.json"
        schema_file.parent.mkdir(parents=True, exist_ok=True)
        self._save_json(schema_file, schema.to_dict())
        logger.info(f"Registered schema for skill: {schema.skill_name}")

    def delete_schema(self, skill_name: str) -> bool:
        """删除 Schema"""
        if skill_name not in self.schemas:
            return False

        del self.schemas[skill_name]

        # 删除文件
        schema_file = self.skills_dir / skill_name / "schema.json"
        if schema_file.exists():
            schema_file.unlink()

        logger.info(f"Deleted schema for skill: {skill_name}")
        return True

    def validate_config(
        self,
        skill_name: str,
        config: dict,
    ) -> tuple[bool, list[str]]:
        """
        验证配置是否符合 Schema

        Args:
            skill_name: 技能名称
            config: 配置字典

        Returns:
            tuple: (is_valid, errors)
        """
        schema = self.get_schema(skill_name)
        if not schema:
            return True, []  # 没有 Schema，默认通过

        errors = []
        self._validate_fields(config, schema.fields, errors, prefix="")

        return len(errors) == 0, errors

    def _validate_fields(
        self,
        config: dict,
        fields: list[SchemaField],
        errors: list[str],
        prefix: str = "",
    ) -> None:
        """递归验证字段"""
        for field_def in fields:
            key = field_def.key
            full_key = f"{prefix}{key}" if prefix else key
            value = config.get(key)

            # 检查必填字段
            if field_def.required and value is None:
                errors.append(f"字段 '{full_key}' 是必填的")
                continue

            if value is None:
                continue

            # 类型验证
            if field_def.type == "string" or field_def.type == "password":
                if not isinstance(value, str):
                    errors.append(f"字段 '{full_key}' 必须是字符串")

            elif field_def.type == "number":
                if not isinstance(value, (int, float)):
                    errors.append(f"字段 '{full_key}' 必须是数字")
                elif field_def.min is not None and value < field_def.min:
                    errors.append(f"字段 '{full_key}' 不能小于 {field_def.min}")
                elif field_def.max is not None and value > field_def.max:
                    errors.append(f"字段 '{full_key}' 不能大于 {field_def.max}")

            elif field_def.type == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"字段 '{full_key}' 必须是布尔值")

            elif field_def.type == "select":
                valid_values = [opt["value"] for opt in field_def.options]
                if value not in valid_values:
                    errors.append(f"字段 '{full_key}' 必须是以下值之一: {valid_values}")

            elif field_def.type == "object":
                if not isinstance(value, dict):
                    errors.append(f"字段 '{full_key}' 必须是对象")
                else:
                    # 递归验证嵌套字段
                    self._validate_fields(
                        value, field_def.fields, errors, prefix=f"{full_key}."
                    )

    def get_default_config(self, skill_name: str) -> dict:
        """
        获取技能的默认配置

        Args:
            skill_name: 技能名称

        Returns:
            dict: 默认配置
        """
        schema = self.get_schema(skill_name)
        if not schema:
            return {}

        config = {}
        self._add_defaults(config, schema.fields)
        return config

    def _add_defaults(self, config: dict, fields: list[SchemaField]) -> None:
        """递归添加默认值"""
        for field_def in fields:
            if field_def.type == "object":
                # 对象类型
                obj = {}
                if field_def.fields:
                    self._add_defaults(obj, field_def.fields)
                if obj or field_def.default is not None:
                    config[field_def.key] = (
                        field_def.default if field_def.default is not None else obj
                    )
            elif field_def.default is not None:
                config[field_def.key] = field_def.default


# ==================== 预定义 Schema ====================

# 常用字段模板
FIELD_API_KEY = SchemaField(
    key="api_key",
    type="password",
    label="API Key",
    description="API 密钥",
    required=True,
    sensitive=True,
    placeholder="请输入 API Key",
)

FIELD_BASE_URL = SchemaField(
    key="base_url",
    type="string",
    label="API 地址",
    description="API 服务地址",
    default="",
    placeholder="https://api.example.com",
)

FIELD_TIMEOUT = SchemaField(
    key="timeout",
    type="number",
    label="超时时间（秒）",
    description="请求超时时间",
    default=30,
    min=5,
    max=300,
)

FIELD_MAX_RESULTS = SchemaField(
    key="max_results",
    type="number",
    label="最大结果数",
    description="返回的最大结果数量",
    default=10,
    min=1,
    max=100,
)


# ==================== 全局实例 ====================

_global_schema_registry: SkillsSchemaRegistry | None = None


def get_schema_registry(skills_dir: Path | None = None) -> SkillsSchemaRegistry:
    """获取全局 Schema 注册表实例"""
    global _global_schema_registry

    if _global_schema_registry is None:
        if skills_dir is None:
            skills_dir = Path.cwd() / "skills"
        _global_schema_registry = SkillsSchemaRegistry(skills_dir)

    return _global_schema_registry


def init_schema_registry(skills_dir: Path) -> SkillsSchemaRegistry:
    """初始化全局 Schema 注册表"""
    global _global_schema_registry
    _global_schema_registry = SkillsSchemaRegistry(skills_dir)
    return _global_schema_registry
