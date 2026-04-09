"""LLMClient (AICA API) 단위 테스트."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mider.config.llm_client import AICAError, LLMClient, MODEL_CD_MAP


class TestLLMClientInit:
    def test_raises_without_env(self):
        """API 키가 없으면 EnvironmentError를 raise한다."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EnvironmentError, match="LLM API 키가 설정되지"):
                LLMClient()

    def test_raises_without_endpoint(self):
        """AICA_ENDPOINT가 없으면 EnvironmentError를 raise한다."""
        env = {"AICA_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(EnvironmentError, match="LLM API 키가 설정되지"):
                LLMClient()

    def test_creates_client(self):
        """AICA 환경 변수가 모두 있으면 클라이언트가 생성된다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._api_key == "test-key"
            assert client._base_url == "http://aica.test.com:3000"

    def test_sso_session_optional(self):
        """AICA_SSO_SESSION은 선택 사항이다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
            "AICA_SSO_SESSION": "sso-token",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._sso_session == "sso-token"

    def test_custom_user_id(self):
        """AICA_USER_ID를 커스텀 설정할 수 있다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
            "AICA_USER_ID": "custom_user",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._user_id == "custom_user"


class TestModelCdMapping:
    def test_known_models(self):
        """알려진 모델명이 올바른 model_cd로 매핑된다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            for model_name, expected_cd in MODEL_CD_MAP.items():
                assert client._resolve_model_cd(model_name) == expected_cd

    def test_unknown_model_defaults(self):
        """알 수 없는 모델명은 GPT5_2로 기본 매핑된다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._resolve_model_cd("unknown-model") == "GPT5_2"


class TestBuildMessage:
    def test_system_and_user(self):
        """system + user 메시지를 올바르게 결합한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
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
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            messages = [{"role": "user", "content": "Hello"}]
            result = client._build_message(messages)
            assert result == "Hello"


class TestLLMClientChat:
    @pytest.mark.asyncio
    async def test_chat_success(self):
        """정상 응답 시 token.data를 반환한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": '{"result": "ok"}'}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        result = await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )
        assert result == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_chat_sends_correct_payload(self):
        """올바른 페이로드를 AICA API에 전송한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": "response"}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "hello"}],
        )

        call_kwargs = client._client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["model_cd"] == "GPT5_2"
        assert payload["usecase_mode"] == "GENERAL"
        assert payload["stream"] is False
        assert "hello" in payload["message"]

    @pytest.mark.asyncio
    async def test_chat_with_sso_session(self):
        """SSO 세션이 설정되면 쿠키에 포함한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
            "AICA_SSO_SESSION": "sso-token-123",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": "ok"}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )

        call_kwargs = client._client.post.call_args
        cookies = call_kwargs.kwargs["cookies"]
        assert cookies["SSOSESSION"] == "sso-token-123"

    @pytest.mark.asyncio
    async def test_chat_aica_error(self):
        """AICA 에러 응답 시 AICAError를 raise한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={
                "error": {
                    "status_code": "50011",
                    "reason": "일일 사용 한도 초과",
                },
            },
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AICAError, match="50011"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_empty_response(self):
        """빈 응답이면 ValueError를 raise한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"token": {"data": ""}},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(ValueError, match="빈 응답"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_http_error(self):
        """HTTP 에러 시 httpx.HTTPStatusError를 raise한다."""
        env = {
            "AICA_API_KEY": "test-key",
            "AICA_ENDPOINT": "http://aica.test.com:3000",
        }
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )
