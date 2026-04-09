"""LLMClient: SKT AICA API 래퍼.

AICA 사내 LLM Gateway를 통해 GPT 모델을 호출한다.
환경 변수 AICA_API_KEY, AICA_ENDPOINT로 설정.
"""

import logging
import os
from typing import Optional

import httpx

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

# AICA 에러 코드
AICA_ERROR_QUOTA = "50011"
AICA_ERROR_PII = "50012"


class AICAError(Exception):
    """AICA API 에러."""

    def __init__(self, status_code: str, reason: str) -> None:
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"AICA API 오류 [{status_code}]: {reason}")


class LLMClient:
    """SKT AICA LLM API 클라이언트.

    환경 변수:
    - AICA_API_KEY: X-AGENT-API-KEY 헤더 값
    - AICA_ENDPOINT: API 서버 주소 (예: http://aica.sktelecom.com:3000)
    - AICA_SSO_SESSION: SSO 세션 쿠키 값 (선택, 별도 작업에서 설정)
    - AICA_USER_ID: 사용자 ID (기본: mider_agent)
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("AICA_API_KEY", "")
        self._endpoint = os.environ.get("AICA_ENDPOINT", "")
        self._sso_session = os.environ.get("AICA_SSO_SESSION", "")
        self._user_id = os.environ.get("AICA_USER_ID", "mider_agent")

        if not self._api_key or not self._endpoint:
            raise EnvironmentError(
                "LLM API 키가 설정되지 않았습니다. "
                "AICA_API_KEY와 AICA_ENDPOINT를 환경 변수로 설정하세요."
            )

        self._base_url = self._endpoint.rstrip("/")
        self._client = httpx.AsyncClient(timeout=180.0)
        logger.info("AICA LLM 클라이언트 초기화: %s", self._base_url)

    async def aclose(self) -> None:
        """httpx AsyncClient 연결을 정리한다."""
        await self._client.aclose()

    def _resolve_model_cd(self, model: str) -> str:
        """settings.yaml 모델명을 AICA model_cd로 변환."""
        model_cd = MODEL_CD_MAP.get(model, "GPT5_2")
        if model not in MODEL_CD_MAP:
            logger.warning(
                "알 수 없는 모델 '%s' → 기본값 GPT5_2 사용", model
            )
        return model_cd

    def _build_message(self, messages: list[dict[str, str]]) -> str:
        """OpenAI 형식 messages를 단일 문자열로 변환.

        AICA API는 단일 message 필드를 사용하므로,
        system + user 메시지를 결합한다.
        """
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

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        json_mode: bool = True,
        max_tokens: Optional[int] = None,
    ) -> str:
        """AICA LLM Chat 호출.

        Args:
            model: 모델명 (settings.yaml 기준, 자동으로 AICA model_cd로 변환)
            messages: OpenAI 형식 메시지 리스트
            temperature: 미사용 (AICA API 미지원, 인터페이스 호환용)
            json_mode: True이면 프롬프트에 JSON 응답 지시 추가
            max_tokens: 미사용 (AICA API 미지원, 인터페이스 호환용)

        Returns:
            LLM 응답 텍스트
        """
        model_cd = self._resolve_model_cd(model)
        message = self._build_message(messages)

        # json_mode: AICA API에는 response_format이 없으므로 프롬프트에 지시 추가
        if json_mode:
            message += "\n\n[IMPORTANT] Respond ONLY with valid JSON. No markdown, no explanation."

        url = f"{self._base_url}/api/agent/v1/chats"
        headers: dict[str, str] = {
            "X-AGENT-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

        cookies: dict[str, str] = {}
        if self._sso_session:
            cookies["SSOSESSION"] = self._sso_session

        payload = {
            "user_id": self._user_id,
            "model_cd": model_cd,
            "message": message,
            "usecase_mode": "GENERAL",
            "stream": False,
        }

        logger.debug(
            "AICA 요청: model_cd=%s, message_len=%d", model_cd, len(message)
        )

        response = await self._client.post(
            url, json=payload, headers=headers, cookies=cookies,
        )
        response.raise_for_status()

        data = response.json()

        # 에러 응답 처리
        error = data.get("error")
        if error:
            status_code = str(error.get("status_code", "unknown"))
            reason = error.get("reason", "알 수 없는 오류")
            raise AICAError(status_code, reason)

        # 응답 텍스트 추출
        token_data = data.get("token", {})
        content = token_data.get("data", "")

        if not content:
            raise ValueError("AICA가 빈 응답을 반환했습니다 (token.data가 비어 있음)")

        logger.debug("AICA 응답 수신: model_cd=%s, len=%d", model_cd, len(content))

        return content
