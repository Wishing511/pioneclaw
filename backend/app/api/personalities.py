"""
Personalities API - AI 性格管理接口

提供性格预设查询、自定义性格管理等功能
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.agent.personalities import (
    Personality,
    PersonalityCategory,
    get_all_personalities,
    get_all_personality_ids,
    get_default_personality_id,
    get_personality_info,
    get_personality_prompt,
    get_personality_system_prompt,
    is_valid_personality_id,
    register_custom_personality,
    unregister_custom_personality,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/personalities", tags=["性格管理"])


# ==================== 请求模型 ====================


class CustomPersonalityRequest(BaseModel):
    """自定义性格请求"""

    id: str
    name: str
    description: str
    traits: list[str]
    speaking_style: str
    category: str = "professional"
    emoji_list: list[str] = []
    example_phrases: list[str] = []


class GeneratePromptRequest(BaseModel):
    """生成提示词请求"""

    personality_id: str
    custom_text: str = ""


# ==================== API 端点 ====================


@router.get("")
async def list_personalities(
    category: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """获取所有性格预设"""
    all_personalities = get_all_personalities()

    personalities_list = []
    for _pid, personality in all_personalities.items():
        info = personality.to_dict()
        # 过滤分类
        if category and info["category"] != category:
            continue
        personalities_list.append(info)

    return {
        "personalities": personalities_list,
        "total": len(personalities_list),
        "default": get_default_personality_id(),
    }


@router.get("/ids")
async def list_personality_ids(
    current_user: User = Depends(get_current_active_user),
):
    """获取所有性格 ID 列表"""
    return {
        "ids": get_all_personality_ids(),
        "default": get_default_personality_id(),
    }


@router.get("/categories")
async def list_categories(
    current_user: User = Depends(get_current_active_user),
):
    """获取性格分类列表"""
    return {
        "categories": [
            {
                "value": "humorous",
                "label": "幽默类",
                "description": "幽默风趣，轻松愉快",
            },
            {
                "value": "professional",
                "label": "专业类",
                "description": "严谨专业，逻辑清晰",
            },
            {"value": "warm", "label": "温暖类", "description": "温柔体贴，关怀备至"},
            {"value": "unique", "label": "独特类", "description": "个性鲜明，风格独特"},
        ],
    }


@router.get("/{personality_id}")
async def get_personality(
    personality_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取性格详情"""
    info = get_personality_info(personality_id)

    if not info:
        raise HTTPException(status_code=404, detail="Personality not found")

    return info


@router.get("/{personality_id}/prompt")
async def get_prompt(
    personality_id: str,
    custom_text: str = "",
    current_user: User = Depends(get_current_active_user),
):
    """获取性格提示词片段"""
    if not is_valid_personality_id(personality_id):
        raise HTTPException(status_code=400, detail="Invalid personality ID")

    prompt = get_personality_prompt(personality_id, custom_text)
    return {"personality_id": personality_id, "prompt": prompt}


@router.post("/system-prompt")
async def generate_system_prompt(
    request: GeneratePromptRequest,
    current_user: User = Depends(get_current_active_user),
):
    """生成完整的性格系统提示词"""
    if not is_valid_personality_id(request.personality_id):
        raise HTTPException(status_code=400, detail="Invalid personality ID")

    system_prompt = get_personality_system_prompt(
        request.personality_id, request.custom_text
    )
    return {
        "personality_id": request.personality_id,
        "system_prompt": system_prompt,
    }


@router.post("/custom")
async def create_custom_personality(
    request: CustomPersonalityRequest,
    current_user: User = Depends(get_current_active_user),
):
    """创建自定义性格"""
    # 检查 ID 是否已存在
    if request.id in get_all_personalities():
        raise HTTPException(status_code=400, detail="Personality ID already exists")

    # 验证分类
    try:
        category = PersonalityCategory(request.category)
    except ValueError:
        category = PersonalityCategory.PROFESSIONAL

    # 创建性格
    personality = Personality(
        id=request.id,
        name=request.name,
        description=request.description,
        traits=request.traits,
        speaking_style=request.speaking_style,
        category=category,
        emoji_list=request.emoji_list,
        example_phrases=request.example_phrases,
        is_builtin=False,
    )

    register_custom_personality(personality)

    return {
        "success": True,
        "personality": personality.to_dict(),
    }


@router.delete("/custom/{personality_id}")
async def delete_custom_personality(
    personality_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除自定义性格"""
    success = unregister_custom_personality(personality_id)

    if not success:
        raise HTTPException(status_code=404, detail="Custom personality not found")

    return {"success": True, "message": f"Personality {personality_id} deleted"}
