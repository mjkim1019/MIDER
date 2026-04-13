"""LLMClient 단위 테스트 (OpenAI + AICA 스위칭)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mider.config.llm_client import (
    AICAError,
    AICASessionExpiredError,
    LLMClient,
    MODEL_CD_MAP,
)

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


def _aica_success_response(content: str = '{"result": "ok"}') -> httpx.Response:
    """AICA 정상 응답 (OpenAI 호환 형식)."""
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "GPT5_2",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
        request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
    )


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

    def test_aica_sso_session_from_env(self):
        """AICA_SSO_SESSION 환경변수로 세션을 설정한다."""
        env = {**AICA_ENV, "AICA_SSO_SESSION": "sso-token"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()
            assert client._aica_sso_session == "sso-token"

    def test_aica_with_sso_authenticator(self):
        """SSOAuthenticator를 전달하면 세션/user_id를 자동 획득한다."""
        mock_auth = MagicMock()
        mock_auth.authenticate.return_value = MagicMock(
            sso_session="sso-from-auth",
            user_id="auth_user",
        )
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient(sso_authenticator=mock_auth)
            assert client._aica_sso_session == "sso-from-auth"
            assert client._aica_user_id == "auth_user"

    def test_aica_default_user_id(self):
        """AICA_USER_ID 미설정 시 기본값은 mider_agent."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()
            assert client._aica_user_id == "mider_agent"


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
        """AICA: choices[0].message.content를 반환한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        client._http_client.post = AsyncMock(
            return_value=_aica_success_response('{"result": "ok"}')
        )

        result = await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )
        assert result == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_chat_sends_correct_payload(self):
        """AICA: 올바른 페이로드(user_id, app_env 포함)를 전송한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        client._http_client.post = AsyncMock(
            return_value=_aica_success_response("response")
        )

        await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "hello"}],
        )

        payload = client._http_client.post.call_args.kwargs["json"]
        assert payload["model_cd"] == "GPT5_2"
        assert payload["usecase_mode"] == "GENERAL"
        assert payload["stream"] is False
        assert payload["context"] == "mider"
        assert payload["app_env"] == "prd"
        assert payload["user_id"] == "mider_agent"

    @pytest.mark.asyncio
    async def test_chat_sends_sso_cookie(self):
        """AICA: SSO 세션이 있으면 쿠키로 전송한다."""
        env = {**AICA_ENV, "AICA_SSO_SESSION": "sso-test-token"}
        with patch.dict("os.environ", env, clear=True):
            client = LLMClient()

        client._http_client.post = AsyncMock(
            return_value=_aica_success_response("ok")
        )

        await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )

        cookies = client._http_client.post.call_args.kwargs["cookies"]
        assert cookies["SSOSESSION"] == "sso-test-token"

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
    async def test_chat_empty_choices(self):
        """AICA: 빈 choices이면 ValueError를 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"choices": []},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(ValueError, match="빈 응답"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_empty_content(self):
        """AICA: content가 비어 있으면 ValueError를 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(ValueError, match="빈 응답"):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )


# ── SSO 만료 감지 + 자동 재인증 테스트 ──


class TestSSOExpiry:
    @pytest.mark.asyncio
    async def test_html_response_raises_session_expired(self):
        """AICA: HTML 응답이면 AICASessionExpiredError를 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            text="<html><body>SSO redirect</body></html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AICASessionExpiredError):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_auto_reauth_on_session_expired(self):
        """AICA: SSO 만료 시 자동 재인증 후 재시도한다."""
        mock_auth = MagicMock()
        mock_auth.authenticate.return_value = MagicMock(
            sso_session="new-sso",
            user_id="reauth_user",
        )

        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient(sso_authenticator=mock_auth)

        # 1차: HTML (만료) → 2차: 정상 응답
        expired_response = httpx.Response(
            200,
            text="<html>SSO redirect</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(
            side_effect=[expired_response, _aica_success_response("재시도 성공")]
        )

        result = await client.chat(
            model="gpt-5",
            messages=[{"role": "user", "content": "test"}],
        )

        assert result == "재시도 성공"
        assert client._aica_sso_session == "new-sso"
        assert client._aica_user_id == "reauth_user"
        mock_auth.invalidate_session.assert_called_once()
        # authenticate: 1회(init) + 1회(reauth) = 2회
        assert mock_auth.authenticate.call_count == 2

    @pytest.mark.asyncio
    async def test_no_reauth_without_authenticator(self):
        """AICA: SSOAuthenticator 없으면 만료 시 에러를 그대로 raise한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        mock_response = httpx.Response(
            200,
            text="<html>SSO redirect</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("POST", "http://test/api/agent/v1/chats"),
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AICASessionExpiredError):
            await client.chat(
                model="gpt-5",
                messages=[{"role": "user", "content": "test"}],
            )

    def test_is_sso_expired_html_content_type(self):
        """text/html Content-Type이면 만료로 판단한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        resp = httpx.Response(
            200,
            text="<html>redirect</html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=httpx.Request("POST", "http://test"),
        )
        assert client._is_sso_expired_response(resp) is True

    def test_is_sso_expired_html_body(self):
        """Content-Type이 없어도 <로 시작하면 만료로 판단한다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        resp = httpx.Response(
            200,
            text="<html>redirect</html>",
            request=httpx.Request("POST", "http://test"),
        )
        assert client._is_sso_expired_response(resp) is True

    def test_is_sso_expired_json_response(self):
        """정상 JSON 응답은 만료가 아니다."""
        with patch.dict("os.environ", AICA_ENV, clear=True):
            client = LLMClient()

        resp = httpx.Response(
            200,
            text='{"choices": []}',
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "http://test"),
        )
        assert client._is_sso_expired_response(resp) is False


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
