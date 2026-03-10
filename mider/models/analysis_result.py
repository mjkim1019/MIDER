"""Phase 2: AnalysisResult 스키마.

각 AnalyzerAgent(JS/C/ProC/SQL)가 생성하여 OrchestratorAgent에 반환하는 분석 결과.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Location(BaseModel):
    """코드 위치 정보."""

    file: str = Field(description="파일 경로")
    line_start: int = Field(description="시작 라인")
    line_end: int = Field(description="종료 라인")
    column_start: Optional[int] = Field(default=None, description="시작 컬럼")
    column_end: Optional[int] = Field(default=None, description="종료 컬럼")


class CodeFix(BaseModel):
    """수정 제안 코드."""

    before: str = Field(description="수정 전 코드 (1-3줄)")
    after: str = Field(description="수정 후 코드 (1-3줄)")
    description: str = Field(description="한국어 수정 설명")


class Issue(BaseModel):
    """발견된 이슈."""

    issue_id: str = Field(description="JS-001, C-001, PC-001, SQL-001")
    category: Literal[
        "memory_safety",
        "null_safety",
        "data_integrity",
        "error_handling",
        "security",
        "performance",
        "code_quality",
    ] = Field(description="이슈 카테고리")
    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="심각도"
    )
    title: str = Field(description="이슈 제목 (한국어)")
    description: str = Field(description="상세 설명 (한국어)")
    location: Location
    fix: CodeFix
    source: Literal["static_analysis", "llm", "hybrid"] = Field(
        description="탐지 출처"
    )
    static_tool: Optional[str] = Field(
        default=None, description="정적 분석 도구명 (eslint, clang-tidy, proc)"
    )
    static_rule: Optional[str] = Field(
        default=None, description="정적 분석 규칙명"
    )


class AnalysisResult(BaseModel):
    """Phase 2의 출력: 파일별 분석 결과."""

    task_id: str = Field(description="ExecutionPlan의 task_id와 매칭")
    file: str = Field(description="분석된 파일 경로")
    language: Literal["javascript", "c", "proc", "sql", "xml"] = Field(
        description="파일 언어"
    )
    agent: str = Field(description="분석 Agent명 (e.g., JavaScriptAnalyzerAgent)")
    issues: List[Issue] = Field(default_factory=list)
    analysis_time_seconds: float = Field(description="분석 소요 시간 (초)")
    llm_tokens_used: int = Field(description="LLM 토큰 사용량")
    error: Optional[str] = Field(
        default=None, description="Agent 내부 에러 발생 시"
    )
