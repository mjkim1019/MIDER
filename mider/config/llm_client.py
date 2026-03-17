"""LLMClient: OpenAI/Azure OpenAI API 래퍼.

JSON Mode 지원, 환경 변수 기반 설정.
"""

import logging
import os
from typing import Optional

from openai import AsyncAzureOpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """OpenAI/Azure OpenAI API 클라이언트 래퍼.

    환경 변수에 따라 Azure 또는 OpenAI API를 자동으로 선택한다.
    - Azure: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT
    - OpenAI: OPENAI_API_KEY
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | AsyncAzureOpenAI = self._create_client()

    def _create_client(self) -> AsyncOpenAI | AsyncAzureOpenAI:
        """환경 변수에 따라 적절한 클라이언트를 생성."""
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

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        json_mode: bool = True,
        max_tokens: Optional[int] = None,
    ) -> str:
        """LLM Chat Completion 호출.

        Args:
            model: 모델명 (또는 Azure 배포명)
            messages: OpenAI 형식 메시지 리스트
            temperature: 샘플링 온도 (0.0 ~ 1.0)
            json_mode: True이면 JSON Mode 응답 요청
            max_tokens: 최대 응답 토큰 수 (None이면 제한 없음)

        Returns:
            LLM 응답 텍스트
        """
        kwargs: dict = {
            "model": model,
            "messages": messages,
        }

        # gpt-5 계열은 temperature 기본값(1)만 지원
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await self._client.chat.completions.create(**kwargs)

        if not response.choices:
            raise ValueError("LLM이 빈 응답을 반환했습니다 (choices가 비어 있음)")

        content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else 0
        logger.debug(f"LLM 응답 수신: model={model}, tokens={tokens_used}")

        return content
