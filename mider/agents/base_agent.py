"""BaseAgent: 모든 Agent의 기본 추상 클래스.

LLM 호출, 재시도, fallback 로직을 포함한다.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from mider.config.llm_client import LLMClient

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """모든 Agent의 기본 클래스.

    Attributes:
        model: 기본 LLM 모델명
        fallback_model: 기본 모델 실패 시 사용할 모델
        temperature: LLM 샘플링 온도
        max_retries: LLM API 재시도 횟수
    """

    def __init__(
        self,
        model: str,
        fallback_model: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.max_retries = max_retries
        self._llm_client: Optional[LLMClient] = None

    @property
    def llm_client(self) -> LLMClient:
        """LLMClient 인스턴스를 lazy 초기화."""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client

    @abstractmethod
    async def run(self, **kwargs: Any) -> dict:
        """Agent 실행. 하위 클래스에서 구현해야 한다.

        Returns:
            Agent 실행 결과 딕셔너리
        """

    async def call_llm(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = True,
    ) -> str:
        """LLM API 호출 (재시도 + fallback 포함).

        Args:
            messages: OpenAI 형식 메시지 리스트
            json_mode: True이면 JSON Mode 응답 요청

        Returns:
            LLM 응답 텍스트

        Raises:
            Exception: 모든 재시도 및 fallback 실패 시
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = await self.llm_client.chat(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    json_mode=json_mode,
                )
                return response
            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM 호출 실패 (시도 {attempt + 1}/{self.max_retries}): {e}"
                )

                if attempt == self.max_retries - 1 and self.fallback_model:
                    logger.info(
                        f"Fallback 모델로 전환: {self.model} → {self.fallback_model}"
                    )
                    try:
                        response = await self.llm_client.chat(
                            model=self.fallback_model,
                            messages=messages,
                            temperature=self.temperature,
                            json_mode=json_mode,
                        )
                        return response
                    except Exception as fallback_error:
                        logger.error(f"Fallback 모델도 실패: {fallback_error}")
                        raise fallback_error from last_error

        raise last_error  # type: ignore[misc]
