"""
Provider 管理 API

提供 Provider 的 CRUD 和配置接口
"""

import logging
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models.models import User
from app.modules.providers import (
    THINKING_PROFILES,
    ModelOverride,
    ProviderConfig,
    ProviderType,
    get_provider_factory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["Provider 管理"])


# ==================== 请求模型 ====================


class ProviderTypeEnum(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    AZURE = "azure"
    LOCAL = "local"
    CUSTOM = "custom"


class ProviderCreateRequest(BaseModel):
    """创建 Provider 请求"""

    provider_type: ProviderTypeEnum
    name: str
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    default_model: str | None = None
    timeout: float = 60.0
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_thinking: bool = False
    extra: dict = {}


class ProviderUpdateRequest(BaseModel):
    """更新 Provider 请求"""

    name: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    default_model: str | None = None
    timeout: float | None = None
    extra: dict | None = None


class ModelOverrideRequest(BaseModel):
    """模型覆盖请求"""

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    thinking_enabled: bool | None = None
    thinking_budget: int | None = None


# ==================== API 端点 ====================


@router.get("")
async def list_providers(
    current_user: User = Depends(get_current_active_user),
):
    """获取所有 Provider"""
    factory = get_provider_factory()
    providers = factory.get_available_providers()

    result = []
    for provider_id in providers:
        provider = factory.get(provider_id)
        if provider:
            result.append(provider.get_info())

    return {
        "providers": result,
        "total": len(result),
    }


@router.post("")
async def create_provider(
    request: ProviderCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """创建 Provider"""
    factory = get_provider_factory()

    # 生成 Provider ID
    import uuid

    provider_id = f"{request.provider_type.value}_{uuid.uuid4().hex[:8]}"

    # 创建配置
    config = ProviderConfig(
        provider_id=provider_id,
        provider_type=ProviderType(request.provider_type.value),
        name=request.name,
        api_key=request.api_key,
        api_base=request.api_base,
        api_version=request.api_version,
        default_model=request.default_model,
        timeout=request.timeout,
        supports_streaming=request.supports_streaming,
        supports_tools=request.supports_tools,
        supports_vision=request.supports_vision,
        supports_thinking=request.supports_thinking,
        extra=request.extra,
    )

    # 注册配置
    factory.register_config(config)

    # 创建实例
    provider = factory.create(provider_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Failed to create provider")

    return {
        "success": True,
        "provider_id": provider_id,
        "info": provider.get_info(),
    }


@router.get("/{provider_id}")
async def get_provider(
    provider_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取 Provider 详情"""
    factory = get_provider_factory()
    provider = factory.get(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    return provider.get_info()


@router.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    request: ProviderUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """更新 Provider 配置"""
    factory = get_provider_factory()
    provider = factory.get(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # 更新配置
    config = provider.config
    if request.name:
        config.name = request.name
    if request.api_key:
        config.api_key = request.api_key
    if request.api_base:
        config.api_base = request.api_base
    if request.default_model:
        config.default_model = request.default_model
    if request.timeout is not None:
        config.timeout = request.timeout
    if request.extra:
        config.extra.update(request.extra)

    return {"success": True, "message": "Provider updated"}


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除 Provider"""
    factory = get_provider_factory()

    if provider_id not in factory._configs:
        raise HTTPException(status_code=404, detail="Provider not found")

    # 移除配置和实例
    del factory._configs[provider_id]
    if provider_id in factory._instances:
        del factory._instances[provider_id]
    if provider_id in factory._key_rotators:
        del factory._key_rotators[provider_id]

    return {"success": True, "message": "Provider deleted"}


@router.post("/{provider_id}/override")
async def apply_model_override(
    provider_id: str,
    request: ModelOverrideRequest,
    current_user: User = Depends(get_current_active_user),
):
    """应用模型覆盖配置"""
    factory = get_provider_factory()
    provider = factory.get(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # 创建覆盖配置
    override = ModelOverride(
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        thinking_enabled=request.thinking_enabled,
        thinking_budget=request.thinking_budget,
    )

    return {
        "success": True,
        "override": {
            "model": override.model,
            "temperature": override.temperature,
            "max_tokens": override.max_tokens,
            "thinking_enabled": override.thinking_enabled,
            "thinking_budget": override.thinking_budget,
        },
    }


@router.get("/types/supported")
async def get_supported_types(
    current_user: User = Depends(get_current_active_user),
):
    """获取支持的 Provider 类型"""
    factory = get_provider_factory()
    types = factory.get_supported_types()

    return {
        "types": [
            {"value": "openai", "label": "OpenAI", "supports_thinking": False},
            {
                "value": "anthropic",
                "label": "Anthropic (Claude)",
                "supports_thinking": True,
            },
            {"value": "azure", "label": "Azure OpenAI", "supports_thinking": False},
            {"value": "google", "label": "Google AI", "supports_thinking": False},
            {
                "value": "local",
                "label": "本地模型 (Ollama)",
                "supports_thinking": False,
            },
        ],
        "supported": types,
    }


@router.get("/thinking-profiles")
async def get_thinking_profiles(
    current_user: User = Depends(get_current_active_user),
):
    """获取 Thinking Profiles 配置"""
    return {
        "profiles": {
            name: {
                "name": profile.name,
                "enabled": profile.enabled,
                "budget_tokens": profile.budget_tokens,
                "max_thinking_tokens": profile.max_thinking_tokens,
                "show_thinking": profile.show_thinking,
            }
            for name, profile in THINKING_PROFILES.items()
        },
        "default": "default",
    }


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """测试 Provider 连接"""
    factory = get_provider_factory()
    provider = factory.get(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # 简单的测试消息
    try:
        from app.modules.providers.base import ChatMessage

        messages = [ChatMessage(role="user", content="Hello")]

        response = await provider.chat(
            messages=messages,
            max_tokens=50,
        )

        if "error" in response:
            return {"success": False, "error": response["error"]}

        return {
            "success": True,
            "message": "Provider connection successful",
            "model": response.get("model", provider.config.default_model),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
