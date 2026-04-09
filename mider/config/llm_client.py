"""LLMClient: LLM API 래퍼 (OpenAI/Azure/AICA 스위칭).

환경 변수 API_PROVIDER로 백엔드를 선택한다:
- "openai" (기본): OpenAI 또는 Azure OpenAI SDK
- "aica": SKT AICA LLM Gateway (httpx)
"""

import logging
import os
from typing import Optional

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)

# settings.yaml 모델명 → AICA model_cd 매핑
MODEL_CD_MAP: dict[str, str] = {
    "gpt-5": "GPT5_2",
    "gpt-5-mini": "GPT5_2",
    "gpt-4.1": "GPT5_2",
    "gpt-4.1-mini": "GPT5_2",
    "gpt-4o": "GPT5_2",
    "gpt-4o-mini": "GPT5_2",
}


class AICAError(Exception):
    """AICA API 에러."""

    def __init__(self, status_code: str, reason: str) -> None:
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"AICA API 오류 [{status_code}]: {reason}")


class LLMClient:
    """LLM API 클라이언트 (OpenAI/Azure/AICA 자동 스위칭).

    환경 변수 API_PROVIDER로 백엔드를 선택한다:
    - "openai" (기본): AZURE_OPENAI_API_KEY 또는 OPENAI_API_KEY
    - "aica": AICA_API_KEY + AICA_ENDPOINT
    """

    def __init__(self) -> None:
        self._provider = os.environ.get("API_PROVIDER", "openai").lower()

        if self._provider == "aica":
            self._init_aica()
        else:
            self._init_openai()

    # ── OpenAI/Azure 초기화 ──

    def _init_openai(self) -> None:
        """OpenAI/Azure OpenAI 클라이언트를 초기화한다."""
        self._openai_client: AsyncOpenAI | AsyncAzureOpenAI = self._create_openai_client()

    def _create_openai_client(self) -> AsyncOpenAI | AsyncAzureOpenAI:
        """환경 변수에 따라 Azure 또는 OpenAI 클라이언트를 생성."""
        azure_key = os.environ.get("AZURE_OPENAI_API_KEY")
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")

        if azure_key and azure_endpoint:
            api_version = os.environ.get(
                "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
            )
            logger.info("Azure OpenAI 클라이언트 초기화")
            return AsyncAzureOpenAI(
                api_key=azure_key,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
            )

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            base_url = os.environ.get("OPENAI_BASE_URL")
            logger.info("OpenAI 클라이언트 초기화")
            return AsyncOpenAI(
                api_key=openai_key,
                base_url=base_url,
            )

        raise EnvironmentError(
            "LLM API 키가 설정되지 않았습니다. "
            "AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT 또는 "
            "OPENAI_API_KEY를 환경 변수로 설정하세요."
        )

    # ── AICA 초기화 ──

    def _init_aica(self) -> None:
        """AICA LLM Gateway 클라이언트를 초기화한다."""
        self._aica_api_key = os.environ.get("AICA_API_KEY", "")
        self._aica_endpoint = os.environ.get("AICA_ENDPOINT", "")
        self._aica_sso_session = os.environ.get("AICA_SSO_SESSION", "")
        self._aica_user_id = os.environ.get("AICA_USER_ID", "mider_agent")

        if not self._aica_api_key or not self._aica_endpoint:
            raise EnvironmentError(
                "LLM API 키가 설정되지 않았습니다. "
                "AICA_API_KEY와 AICA_ENDPOINT를 환경 변수로 설정하세요."
            )

        self._aica_base_url = self._aica_endpoint.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=180.0)
        logger.info("AICA LLM 클라이언트 초기화: %s", self._aica_base_url)

    async def aclose(self) -> None:
        """AICA httpx 클라이언트 연결을 정리한다."""
        if self._provider == "aica" and hasattr(self, "_http_client"):
            await self._http_client.aclose()

    # ── 공통 chat 인터페이스 ──

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        json_mode: bool = True,
        max_tokens: Optional[int] = None,
    ) -> str:
        """LLM Chat 호출 (provider에 따라 자동 분기).

        Args:
            model: 모델명 (settings.yaml 기준)
            messages: OpenAI 형식 메시지 리스트
            temperature: 샘플링 온도 (AICA에서는 미지원)
            json_mode: True이면 JSON Mode 응답 요청
            max_tokens: 최대 응답 토큰 수 (AICA에서는 미지원)

        Returns:
            LLM 응답 텍스트
        """
        if self._provider == "aica":
            return await self._chat_aica(model, messages, json_mode)
        return await self._chat_openai(model, messages, temperature, json_mode, max_tokens)

    # ── OpenAI/Azure 호출 ──

    async def _chat_openai(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        json_mode: bool,
        max_tokens: Optional[int],
    ) -> str:
        """OpenAI/Azure API로 호출."""
        kwargs: dict = {
            "model": model,
            "messages": messages,
        }

        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await self._openai_client.chat.completions.create(**kwargs)

        if not response.choices:
            raise ValueError("LLM이 빈 응답을 반환했습니다 (choices가 비어 있음)")

        content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else 0
        logger.debug(f"LLM 응답 수신: model={model}, tokens={tokens_used}")

        return content

    # ── AICA 호출 ──

    def _resolve_model_cd(self, model: str) -> str:
        """settings.yaml 모델명을 AICA model_cd로 변환."""
        model_cd = MODEL_CD_MAP.get(model, "GPT5_2")
        if model not in MODEL_CD_MAP:
            logger.warning("알 수 없는 모델 '%s' → 기본값 GPT5_2 사용", model)
        return model_cd

    def _build_message(self, messages: list[dict[str, str]]) -> str:
        """OpenAI 형식 messages를 단일 문자열로 변환."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"[SYSTEM]\n{content}")
            elif role == "user":
                parts.append(content)
            elif role == "assistant":
                parts.append(f"[ASSISTANT]\n{content}")
        return "\n\n".join(parts)

    async def _chat_aica(
        self,
        model: str,
        messages: list[dict[str, str]],
        json_mode: bool,
    ) -> str:
        """AICA Gateway API로 호출."""
        model_cd = self._resolve_model_cd(model)
        message = self._build_message(messages)

        if json_mode:
            message += "\n\n[IMPORTANT] Respond ONLY with valid JSON. No markdown, no explanation."

        url = f"{self._aica_base_url}/api/agent/v1/chats"
        headers: dict[str, str] = {
            "X-AGENT-API-KEY": self._aica_api_key,
            "Content-Type": "application/json",
        }

        cookies: dict[str, str] = {}
        if self._aica_sso_session:
            cookies["SSOSESSION"] = self._aica_sso_session

        payload = {
            "user_id": self._aica_user_id,
            "model_cd": model_cd,
            "message": message,
            "usecase_mode": "GENERAL",
            "stream": False,
            "context": "mider",
        }

        logger.debug("AICA 요청: model_cd=%s, message_len=%d", model_cd, len(message))

        response = await self._http_client.post(
            url, json=payload, headers=headers, cookies=cookies,
        )
        response.raise_for_status()

        data = response.json()

        error = data.get("error")
        if error:
            status_code = str(error.get("status_code", "unknown"))
            reason = error.get("reason", "알 수 없는 오류")
            raise AICAError(status_code, reason)

        token_data = data.get("token", {})
        content = token_data.get("data", "")

        if not content:
            raise ValueError("AICA가 빈 응답을 반환했습니다 (token.data가 비어 있음)")

        logger.debug("AICA 응답 수신: model_cd=%s, len=%d", model_cd, len(content))

        return content
