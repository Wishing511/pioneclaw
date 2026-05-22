"""
图片生成模块

支持多种图片生成 API：
- OpenAI DALL-E 3
- MiniMax
- Stability AI
"""

import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class ImageProvider(str, Enum):
    """图片生成提供商"""

    OPENAI = "openai"
    MINIMAX = "minimax"
    STABILITY = "stability"


@dataclass
class ImageGenerationResult:
    """图片生成结果"""

    success: bool
    image_url: str | None = None
    image_base64: str | None = None
    revised_prompt: str | None = None
    error: str | None = None


class BaseImageGenerator(ABC):
    """图片生成器基类"""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
    ) -> ImageGenerationResult:
        """生成图片"""
        pass


class OpenAIImageGenerator(BaseImageGenerator):
    """OpenAI DALL-E 图片生成器"""

    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "dall-e-3",
        response_format: str = "url",  # url or b64_json
    ) -> ImageGenerationResult:
        """生成图片"""
        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": size,
            "quality": quality,
            "response_format": response_format,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    images = data.get("data", [])
                    if images:
                        first_image = images[0]
                        return ImageGenerationResult(
                            success=True,
                            image_url=first_image.get("url"),
                            image_base64=first_image.get("b64_json"),
                            revised_prompt=first_image.get("revised_prompt"),
                        )
                    return ImageGenerationResult(
                        success=False, error="No image returned"
                    )
                else:
                    error_msg = response.text
                    try:
                        error_json = response.json()
                        if "error" in error_json:
                            error_msg = error_json["error"].get("message", error_msg)
                    except Exception:
                        pass
                    return ImageGenerationResult(success=False, error=error_msg)

        except Exception as e:
            logger.error(f"OpenAI image generation failed: {e}")
            return ImageGenerationResult(success=False, error=str(e))


class MiniMaxImageGenerator(BaseImageGenerator):
    """MiniMax 图片生成器"""

    def __init__(self, api_key: str, group_id: str, base_url: str | None = None):
        self.api_key = api_key
        self.group_id = group_id
        self.base_url = base_url or "https://api.minimax.chat/v1"

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "image-01",
    ) -> ImageGenerationResult:
        """生成图片"""
        url = f"{self.base_url}/image_generation"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "group_id": self.group_id,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    # MiniMax 返回格式可能不同，需要适配
                    image_url = data.get("data", {}).get("image_url")
                    if image_url:
                        return ImageGenerationResult(
                            success=True,
                            image_url=image_url,
                        )
                    # 尝试其他格式
                    base64_image = data.get("data", {}).get("image")
                    if base64_image:
                        return ImageGenerationResult(
                            success=True,
                            image_base64=base64_image,
                        )
                    return ImageGenerationResult(
                        success=False, error="No image returned"
                    )
                else:
                    return ImageGenerationResult(success=False, error=response.text)

        except Exception as e:
            logger.error(f"MiniMax image generation failed: {e}")
            return ImageGenerationResult(success=False, error=str(e))


class ImageGenerator:
    """
    统一图片生成器

    根据配置选择不同的提供商
    """

    def __init__(
        self,
        provider: ImageProvider = ImageProvider.OPENAI,
        api_key: str | None = None,
        base_url: str | None = None,
        group_id: str | None = None,  # MiniMax 需要
    ):
        self.provider = provider
        self._generator: BaseImageGenerator | None = None
        self.api_key = api_key
        self.base_url = base_url
        self.group_id = group_id

    def _get_generator(self) -> BaseImageGenerator:
        """获取实际生成器"""
        if self._generator is None:
            if self.provider == ImageProvider.OPENAI:
                if not self.api_key:
                    raise ValueError("OpenAI API key is required")
                self._generator = OpenAIImageGenerator(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            elif self.provider == ImageProvider.MINIMAX:
                if not self.api_key or not self.group_id:
                    raise ValueError("MiniMax API key and group_id are required")
                self._generator = MiniMaxImageGenerator(
                    api_key=self.api_key,
                    group_id=self.group_id,
                    base_url=self.base_url,
                )
            else:
                raise ValueError(f"Unsupported provider: {self.provider}")
        return self._generator

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        **kwargs,
    ) -> ImageGenerationResult:
        """
        生成图片

        Args:
            prompt: 图片描述
            size: 图片尺寸 (256x256, 512x512, 1024x1024)
            quality: 图片质量 (standard, hd)
            n: 生成数量
            **kwargs: 其他提供商特定参数

        Returns:
            ImageGenerationResult: 生成结果
        """
        generator = self._get_generator()
        return await generator.generate(prompt, size, quality, n, **kwargs)

    async def generate_and_save(
        self,
        prompt: str,
        output_path: str,
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> ImageGenerationResult:
        """
        生成图片并保存到文件

        Args:
            prompt: 图片描述
            output_path: 输出文件路径
            size: 图片尺寸
            quality: 图片质量

        Returns:
            ImageGenerationResult: 生成结果
        """
        result = await self.generate(prompt, size, quality)

        if result.success:
            try:
                if result.image_base64:
                    # 从 base64 保存
                    image_data = base64.b64decode(result.image_base64)
                    with open(output_path, "wb") as f:
                        f.write(image_data)
                elif result.image_url:
                    # 从 URL 下载
                    async with httpx.AsyncClient() as client:
                        response = await client.get(result.image_url)
                        if response.status_code == 200:
                            with open(output_path, "wb") as f:
                                f.write(response.content)
                        else:
                            return ImageGenerationResult(
                                success=False,
                                error=f"Failed to download image: {response.status_code}",
                            )
                logger.info(f"Image saved to {output_path}")
            except Exception as e:
                return ImageGenerationResult(
                    success=False, error=f"Failed to save image: {e}"
                )

        return result
