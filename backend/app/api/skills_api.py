"""
Skills API - 技能管理接口

提供技能的加载、启用/禁用、CRUD 等功能
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.agent.skills import (
    SkillsLoader,
    get_skills_loader,
)
from app.modules.agent.skills_config import (
    SkillsConfigManager,
    get_config_manager,
)
from app.modules.agent.skills_schema import (
    SkillSchema,
    SkillsSchemaRegistry,
    get_schema_registry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["技能管理"])


# ==================== 请求模型 ====================


class CreateSkillRequest(BaseModel):
    """创建技能请求"""

    name: str
    content: str


class UpdateSkillRequest(BaseModel):
    """更新技能请求"""

    content: str


class ToggleSkillRequest(BaseModel):
    """切换技能状态请求"""

    enabled: bool


class SetConfigRequest(BaseModel):
    """设置配置请求"""

    config: dict


class SetFieldRequest(BaseModel):
    """设置字段请求"""

    field_key: str
    value: Any


# ==================== 依赖 ====================


def get_loader() -> SkillsLoader:
    """获取技能加载器实例"""
    skills_dir = Path.cwd() / "skills"
    return get_skills_loader(skills_dir)


def get_config_mgr() -> SkillsConfigManager:
    """获取配置管理器实例"""
    skills_dir = Path.cwd() / "skills"
    return get_config_manager(skills_dir)


def get_schema_reg() -> SkillsSchemaRegistry:
    """获取 Schema 注册表实例"""
    skills_dir = Path.cwd() / "skills"
    return get_schema_registry(skills_dir)


# ==================== API 端点 ====================


@router.get("")
async def list_skills(
    enabled_only: bool = Query(False, description="只返回已启用的技能"),
    filter_unavailable: bool = Query(False, description="过滤掉依赖未满足的技能"),
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取技能列表"""
    skills = loader.list_skills(
        enabled_only=enabled_only,
        filter_unavailable=filter_unavailable,
    )
    return {
        "skills": skills,
        "total": len(skills),
    }


@router.get("/stats")
async def get_stats(
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取技能统计"""
    return loader.get_stats()


@router.get("/summary")
async def get_summary(
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取技能摘要（用于 Agent 上下文）"""
    summary = loader.build_skills_summary()
    return {"summary": summary}


@router.get("/always")
async def get_always_skills(
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取自动加载的技能"""
    names = loader.get_always_skills()
    return {
        "skills": names,
        "total": len(names),
    }


@router.get("/{name}")
async def get_skill(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取单个技能详情"""
    skill = loader.get_skill(name)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    return skill.to_dict()


@router.get("/{name}/content")
async def get_skill_content(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """获取技能内容"""
    try:
        content = loader.read_skill(name)
        return {"name": name, "content": content}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{name}/requirements")
async def check_requirements(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """检查技能依赖"""
    skill = loader.get_skill(name)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    satisfied = skill.check_requirements()
    missing = skill.get_missing_requirements()

    return {
        "name": name,
        "satisfied": satisfied,
        "missing": missing if missing else None,
    }


@router.post("/{name}/toggle")
async def toggle_skill(
    name: str,
    request: ToggleSkillRequest,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """切换技能启用状态"""
    success = loader.toggle_skill(name, request.enabled)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to toggle skill '{name}'")

    return {
        "success": True,
        "name": name,
        "enabled": request.enabled,
    }


@router.post("/{name}/enable")
async def enable_skill(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """启用技能"""
    success = loader.enable_skill(name)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to enable skill '{name}'")

    return {"success": True, "name": name, "enabled": True}


@router.post("/{name}/disable")
async def disable_skill(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """禁用技能"""
    success = loader.disable_skill(name)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to disable skill '{name}'")

    return {"success": True, "name": name, "enabled": False}


@router.post("")
async def create_skill(
    request: CreateSkillRequest,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """创建新技能"""
    # 验证技能名称
    if (
        not request.name.isalnum()
        and "_" not in request.name
        and "-" not in request.name
    ):
        raise HTTPException(
            status_code=400,
            detail="Skill name must be alphanumeric, underscore, or hyphen",
        )

    success = loader.add_skill(request.name, request.content)

    if not success:
        raise HTTPException(
            status_code=400, detail=f"Failed to create skill '{request.name}'"
        )

    return {
        "success": True,
        "name": request.name,
    }


@router.put("/{name}")
async def update_skill(
    name: str,
    request: UpdateSkillRequest,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """更新技能"""
    success = loader.update_skill(name, request.content)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to update skill '{name}'")

    return {"success": True, "name": name}


@router.delete("/{name}")
async def delete_skill(
    name: str,
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """删除技能"""
    success = loader.delete_skill(name)

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to delete skill '{name}'")

    return {"success": True, "name": name}


@router.post("/reload")
async def reload_skills(
    current_user: User = Depends(get_current_active_user),
    loader: SkillsLoader = Depends(get_loader),
):
    """重新加载所有技能"""
    loader.reload()
    return {
        "success": True,
        "total": len(loader.skills),
    }


# ==================== 配置管理 API ====================


@router.get("/{name}/config")
async def get_skill_config(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """获取技能配置"""
    config = config_mgr.load_config(name)

    # 如果没有配置，返回默认配置
    if config is None:
        schema_reg = get_schema_reg()
        config = schema_reg.get_default_config(name)

    return {
        "name": name,
        "config": config or {},
    }


@router.put("/{name}/config")
async def set_skill_config(
    name: str,
    request: SetConfigRequest,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
    schema_reg: SkillsSchemaRegistry = Depends(get_schema_reg),
):
    """设置技能配置"""
    # 验证配置
    is_valid, errors = schema_reg.validate_config(name, request.config)

    if not is_valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    success = config_mgr.save_config(name, request.config)

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to save config for skill '{name}'"
        )

    return {"success": True, "name": name}


@router.patch("/{name}/config")
async def set_skill_field(
    name: str,
    request: SetFieldRequest,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """设置技能配置的单个字段"""
    success = config_mgr.set_field_value(name, request.field_key, request.value)

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set field '{request.field_key}' for skill '{name}'",
        )

    return {
        "success": True,
        "name": name,
        "field_key": request.field_key,
    }


@router.delete("/{name}/config")
async def delete_skill_config(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """删除技能配置"""
    success = config_mgr.delete_config(name)

    if not success:
        raise HTTPException(
            status_code=404, detail=f"Config not found for skill '{name}'"
        )

    return {"success": True, "name": name}


@router.get("/{name}/config/status")
async def get_config_status(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """获取技能配置状态"""
    status = config_mgr.get_config_status(name)
    return status.to_dict()


@router.post("/{name}/config/fix")
async def fix_skill_config(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """自动修复技能配置"""
    success, changes = config_mgr.auto_fix_config(name)

    if not success:
        raise HTTPException(status_code=400, detail={"changes": changes})

    return {
        "success": True,
        "name": name,
        "changes": changes,
    }


@router.get("/{name}/config/help")
async def get_config_help(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """获取技能配置帮助文档"""
    help_content = config_mgr.get_help_content(name)

    if help_content is None:
        raise HTTPException(
            status_code=404, detail=f"Help content not found for skill '{name}'"
        )

    return {
        "name": name,
        "help": help_content,
    }


# ==================== Schema 管理 API ====================


@router.get("/{name}/schema")
async def get_skill_schema(
    name: str,
    current_user: User = Depends(get_current_active_user),
    schema_reg: SkillsSchemaRegistry = Depends(get_schema_reg),
):
    """获取技能 Schema"""
    schema = schema_reg.get_schema(name)

    if schema is None:
        raise HTTPException(
            status_code=404, detail=f"Schema not found for skill '{name}'"
        )

    return schema.to_dict()


@router.post("/{name}/schema")
async def create_skill_schema(
    name: str,
    schema_data: dict,
    current_user: User = Depends(get_current_active_user),
    schema_reg: SkillsSchemaRegistry = Depends(get_schema_reg),
):
    """创建技能 Schema"""
    try:
        schema = SkillSchema.from_dict({**schema_data, "skill_name": name})
        schema_reg.register_schema(schema)
        return {"success": True, "name": name}
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to create schema: {str(e)}"
        )


@router.delete("/{name}/schema")
async def delete_skill_schema(
    name: str,
    current_user: User = Depends(get_current_active_user),
    schema_reg: SkillsSchemaRegistry = Depends(get_schema_reg),
):
    """删除技能 Schema"""
    success = schema_reg.delete_schema(name)

    if not success:
        raise HTTPException(
            status_code=404, detail=f"Schema not found for skill '{name}'"
        )

    return {"success": True, "name": name}


@router.get("/configs/status")
async def list_configs_status(
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(get_config_mgr),
):
    """列出所有技能的配置状态"""
    return config_mgr.list_configs()
