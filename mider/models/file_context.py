"""Phase 1 → Phase 2: FileContext 스키마.

ContextCollectorAgent가 생성하여 각 AnalyzerAgent에 전달하는 파일 컨텍스트.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from mider.models.execution_plan import DependencyGraph


class ImportInfo(BaseModel):
    """import/include 구문 정보."""

    statement: str = Field(description="원본 구문 (e.g., #include <stdio.h>)")
    resolved_path: Optional[str] = Field(
        default=None, description="매칭된 파일 경로 (없으면 None)"
    )
    is_external: bool = Field(description="외부 라이브러리 여부")


class CallInfo(BaseModel):
    """함수 호출 정보."""

    function_name: str = Field(description="호출되는 함수명")
    line: int = Field(description="호출 위치 (라인 번호)")
    target_file: Optional[str] = Field(
        default=None, description="대상 파일 (매칭된 경우)"
    )


class PatternInfo(BaseModel):
    """코드 패턴 정보."""

    pattern_type: Literal[
        "error_handling",
        "logging",
        "transaction",
        "memory_management",
    ] = Field(description="패턴 유형")
    description: str = Field(description="패턴 설명")
    line: int = Field(description="패턴 위치 (라인 번호)")


class SingleFileContext(BaseModel):
    """단일 파일의 컨텍스트 정보."""

    file: str = Field(description="파일 경로")
    language: Literal["javascript", "c", "proc", "sql", "xml"] = Field(
        description="파일 언어"
    )
    imports: List[ImportInfo] = Field(default_factory=list)
    calls: List[CallInfo] = Field(default_factory=list)
    patterns: List[PatternInfo] = Field(default_factory=list)


class FileContext(BaseModel):
    """Phase 1의 출력: 파일 컨텍스트 모음."""

    file_contexts: List[SingleFileContext]
    dependencies: DependencyGraph = Field(
        description="ExecutionPlan에서 전달받은 의존성 그래프"
    )
    common_patterns: Dict[str, int] = Field(
        default_factory=dict, description="패턴 유형별 빈도수"
    )
