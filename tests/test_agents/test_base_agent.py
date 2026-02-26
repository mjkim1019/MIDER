"""BaseAgent 단위 테스트."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mider.agents.base_agent import BaseAgent


class DummyAgent(BaseAgent):
    """테스트용 BaseAgent 구현."""

    async def run(self, **kwargs: Any) -> dict:
        return {"result": "ok"}


class TestBaseAgentInit:
    def test_defaults(self):
        agent = DummyAgent(model="gpt-4o")
        assert agent.model == "gpt-4o"
        assert agent.fallback_model is None
        assert agent.temperature == 0.0
        assert agent.max_retries == 3

    def test_custom_params(self):
        agent = DummyAgent(
            model="gpt-4o",
            fallback_model="gpt-4o-mini",
            temperature=0.3,
            max_retries=5,
        )
        assert agent.fallback_model == "gpt-4o-mini"
        assert agent.temperature == 0.3
        assert agent.max_retries == 5


class TestBaseAgentRun:
    @pytest.mark.asyncio
    async def test_run(self):
        agent = DummyAgent(model="gpt-4o")
        result = await agent.run()
        assert result == {"result": "ok"}


class TestCallLLM:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = DummyAgent(model="gpt-4o")
        mock_client = AsyncMock()
        mock_client.chat.return_value = '{"answer": "ok"}'
        agent._llm_client = mock_client

        result = await agent.call_llm(
            messages=[{"role": "user", "content": "test"}]
        )
        assert result == '{"answer": "ok"}'
        mock_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        agent = DummyAgent(model="gpt-4o", max_retries=3)
        mock_client = AsyncMock()
        mock_client.chat.side_effect = [
            Exception("error1"),
            Exception("error2"),
            '{"answer": "ok"}',
        ]
        agent._llm_client = mock_client

        result = await agent.call_llm(
            messages=[{"role": "user", "content": "test"}]
        )
        assert result == '{"answer": "ok"}'
        assert mock_client.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_fallback_on_all_retries_fail(self):
        agent = DummyAgent(
            model="gpt-4o",
            fallback_model="gpt-4o-mini",
            max_retries=2,
        )
        mock_client = AsyncMock()
        mock_client.chat.side_effect = [
            Exception("fail1"),
            Exception("fail2"),
            '{"fallback": "ok"}',
        ]
        agent._llm_client = mock_client

        result = await agent.call_llm(
            messages=[{"role": "user", "content": "test"}]
        )
        assert result == '{"fallback": "ok"}'
        # 2번 기본 모델 시도 + 1번 fallback = 3번 호출
        assert mock_client.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_when_no_fallback(self):
        agent = DummyAgent(model="gpt-4o", max_retries=2)
        mock_client = AsyncMock()
        mock_client.chat.side_effect = Exception("always fails")
        agent._llm_client = mock_client

        with pytest.raises(Exception, match="always fails"):
            await agent.call_llm(
                messages=[{"role": "user", "content": "test"}]
            )

    @pytest.mark.asyncio
    async def test_raises_when_fallback_also_fails(self):
        agent = DummyAgent(
            model="gpt-4o",
            fallback_model="gpt-4o-mini",
            max_retries=1,
        )
        mock_client = AsyncMock()
        mock_client.chat.side_effect = Exception("all fail")
        agent._llm_client = mock_client

        with pytest.raises(Exception, match="all fail"):
            await agent.call_llm(
                messages=[{"role": "user", "content": "test"}]
            )

    @pytest.mark.asyncio
    async def test_json_mode_passed(self):
        agent = DummyAgent(model="gpt-4o")
        mock_client = AsyncMock()
        mock_client.chat.return_value = "{}"
        agent._llm_client = mock_client

        await agent.call_llm(
            messages=[{"role": "user", "content": "test"}],
            json_mode=False,
        )
        mock_client.chat.assert_called_once_with(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            json_mode=False,
        )
