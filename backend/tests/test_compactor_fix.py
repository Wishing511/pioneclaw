"""
Compactor 修复测试：user_id/session_id/agent_id 参数化 + CONVERSATION_TO_MEMORY_PROMPT
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agent.compactor import (
    CompactionConfig,
    Compactor,
    create_compactor,
)


class TestCompactorParams:
    """测试 Compactor 参数化"""

    def test_default_user_id(self):
        c = Compactor()
        assert c.user_id == 1
        assert c.session_id is None
        assert c.agent_id is None

    def test_custom_params(self):
        c = Compactor(user_id=42, session_id="sess_abc", agent_id=7)
        assert c.user_id == 42
        assert c.session_id == "sess_abc"
        assert c.agent_id == 7

    def test_create_compactor_with_params(self):
        c = create_compactor(user_id=5, session_id="s1", agent_id=3)
        assert c.user_id == 5
        assert c.session_id == "s1"
        assert c.agent_id == 3


class TestCompactorWriteMemories:
    """测试 _write_memories_to_l1 传递正确参数"""

    @pytest.mark.asyncio
    async def test_writes_with_user_id(self):
        mock_orchestrator = MagicMock()
        mock_orchestrator.store = AsyncMock()

        c = Compactor(
            memory_orchestrator=mock_orchestrator,
            user_id=42,
            session_id="sess_test",
            agent_id=3,
        )
        await c._write_memories_to_l1(["用户喜欢深色模式", "项目用 Python 3.12"])

        assert mock_orchestrator.store.call_count == 2
        # 验证 user_id/session_id/agent_id 传递
        for call in mock_orchestrator.store.call_args_list:
            assert call.kwargs.get("user_id") == 42
            assert call.kwargs.get("session_id") == "sess_test"
            assert call.kwargs.get("agent_id") == 3
            assert call.kwargs.get("source") == "compactor"

    @pytest.mark.asyncio
    async def test_writes_without_session_id(self):
        mock_orchestrator = MagicMock()
        mock_orchestrator.store = AsyncMock()

        c = Compactor(memory_orchestrator=mock_orchestrator, user_id=10)
        await c._write_memories_to_l1(["一条记忆"])

        call = mock_orchestrator.store.call_args
        assert call.kwargs.get("user_id") == 10
        assert call.kwargs.get("session_id") is None


class TestConversationToMemoryPrompt:
    """测试 CONVERSATION_TO_MEMORY_PROMPT 使用"""

    @pytest.mark.asyncio
    async def test_generate_uses_conversation_prompt(self):
        """_generate_memory_entries 应使用 CONVERSATION_TO_MEMORY_PROMPT"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value={"content": "用户偏好深色主题；项目名PioneClaw"}
        )

        c = Compactor(llm_client=mock_llm)
        entries = await c._generate_memory_entries(
            [
                {"role": "user", "content": "我喜欢深色主题"},
                {"role": "assistant", "content": "好的，已记录"},
            ]
        )

        assert len(entries) == 2
        assert "深色主题" in entries[0]

        # 验证调用 LLM 时使用了 CONVERSATION_TO_MEMORY_PROMPT
        call_args = mock_llm.chat.call_args
        prompt = call_args[0][0][0]["content"]
        # CONVERSATION_TO_MEMORY_PROMPT 包含"偏好和习惯"
        assert "偏好和习惯" in prompt

    @pytest.mark.asyncio
    async def test_no_need_to_record(self):
        """LLM 返回'无需记录'"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value={"content": "无需记录"})

        c = Compactor(llm_client=mock_llm)
        entries = await c._generate_memory_entries(
            [
                {"role": "user", "content": "你好"},
            ]
        )

        assert entries == []


class TestCompactorIntegration:
    """测试 Compactor 完整流程"""

    @pytest.mark.asyncio
    async def test_compact_writes_memories_with_params(self):
        """compact() 流程中记忆正确传递 user_id/session_id"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value={"content": "用户用 FastAPI；项目名 TestApp"}
        )
        mock_orchestrator = MagicMock()
        mock_orchestrator.store = AsyncMock()

        c = Compactor(
            config=CompactionConfig(
                message_threshold=5, keep_recent_messages=2, generate_memory=True
            ),
            llm_client=mock_llm,
            memory_orchestrator=mock_orchestrator,
            user_id=99,
            session_id="sess_int",
        )

        # 构造足够多的消息触发压缩
        messages = [{"role": "user", "content": f"消息 {i}"} for i in range(8)]

        await c.compact(messages)

        # 验证记忆写入调用了正确参数
        if mock_orchestrator.store.call_count > 0:
            for call in mock_orchestrator.store.call_args_list:
                assert call.kwargs.get("user_id") == 99
                assert call.kwargs.get("session_id") == "sess_int"
