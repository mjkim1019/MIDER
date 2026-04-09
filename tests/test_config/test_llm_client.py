"""LLMClient 단위 테스트 (OpenAI + AICA 스위칭)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mider.config.llm_client import AICAError, LLMClient, MODEL_CD_MAP

# AICA 테스트용 기본 환경 변수
AICA_ENV = {
    "API_PROVIDER": "aica",
    "AICA_API_KEY": "test-key",
    "AICA_ENDPOINT": "http://aica.test.com:3000",
}

OPENAI_ENV = {
    "API_PROVIDER": "openai",
    "OPENAI_API_KEY": "test-key",
}

AZURE_ENV = {
    "API_PROVIDER": "openai",
    "AZURE_OPENAI_API_KEY": "azure-key",
    "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/",
}


# ── 초기화 테스트 ──


class TestLLMClientInit:
    def test_raises_without_env(self):
        """API 키가 없으면 EnvironmentError를 raise한다."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EnvironmentError, match="LLM API 키가 설정되지"):
                LLMClient()

    def test_openai_client(self):
        """OpenAI provider로 초기화한다."""
        with patch.dict("os.environ", OPENAI_ENV, clear=True):
            client = LLMClient()
            assert client._provider == "openai"
            from openai import AsyncOpenAI
            assert isinstance(client._openai_client, AsyncOpenAI)

    def test_azure_client(self):
        """Azure provider로 초기화한다."""
        with patch.dict("os.environ", AZURE_ENV, clear=True):
            client = LLMClient()
            from openai import AsyncAzureOpenAI
            assert isinstance(client._openai_client, AsyncAzureOpenAI)

    def test_aica_client(self):
        """AICA provider로 초기화한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            assert client._provider == "aica"
            assert client._aica_base_url == "http://aica.test.com:3000"

    def test_aica_raises_without_endpoint(self):
        """AICA에서 ENDPOINT 없으면 EnvironmentError."""
        env = {"API_PROVIDER": "aica", "AICA_API_KEY": "key"}
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(EnvironmentError):
                LLMClient()

    def test_default_provider_is_openai(self):
        """API_PROVIDER 미설정 시 기본값은 openai."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "key"}, clear=True):
            client = LLMClient()
            assert client._provider == "openai"

    def test_aica_sso_session_optional(self):
        """AICA_SSO_SESSION은 선택 사항이다."""
        env = {**AICA_ENV, "AICA_SSO_SESSION": "sso-token"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._aica_sso_session == "sso-token"


# ── OpenAI Chat 테스트 ──


class TestOpenAIChat:
    @pytest.mark.asyncio
    async def test_chat_json_mode(self):
        """OpenAI: JSON Mode로 chat을 호출한다."""
        with patch.dict("os.environ", OPENAI_ENV, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100

        client._openai_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
        )
        assert result == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_chat_no_json_mode(self):
        """OpenAI: JSON Mode 없이 호출한다."""
        with patch.dict("os.environ", OPENAI_ENV, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "plain text"
        mock_response.usage = None

        client._openai_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            json_mode=False,
        )
        assert result == "plain text"
        call_kwargs = client._openai_client.chat.completions.create.call_args[1]
        assert "response_format" not in call_kwargs

    @pytest.mark.asyncio
    async def test_chat_empty_choices(self):
        """OpenAI: 빈 choices이면 ValueError를 raise한다."""
        with patch.dict("os.environ", OPENAI_ENV, clear=True):
            client = LLMClient()

        mock_response = MagicMock()
        mock_response.choices = []

        client._openai_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(ValueError, match="빈 응답"):
            await client.chat(
                model="gpt-4o",
                messages=[{"role": "user", "content": "test"}],
            )


# ── AICA Chat 테스트 ──


class TestAICAChat:
    @pytest.mark.asyncio
    async def test_chat_success(self):
        """AICA: 정상 응답 시 token.data를 반환한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": '{"result": "ok"}'}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        result = await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )
        assert result == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_chat_sends_correct_payload(self):
        """AICA: 올바른 페이로드를 전송한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": "response"}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "hello"}],
        )

        payload = client._http_client.post.call_args.kwargs["json"]
        assert payload["model_cd"] == "GPT5_2"
        assert payload["usecase_mode"] == "GENERAL"
        assert payload["stream"] is False
        assert payload["context"] == "mider"

    @pytest.mark.asyncio
    async def test_chat_aica_error(self):
        """AICA: 에러 응답 시 AICAError를 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"error": {"status_code": "50011", "reason": "한도 초과"}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AICAError, match="50011"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_empty_response(self):
        """AICA: 빈 응답이면 ValueError를 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": ""}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(ValueError, match="빈 응답"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )


# ── Model CD 매핑 테스트 ──


class TestModelCdMapping:
    def test_known_models(self):
        """알려진 모델명이 올바른 model_cd로 매핑된다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            for model_name, expected_cd in MODEL_CD_MAP.items():
                assert client._resolve_model_cd(model_name) == expected_cd

    def test_unknown_model_defaults(self):
        """알 수 없는 모델명은 GPT5_2로 기본 매핑된다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            assert client._resolve_model_cd("unknown-model") == "GPT5_2"


# ── Build Message 테스트 ──


class TestBuildMessage:
    def test_system_and_user(self):
        """system + user 메시지를 결합한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            messages = [
                {"role": "system", "content": "You are a code analyzer."},
                {"role": "user", "content": "Analyze this code."},
            ]
            result = client._build_message(messages)
            assert "[SYSTEM]\nYou are a code analyzer." in result
            assert "Analyze this code." in result

    def test_user_only(self):
        """user 메시지만 있을 때도 동작한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            result = client._build_message([{"role": "user", "content": "Hello"}])
            assert result == "Hello"
