"""LLMClient 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mider.config.llm_client import LLMClient


class TestLLMClientInit:
    def test_raises_without_env(self):
        """API 키가 없으면 EnvironmentError를 raise한다."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EnvironmentError, match="LLM API 키가 설정되지"):
                LLMClient()

    def test_azure_client(self):
        """Azure 환경 변수 설정 시 AsyncAzureOpenAI를 사용한다."""
        env = {
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            from openai import AsyncAzureOpenAI
            assert isinstance(client._client, AsyncAzureOpenAI)

    def test_openai_client(self):
        """OpenAI 환경 변수 설정 시 AsyncOpenAI를 사용한다."""
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            from openai import AsyncOpenAI
            assert isinstance(client._client, AsyncOpenAI)

    def test_azure_takes_priority(self):
        """Azure와 OpenAI 키가 모두 있으면 Azure를 우선한다."""
        env = {
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_API_KEY": "openai-key",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            from openai import AsyncAzureOpenAI
            assert isinstance(client._client, AsyncAzureOpenAI)


class TestLLMClientChat:
    @pytest.mark.asyncio
    async def test_chat_json_mode(self):
        """JSON Mode로 chat을 호출한다."""
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100

        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
        )

        assert result == '{"result": "ok"}'
        client._client.chat.completions.create.assert_called_once_with(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

    @pytest.mark.asyncio
    async def test_chat_no_json_mode(self):
        """JSON Mode 없이 chat을 호출한다."""
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "plain text"
        mock_response.usage = None

        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            json_mode=False,
        )

        assert result == "plain text"
        call_kwargs = client._client.chat.completions.create.call_args[1]
        assert "response_format" not in call_kwargs

    @pytest.mark.asyncio
    async def test_chat_with_max_tokens(self):
        """max_tokens를 전달한다."""
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 50

        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=500,
        )

        call_kwargs = client._client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 500

    @pytest.mark.asyncio
    async def test_chat_empty_content(self):
        """응답 content가 None이면 빈 문자열을 반환한다."""
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.usage = None

        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
        )
        assert result == ""
