"""
多模态输出模块测试

测试图片生成功能。
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from app.modules.output.image_gen import (
    ImageGenerationResult,
    ImageGenerator,
    ImageProvider,
    MiniMaxImageGenerator,
    OpenAIImageGenerator,
)

# ------------------------------------------------------------------
# 图片生成测试
# ------------------------------------------------------------------


class TestImageGenerationResult:
    """图片生成结果测试"""

    def test_success_result(self):
        """测试成功结果"""
        result = ImageGenerationResult(
            success=True,
            image_url="https://example.com/image.png",
            revised_prompt="A beautiful sunset",
        )
        assert result.success
        assert result.image_url == "https://example.com/image.png"
        assert result.error is None

    def test_failure_result(self):
        """测试失败结果"""
        result = ImageGenerationResult(
            success=False,
            error="API error",
        )
        assert not result.success
        assert result.error == "API error"


class TestOpenAIImageGenerator:
    """OpenAI 图片生成器测试"""

    def test_init(self):
        """测试初始化"""
        generator = OpenAIImageGenerator(api_key="test_key")
        assert generator.api_key == "test_key"
        assert generator.base_url == "https://api.openai.com/v1"

    def test_init_with_custom_url(self):
        """测试自定义 URL"""
        generator = OpenAIImageGenerator(
            api_key="test_key",
            base_url="https://custom.api.com/v1",
        )
        assert generator.base_url == "https://custom.api.com/v1"

    @pytest.mark.asyncio
    async def test_generate_success(self):
        """测试成功生成"""
        generator = OpenAIImageGenerator(api_key="test_key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "url": "https://example.com/image.png",
                    "revised_prompt": "A beautiful landscape",
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await generator.generate(prompt="A sunset")

            assert result.success
            assert result.image_url == "https://example.com/image.png"

    @pytest.mark.asyncio
    async def test_generate_failure(self):
        """测试生成失败"""
        generator = OpenAIImageGenerator(api_key="test_key")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await generator.generate(prompt="Test")

            assert not result.success
            assert result.error is not None


class TestMiniMaxImageGenerator:
    """MiniMax 图片生成器测试"""

    def test_init(self):
        """测试初始化"""
        generator = MiniMaxImageGenerator(
            api_key="test_key",
            group_id="test_group",
        )
        assert generator.api_key == "test_key"
        assert generator.group_id == "test_group"

    @pytest.mark.asyncio
    async def test_generate_success(self):
        """测试成功生成"""
        generator = MiniMaxImageGenerator(
            api_key="test_key",
            group_id="test_group",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "image_url": "https://example.com/image.png",
            }
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await generator.generate(prompt="A sunset")

            assert result.success
            assert result.image_url == "https://example.com/image.png"


class TestImageGenerator:
    """统一图片生成器测试"""

    def test_init_openai(self):
        """测试 OpenAI 初始化"""
        generator = ImageGenerator(
            provider=ImageProvider.OPENAI,
            api_key="test_key",
        )
        assert generator.provider == ImageProvider.OPENAI

    def test_init_minimax(self):
        """测试 MiniMax 初始化"""
        generator = ImageGenerator(
            provider=ImageProvider.MINIMAX,
            api_key="test_key",
            group_id="test_group",
        )
        assert generator.provider == ImageProvider.MINIMAX

    def test_get_generator_openai(self):
        """测试获取 OpenAI 生成器"""
        generator = ImageGenerator(
            provider=ImageProvider.OPENAI,
            api_key="test_key",
        )
        gen = generator._get_generator()
        assert isinstance(gen, OpenAIImageGenerator)

    def test_get_generator_minimax(self):
        """测试获取 MiniMax 生成器"""
        generator = ImageGenerator(
            provider=ImageProvider.MINIMAX,
            api_key="test_key",
            group_id="test_group",
        )
        gen = generator._get_generator()
        assert isinstance(gen, MiniMaxImageGenerator)

    def test_missing_api_key(self):
        """测试缺少 API key"""
        generator = ImageGenerator(provider=ImageProvider.OPENAI)
        with pytest.raises(ValueError, match="API key"):
            generator._get_generator()


# ------------------------------------------------------------------
# API 测试
# ------------------------------------------------------------------


class TestOutputAPI:
    """多模态输出 API 测试"""

    @pytest.mark.asyncio
    async def test_image_generate_endpoint(self, async_client, mock_user):
        """测试图片生成端点"""
        # 需要 mock ImageGenerator
        pass


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------


@pytest.fixture
def mock_user():
    """Mock 用户"""
    user = Mock()
    user.id = 1
    user.username = "test_user"
    return user


@pytest.fixture
def async_client():
    """Mock HTTP 客户端"""
    from httpx import AsyncClient

    return Mock(spec=AsyncClient)
