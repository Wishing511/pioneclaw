"""
SecurityClient fail-open 降级测试

验证安全网关在异常场景下正确降级放行（fail-open），
避免安全网关故障导致主业务不可用。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.core.security_client import FilterResult, SecurityClient, apply_input_filter


class TestSecurityClientFailOpen:
    """SecurityClient fail-open 降级测试"""

    @pytest_asyncio.fixture
    async def client(self):
        """每个测试创建新的 SecurityClient 实例（绕过单例）"""
        # 通过重置单例来创建新实例
        SecurityClient._instance = None
        c = SecurityClient()
        c._enabled = True
        c._base_url = "http://localhost:8001"
        yield c
        # 清理
        if c._client and not c._client.is_closed:
            await c.close()
        SecurityClient._instance = None

    @pytest.mark.asyncio
    async def test_disabled_returns_allow(self, client):
        """禁用状态下直接返回 ALLOW"""
        client.set_enabled(False)

        result = await client.filter_input("任何文本")
        assert result.action == "allow"
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_connection_error_fail_open(self, client):
        """连接失败时降级放行"""
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.filter_input("测试文本")
        assert result.action == "allow"
        assert "安全网关异常" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_http_500_fail_open(self, client):
        """HTTP 500 时降级放行"""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception(
            "500 Internal Server Error"
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.filter_output("测试文本")
        assert result.action == "allow"
        assert "安全网关异常" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_timeout_fail_open(self, client):
        """超时时降级放行"""
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("ReadTimeout")
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.check_tool("test_tool", {"arg": "value"})
        assert result.action == "allow"
        assert "安全网关异常" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_successful_filter_input(self, client):
        """正常调用返回实际结果"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "action": "block",
            "reason": "检测到敏感词",
            "risk_level": "critical",
            "matched_rules": [{"type": "sensitive", "word": "测试"}],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.filter_input("测试文本")
        assert result.action == "block"
        assert result.reason == "检测到敏感词"
        assert result.risk_level == "critical"
        assert len(result.matched_rules or []) == 1

    @pytest.mark.asyncio
    async def test_successful_sanitize(self, client):
        """正常调用返回脱敏结果"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "action": "sanitize",
            "content": "脱敏后的内容",
            "reason": "检测到敏感信息",
            "risk_level": "medium",
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.filter_output("原始内容")
        assert result.action == "sanitize"
        assert result.content == "脱敏后的内容"


class TestApplyInputFilter:
    """apply_input_filter helper 测试"""

    @pytest_asyncio.fixture
    async def mock_security_client(self):
        """创建 mock SecurityClient"""
        client = MagicMock(spec=SecurityClient)
        client.enabled = True
        return client

    @pytest.mark.asyncio
    async def test_disabled_returns_original(self, mock_security_client):
        """禁用时返回原始文本"""
        mock_security_client.enabled = False

        text, error = await apply_input_filter(mock_security_client, "原始文本")
        assert text == "原始文本"
        assert error is None

    @pytest.mark.asyncio
    async def test_block_returns_error(self, mock_security_client):
        """block 时返回错误响应"""
        mock_security_client.filter_input = AsyncMock(
            return_value=FilterResult(
                action="block",
                reason="检测到敏感词",
                risk_level="critical",
            )
        )

        text, error = await apply_input_filter(mock_security_client, "测试文本")
        assert text == "测试文本"
        assert error is not None
        assert error["success"] is False
        assert "安全拦截" in error["message"]
        assert "检测到敏感词" in error["message"]
        assert error["latency_ms"] == 0

    @pytest.mark.asyncio
    async def test_approve_returns_error(self, mock_security_client):
        """approve 时返回错误响应"""
        mock_security_client.filter_input = AsyncMock(
            return_value=FilterResult(
                action="approve",
                reason="需人工审批",
                risk_level="high",
            )
        )

        text, error = await apply_input_filter(mock_security_client, "测试文本")
        assert error is not None
        assert "待审批" in error["message"]

    @pytest.mark.asyncio
    async def test_sanitize_returns_filtered_text(self, mock_security_client):
        """sanitize 时返回脱敏文本"""
        mock_security_client.filter_input = AsyncMock(
            return_value=FilterResult(
                action="sanitize",
                content="脱敏后文本",
                reason="已脱敏",
            )
        )

        text, error = await apply_input_filter(mock_security_client, "原始文本")
        assert text == "脱敏后文本"
        assert error is None

    @pytest.mark.asyncio
    async def test_allow_returns_original(self, mock_security_client):
        """allow 时返回原始文本"""
        mock_security_client.filter_input = AsyncMock(
            return_value=FilterResult(action="allow")
        )

        text, error = await apply_input_filter(mock_security_client, "原始文本")
        assert text == "原始文本"
        assert error is None

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self, mock_security_client):
        """helper 内部异常应向上传播（由 SecurityClient.filter_input 负责 fail-open）"""
        mock_security_client.filter_input = AsyncMock(
            side_effect=Exception("Unexpected")
        )

        # apply_input_filter 本身没有 try/except，异常会向上抛
        # 实际使用中 SecurityClient.filter_input 已经捕获所有异常并返回 ALLOW
        with pytest.raises(Exception, match="Unexpected"):
            await apply_input_filter(mock_security_client, "测试文本")
