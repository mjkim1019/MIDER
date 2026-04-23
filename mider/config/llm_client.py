"""LLMClient: LLM API 래퍼 (OpenAI/Azure/AICA 스위칭).

환경 변수 API_PROVIDER로 백엔드를 선택한다:
- "openai" (기본): OpenAI 또는 Azure OpenAI SDK
- "aica": SKT AICA LLM Gateway (httpx)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI

if TYPE_CHECKING:
    from mider.config.sso_auth import SSOAuthenticator

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

    def __init__(
        self,
        status_code: str,
        reason: str,
        detects: list[dict[str, str]] | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detects = detects or []
        detail = ""
        if self.detects:
            detail = " | 검출: " + ", ".join(
                f"{d.get('detect_type_name', '?')}({d.get('detect_str', '?')})"
                for d in self.detects
            )
        super().__init__(f"AICA API 오류 [{status_code}]: {reason}{detail}")


class AICASessionExpiredError(AICAError):
    """SSO 세션 만료 에러."""

    def __init__(self) -> None:
        super().__init__("SESSION_EXPIRED", "SSO 세션이 만료되었습니다")


def _mask_center(s: str) -> str:
    """문자열 가운데를 *로 치환한다.

    홀수 길이: 가운데 1문자를 * 로 치환
    짝수 길이: 가운데 2문자를 ** 로 치환

    예:
        "1030-2300"  (9자) → "1030*2300"
        "1000025847" (10자) → "1000**5847"
    """
    n = len(s)
    if n <= 1:
        return "*" * n
    if n % 2 == 1:
        mid = n // 2
        return s[:mid] + "*" + s[mid + 1:]
    else:
        mid = n // 2
        return s[:mid - 1] + "**" + s[mid + 1:]


def _mask_pii_in_messages(
    messages: list[dict[str, str]],
    detects: list[dict[str, str]],
) -> list[dict[str, str]]:
    """메시지 내 PII 검출 문자열을 가운데 마스킹 치환한 사본을 반환한다."""
    masked: list[dict[str, str]] = []
    for msg in messages:
        content = msg.get("content", "")
        for d in detects:
            detect_str = d.get("detect_str", "")
            if detect_str and detect_str in content:
                content = content.replace(detect_str, _mask_center(detect_str))
        masked.append({**msg, "content": content})
    return masked


class LLMClient:
    """LLM API 클라이언트 (OpenAI/Azure/AICA 자동 스위칭).

    환경 변수 API_PROVIDER로 백엔드를 선택한다:
    - "openai" (기본): AZURE_OPENAI_API_KEY 또는 OPENAI_API_KEY
    - "aica": AICA_API_KEY + AICA_ENDPOINT
    """

    def __init__(self, sso_authenticator: SSOAuthenticator | None = None) -> None:
        self._provider = os.environ.get("API_PROVIDER", "openai").lower()
        self._sso_authenticator = sso_authenticator

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

        if not self._aica_endpoint:
            raise EnvironmentError(
                "AICA_ENDPOINT가 설정되지 않았습니다. "
                "AICA_ENDPOINT를 환경 변수로 설정하세요."
            )

        self._aica_base_url = self._aica_endpoint.rstrip("/")
        self._http_client = httpx.AsyncClient(
            timeout=180.0,
            verify=False,
            follow_redirects=True,
        )

        # SSO 세션: SSOAuthenticator > 환경변수 순으로 결정
        if self._sso_authenticator:
            creds = self._sso_authenticator.authenticate()
            self._aica_sso_session = creds.sso_session
            self._aica_user_id = creds.user_id
            logger.info(
                "AICA 클라이언트 초기화 (SSO user_id=%s): %s",
                self._aica_user_id, self._aica_base_url,
            )
        else:
            self._aica_sso_session = os.environ.get("AICA_SSO_SESSION", "")
            self._aica_user_id = os.environ.get("AICA_USER_ID", "mider_agent")
            # 세션 만료 시 자체 치유가 가능하도록 내부 authenticator를 생성한다.
            # 이 authenticator는 서버에서 SESSION_EXPIRED가 오면 _chat_aica 루프에서
            # invalidate + force_login=True 로 브라우저 재로그인을 트리거한다.
            try:
                from mider.config.sso_auth import SSOAuthenticator
                self._sso_authenticator = SSOAuthenticator(
                    base_url=self._aica_endpoint,
                )
            except Exception as e:
                logger.debug("내부 SSOAuthenticator 생성 실패: %s", e)
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
        max_tokens: int | None = None,
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
        max_tokens: int | None,
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

        from mider.config.debug_logger import is_enabled as _dbg_on, log_llm_request, log_llm_response
        if _dbg_on():
            log_llm_request(model, messages)

        _t0 = __import__("time").time()
        response = await self._openai_client.chat.completions.create(**kwargs)

        if not response.choices:
            raise ValueError("LLM이 빈 응답을 반환했습니다 (choices가 비어 있음)")

        content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else 0
        logger.debug(f"LLM 응답 수신: model={model}, tokens={tokens_used}")

        if _dbg_on():
            log_llm_response(content, (__import__("time").time() - _t0) * 1000)

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

    def _is_sso_expired_response(self, response: httpx.Response) -> bool:
        """AICA 응답이 SSO 만료(리다이렉트 HTML)인지 판단한다."""
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return False
        if "text/html" in content_type:
            return True
        text = response.text.strip()
        if text.startswith("<"):
            return True
        return False

    async def _chat_aica(
        self,
        model: str,
        messages: list[dict[str, str]],
        json_mode: bool,
    ) -> str:
        """AICA Gateway API로 호출 (SSO 재인증 + PII 마스킹 재시도)."""
        max_pii_retries = 2
        current_messages = messages

        for pii_attempt in range(max_pii_retries + 1):
            try:
                return await self._chat_aica_once(model, current_messages, json_mode)
            except AICASessionExpiredError:
                if not self._sso_authenticator:
                    raise
                # 자동 재인증 후 1회 재시도
                logger.info("SSO 세션 만료 — 자동 재인증 시도")
                self._sso_authenticator.invalidate_session()
                creds = self._sso_authenticator.authenticate(force_login=True)
                self._aica_sso_session = creds.sso_session
                self._aica_user_id = creds.user_id
                # 다른 LLMClient 인스턴스와 외부 핸들러가 최신 세션을 사용할 수 있도록
                # 환경변수도 함께 갱신한다.
                os.environ["AICA_SSO_SESSION"] = creds.sso_session
                os.environ["AICA_USER_ID"] = creds.user_id
                logger.info("SSO 재인증 완료: user_id=%s", self._aica_user_id)
                return await self._chat_aica_once(model, current_messages, json_mode)
            except AICAError as e:
                if not e.detects or pii_attempt >= max_pii_retries:
                    raise
                # PII 검출 → 검출 문자열 가운데 마스킹 후 재시도
                # 원문 → 마스킹 결과를 나란히 보여 어떻게 치환됐는지 확인 가능하게 한다.
                mask_summary = ", ".join(
                    f"{d.get('detect_type_name', '?')}("
                    f"{d.get('detect_str', '?')} → {_mask_center(d.get('detect_str', ''))})"
                    for d in e.detects
                )
                logger.info(
                    "PII 검출 → 마스킹 후 재시도 (%d/%d): %s",
                    pii_attempt + 1, max_pii_retries, mask_summary,
                )
                current_messages = _mask_pii_in_messages(current_messages, e.detects)

        raise AICAError("PII_RETRY_EXHAUSTED", "PII 마스킹 재시도 소진")

    async def _chat_aica_once(
        self,
        model: str,
        messages: list[dict[str, str]],
        json_mode: bool,
    ) -> str:
        """AICA Gateway API 단일 호출."""
        model_cd = self._resolve_model_cd(model)
        message = self._build_message(messages)

        if json_mode:
            message += "\n\n[IMPORTANT] Respond ONLY with valid JSON. No markdown, no explanation."

        url = f"{self._aica_base_url}/api/agent/v1/chats"
        headers: dict[str, str] = {
            "X-AGENT-API-KEY": self._aica_api_key,
            "Content-Type": "application/json",
        }

        # 외부에서 재로그인(_run_sso_login 등)으로 환경변수가 갱신될 수 있으므로
        # 매 호출마다 env var의 최신 값을 우선 사용한다. env var이 비어 있을 때만
        # 초기화 시점에 저장했던 값으로 폴백한다.
        current_session = os.environ.get("AICA_SSO_SESSION") or self._aica_sso_session
        current_user_id = os.environ.get("AICA_USER_ID") or self._aica_user_id

        cookies: dict[str, str] = {}
        if current_session:
            cookies["SSOSESSION"] = current_session

        payload = {
            "user_id": current_user_id,
            "model_cd": model_cd,
            "message": message,
            "usecase_mode": "GENERAL",
            "stream": False,
            "context": "mider",
            "app_env": "prd",
        }

        logger.debug("AICA 요청: model_cd=%s, message_len=%d", model_cd, len(message))

        from mider.config.debug_logger import is_enabled as _dbg_on, log_llm_request, log_llm_response
        if _dbg_on():
            log_llm_request(f"AICA/{model_cd}", messages)

        _t0 = __import__("time").time()
        response = await self._http_client.post(
            url, json=payload, headers=headers, cookies=cookies,
        )

        # SSO 만료 감지: HTML 응답이면 세션 만료
        if self._is_sso_expired_response(response):
            raise AICASessionExpiredError()

        response.raise_for_status()

        # AICA는 NDJSON(줄 단위 JSON)으로 응답 — 줄별로 파싱
        result = self._parse_aica_ndjson(response.text)

        if _dbg_on():
            log_llm_response(result, (__import__("time").time() - _t0) * 1000)

        return result

    def _parse_aica_ndjson(self, raw_text: str) -> str:
        """AICA NDJSON 응답을 파싱하여 LLM 응답 텍스트를 추출한다."""
        import json as _json

        from mider.config.debug_logger import is_enabled as _dbg_on, log_info
        if _dbg_on():
            log_info("AICA_RAW", f"HTTP Response Body ({len(raw_text)} chars):\n{raw_text}")

        full_content = ""

        for line in raw_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                data = _json.loads(line)
            except _json.JSONDecodeError:
                logger.warning("AICA 응답 JSON 파싱 실패: %s", line[:200])
                continue

            # 에러 응답 처리
            msg_type = data.get("type")
            if msg_type == "error":
                status_code = str(data.get("status_code", "unknown"))
                reason = data.get("reason", "알 수 없는 오류")
                detects = data.get("detects")
                if detects:
                    logger.warning(
                        "AICA PII 검출: %s",
                        ", ".join(
                            f"{d.get('detect_type_name', '?')}({d.get('detect_str', '?')})"
                            for d in detects
                        ),
                    )
                raise AICAError(status_code, reason, detects)

            # OpenAI 호환 형식: choices[].message.content
            if "choices" in data:
                for choice in data["choices"]:
                    content = choice.get("message", {}).get("content", "")
                    if content:
                        full_content += content

                # choices 응답에 error_code + detects가 함께 온 경우 AICAError raise
                detects = data.get("detects")
                error_code = data.get("error_code")
                if error_code and detects:
                    reason = full_content.strip()
                    logger.warning(
                        "AICA PII 검출 (choices): %s",
                        ", ".join(
                            f"{d.get('detect_type_name', '?')}({d.get('detect_str', '?')})"
                            for d in detects
                        ),
                    )
                    raise AICAError(str(error_code), reason, detects)

            # SSE 형식: type=token
            elif msg_type == "token":
                full_content += data.get("data", "")

        if not full_content:
            logger.warning("AICA 빈 응답 (raw 처음 500자): %s", raw_text[:500])
            raise ValueError("AICA가 빈 응답을 반환했습니다 (content가 비어 있음)")

        # choices content로 에러 메시지가 온 경우 감지
        stripped = full_content.strip()
        if stripped.startswith("[Error:") or stripped.startswith("고객 정보"):
            logger.warning("AICA PII/콘텐츠 필터 차단 (content): %s", stripped[:300])

        # LLM이 마크다운 코드블록으로 감싼 경우 제거
        full_content = self._strip_markdown_json(full_content)

        logger.debug("AICA 응답 수신: len=%d", len(full_content))
        return full_content

    @staticmethod
    def _strip_markdown_json(text: str) -> str:
        """마크다운 코드블록(```json ... ```)을 제거한다.

        LLM이 JSON 응답을 마크다운으로 감싸는 경우가 빈번하므로
        이를 자동으로 제거하여 json.loads()가 성공하도록 한다.
        """
        import re

        stripped = text.strip()
        # ```json ... ``` 또는 ``` ... ``` 패턴
        match = re.match(
            r"^```(?:json)?\s*\n?(.*?)\n?\s*```\s*$",
            stripped,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()
        return stripped
