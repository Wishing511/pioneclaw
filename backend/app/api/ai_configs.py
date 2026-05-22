import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import AIModelConfig, User
from app.schemas import (
    AIModelConfigCreate,
    AIModelConfigResponse,
    AIModelConfigUpdate,
    AIModelTestRequest,
    AIModelTestResponse,
    MessageResponse,
)


class ApiKeyResponse(BaseModel):
    api_key: str | None


router = APIRouter(prefix="/ai-configs", tags=["AI模型配置"])


@router.get("", response_model=list[AIModelConfigResponse])
async def list_ai_configs(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取所有 AI 模型配置"""
    result = await db.execute(
        select(AIModelConfig)
        .order_by(AIModelConfig.is_default.desc(), AIModelConfig.name)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{config_id}/api-key")
async def get_ai_config_api_key(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 API Key 明文（仅管理员）"""
    if not (current_user.is_super_admin or current_user.is_org_admin):
        raise HTTPException(status_code=403, detail="仅管理员可查看 API Key")
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"api_key": config.api_key}


@router.get("/{config_id}", response_model=AIModelConfigResponse)
async def get_ai_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取单个 AI 模型配置"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return config


@router.post(
    "", response_model=AIModelConfigResponse, status_code=status.HTTP_201_CREATED
)
async def create_ai_config(
    config_data: AIModelConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建 AI 模型配置"""
    # 检查名称是否已存在
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.name == config_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="配置名称已存在")

    # 如果设为默认，取消其他默认配置
    if config_data.is_default:
        result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
        for old_default in result.scalars().all():
            old_default.is_default = False

    # display_name 默认和 name 一样
    display_name = config_data.display_name or config_data.name

    config = AIModelConfig(
        name=config_data.name,
        display_name=display_name,
        provider=config_data.provider,
        model_name=config_data.model_name,
        base_url=config_data.base_url,
        api_key=config_data.api_key,  # TODO: 加密存储
        context_window=config_data.context_window,
        max_tokens=config_data.max_tokens,
        temperature=config_data.temperature,
        is_default=config_data.is_default,
        extra_config=config_data.extra_config,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.put("/{config_id}", response_model=AIModelConfigResponse)
async def update_ai_config(
    config_id: int,
    config_data: AIModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 AI 模型配置"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    update_data = config_data.model_dump(exclude_unset=True)

    # 如果设为默认，取消其他默认配置
    if update_data.get("is_default"):
        result = await db.execute(
            select(AIModelConfig).where(
                AIModelConfig.is_default, AIModelConfig.id != config_id
            )
        )
        for old_default in result.scalars().all():
            old_default.is_default = False

    for key, value in update_data.items():
        setattr(config, key, value)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{config_id}", response_model=MessageResponse)
async def delete_ai_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除 AI 模型配置"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    await db.delete(config)
    await db.commit()
    return MessageResponse(message="配置已删除")


@router.post("/test", response_model=AIModelTestResponse)
async def test_ai_config(
    test_data: AIModelTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """测试 AI 模型配置"""
    config = None

    if test_data.model_config_id:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.id == test_data.model_config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="配置不存在")

        provider = config.provider
        model_name = config.model_name
        base_url = config.base_url
        api_key = config.api_key
    else:
        provider = test_data.provider or "openai"
        model_name = test_data.model_name
        base_url = test_data.base_url
        api_key = test_data.api_key

    if not api_key:
        return AIModelTestResponse(success=False, message="API Key 未配置")

    if not model_name:
        return AIModelTestResponse(success=False, message="模型名称未配置")

    # 根据提供商构建请求
    if provider == "anthropic":
        url = base_url or "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_name,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": test_data.test_prompt}],
        }
    else:  # OpenAI 兼容
        url = base_url or "https://api.openai.com/v1/chat/completions"
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_name,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": test_data.test_prompt}],
        }

    try:
        start_time = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=body)
        latency_ms = int((time.time() - start_time) * 1000)

        if response.status_code == 200:
            data = response.json()
            if provider == "anthropic":
                content = data.get("content", [{}])[0].get("text", "")
            else:
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                )

            return AIModelTestResponse(
                success=True,
                message="连接成功",
                response=content,
                latency_ms=latency_ms,
            )
        else:
            error_detail = response.text[:200] if response.text else "无响应"
            try:
                error_json = response.json()
                if "error" in error_json:
                    error_detail = str(
                        error_json["error"].get("message", error_detail)
                    )[:200]
            except Exception:
                pass
            return AIModelTestResponse(
                success=False,
                message=f"API 错误 ({response.status_code}): {error_detail}",
                latency_ms=latency_ms,
            )
    except httpx.TimeoutException:
        return AIModelTestResponse(success=False, message="连接超时")
    except Exception as e:
        err_str = str(e).encode("utf-8", errors="replace").decode("utf-8")
        return AIModelTestResponse(success=False, message=f"连接失败: {err_str}")


@router.post("/{config_id}/set-default", response_model=MessageResponse)
async def set_default_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """设置默认配置"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    # 取消其他默认配置
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.is_default))
    for old_default in result.scalars().all():
        old_default.is_default = False

    config.is_default = True
    await db.commit()
    return MessageResponse(message=f"已将 {config.display_name} 设为默认配置")
