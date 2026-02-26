"""BaseTool: 모든 Tool의 기본 추상 클래스.

ToolResult, ToolExecutionError도 여기서 정의한다.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Tool 실행 결과."""

    success: bool = Field(description="실행 성공 여부")
    data: dict[str, Any] = Field(default_factory=dict, description="결과 데이터")
    error: Optional[str] = Field(default=None, description="에러 메시지")


class ToolExecutionError(Exception):
    """Tool 실행 실패 시 raise하는 예외."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"[{tool_name}] {message}")


class BaseTool(ABC):
    """모든 Tool의 기본 클래스.

    모든 Tool은 이 클래스를 상속하고 execute()를 구현해야 한다.
    """

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Tool 실행. 하위 클래스에서 구현해야 한다.

        Returns:
            ToolResult 인스턴스

        Raises:
            ToolExecutionError: 실행 실패 시
        """
