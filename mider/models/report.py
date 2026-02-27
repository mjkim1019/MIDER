"""Phase 3: Report 스키마 (IssueList, Checklist, Summary).

ReporterAgent가 생성. 각각 별도 JSON 파일로 출력.
"""

from datetime import datetime
from typing import Dict, List, Literal

from pydantic import BaseModel, Field

from mider.models.analysis_result import CodeFix, Location


class IssueListItem(BaseModel):
    """IssueList 내 개별 이슈 항목."""

    issue_id: str
    file: str
    language: str
    category: str
    severity: Literal["critical", "high", "medium", "low"]
    title: str
    description: str
    location: Location
    fix: CodeFix
    source: str


class IssueList(BaseModel):
    """output/issue-list.json: 전체 이슈 목록."""

    generated_at: datetime
    session_id: str
    total_issues: int
    by_severity: Dict[str, int] = Field(
        description='{"critical": 2, "high": 5, ...}'
    )
    issues: List[IssueListItem] = Field(
        description="severity 순 정렬 (critical → low)"
    )


class ChecklistItem(BaseModel):
    """Checklist 내 개별 체크 항목."""

    id: str = Field(description="CHECK-1, CHECK-2, ...")
    category: str = Field(description="issue category")
    severity: Literal["critical", "high"] = Field(
        description="critical, high만 포함"
    )
    description: str = Field(description="한국어 체크 항목")
    related_issues: List[str] = Field(description="연관 issue_id 목록")
    verification_command: str = Field(description="검증 명령어")
    expected_result: str = Field(description="기대 결과")


class Checklist(BaseModel):
    """output/checklist.json: 체크리스트."""

    generated_at: datetime
    session_id: str
    total_checks: int
    items: List[ChecklistItem]


class AnalysisMetadata(BaseModel):
    """분석 메타데이터."""

    session_id: str
    analyzed_at: datetime
    total_files: int
    total_lines: int
    analysis_duration_seconds: float
    total_llm_tokens: int


class IssueSummary(BaseModel):
    """이슈 통계 요약."""

    total: int
    by_severity: Dict[str, int] = Field(
        description='{"critical": 2, "high": 5, ...}'
    )
    by_category: Dict[str, int] = Field(
        description='{"memory_safety": 3, ...}'
    )
    by_language: Dict[str, int] = Field(
        description='{"c": 4, "javascript": 2, ...}'
    )
    by_file: Dict[str, int] = Field(
        description='{"/app/src/calc.c": 4, ...}'
    )


class RiskAssessment(BaseModel):
    """배포 위험도 평가."""

    deployment_risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        description="배포 위험 등급"
    )
    deployment_allowed: bool = Field(
        description="critical == 0 and high < 3이면 True"
    )
    blocking_issues: List[str] = Field(
        description="배포 차단 issue_id 목록 (critical + high)"
    )
    risk_description: str = Field(description="한국어 위험 설명")


class Summary(BaseModel):
    """output/summary.json: 분석 요약 리포트."""

    analysis_metadata: AnalysisMetadata
    issue_summary: IssueSummary
    risk_assessment: RiskAssessment
