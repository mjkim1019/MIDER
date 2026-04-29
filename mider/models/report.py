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


class FileRiskAssessment(BaseModel):
    """파일별 배포 위험도 평가 (RiskAssessment.by_file 항목)."""

    file: str = Field(description="파일 경로")
    deployment_risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNABLE_TO_ANALYZE"] = Field(
        description="이 파일 단독 기준 배포 위험 등급"
    )
    deployment_allowed: bool = Field(
        description="이 파일 기준 배포 가능 여부 (critical==0이면 True)"
    )
    critical_count: int = Field(default=0, description="CRITICAL 이슈 수")
    high_count: int = Field(default=0, description="HIGH 이슈 수")
    medium_count: int = Field(default=0, description="MEDIUM 이슈 수")
    blocking_issues: List[str] = Field(
        default_factory=list,
        description="이 파일에서 배포 차단을 유발한 issue_id 목록",
    )


class RiskAssessment(BaseModel):
    """배포 위험도 평가 (전체 + 파일별)."""

    deployment_risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNABLE_TO_ANALYZE"] = Field(
        description="전체(모든 파일 통합) 배포 위험 등급"
    )
    deployment_allowed: bool = Field(
        description="전체 기준 배포 가능 여부"
    )
    blocking_issues: List[str] = Field(
        description="전체 기준 배포 차단 issue_id 목록"
    )
    risk_description: str = Field(description="한국어 위험 설명")
    by_file: List[FileRiskAssessment] = Field(
        default_factory=list,
        description="파일별 개별 배포 판정 (다중 파일 분석 시)",
    )


class DeploymentChecklistItem(BaseModel):
    """배포 체크리스트 개별 항목."""

    id: str = Field(description="항목 ID (SCR-01, TP-01 등)")
    item: str = Field(description="체크 항목 설명")
    checked: bool = Field(default=False, description="확인 여부")


class DeploymentChecklistSection(BaseModel):
    """배포 체크리스트 섹션."""

    section_id: str = Field(description="섹션 ID (screen, tp, module, batch, dbio)")
    title: str = Field(description="섹션 제목")
    files: List[str] = Field(description="해당 섹션의 파일 목록")
    items: List[DeploymentChecklistItem] = Field(description="체크 항목 목록")


class DeploymentChecklist(BaseModel):
    """output/deployment-checklist.json: 배포 체크리스트."""

    generated_at: datetime
    session_id: str
    total_items: int
    sections: List[DeploymentChecklistSection]


class Summary(BaseModel):
    """output/summary.json: 분석 요약 리포트."""

    analysis_metadata: AnalysisMetadata
    issue_summary: IssueSummary
    risk_assessment: RiskAssessment
