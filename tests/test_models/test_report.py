"""Report 스키마 단위 테스트 (IssueList, Checklist, Summary)."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from mider.models.analysis_result import CodeFix, Location
from mider.models.report import (
    AnalysisMetadata,
    Checklist,
    ChecklistItem,
    IssueList,
    IssueListItem,
    IssueSummary,
    RiskAssessment,
    Summary,
)


class TestIssueListItem:
    def test_valid(self):
        item = IssueListItem(
            issue_id="C-001",
            file="/app/calc.c",
            language="c",
            category="memory_safety",
            severity="critical",
            title="strcpy 버퍼 오버플로우",
            description="위험한 strcpy 사용",
            location=Location(
                file="/app/calc.c", line_start=234, line_end=234
            ),
            fix=CodeFix(
                before="strcpy(dest, src);",
                after="strncpy(dest, src, sizeof(dest) - 1);",
                description="strncpy로 교체",
            ),
            source="hybrid",
        )
        assert item.severity == "critical"


class TestIssueList:
    def test_valid(self):
        issue_list = IssueList(
            generated_at=datetime(2026, 2, 24, 15, 30),
            session_id="20260224_153000",
            total_issues=1,
            by_severity={"critical": 1},
            issues=[
                IssueListItem(
                    issue_id="C-001",
                    file="/app/calc.c",
                    language="c",
                    category="memory_safety",
                    severity="critical",
                    title="test",
                    description="test",
                    location=Location(
                        file="/app/calc.c", line_start=1, line_end=1
                    ),
                    fix=CodeFix(before="a", after="b", description="c"),
                    source="llm",
                )
            ],
        )
        assert issue_list.total_issues == 1
        assert issue_list.by_severity["critical"] == 1


class TestChecklistItem:
    def test_valid(self):
        item = ChecklistItem(
            id="CHECK-1",
            category="memory_safety",
            severity="critical",
            description="모든 strcpy를 strncpy로 교체 완료",
            related_issues=["C-001", "C-003"],
            verification_command="grep -n 'strcpy' /app/src/calc.c",
            expected_result="매칭 결과 없음 (0건)",
        )
        assert len(item.related_issues) == 2

    def test_severity_only_critical_or_high(self):
        with pytest.raises(ValidationError):
            ChecklistItem(
                id="CHECK-1",
                category="code_quality",
                severity="low",  # type: ignore[arg-type]
                description="test",
                related_issues=[],
                verification_command="echo test",
                expected_result="test",
            )


class TestChecklist:
    def test_valid(self):
        checklist = Checklist(
            generated_at=datetime(2026, 2, 24, 15, 30),
            session_id="20260224_153000",
            total_checks=1,
            items=[
                ChecklistItem(
                    id="CHECK-1",
                    category="memory_safety",
                    severity="critical",
                    description="strcpy 교체 확인",
                    related_issues=["C-001"],
                    verification_command="grep -n 'strcpy' /app/calc.c",
                    expected_result="0건",
                )
            ],
        )
        assert checklist.total_checks == 1


class TestAnalysisMetadata:
    def test_valid(self):
        meta = AnalysisMetadata(
            session_id="20260224_153000",
            analyzed_at=datetime(2026, 2, 24, 15, 30),
            total_files=5,
            total_lines=2340,
            analysis_duration_seconds=45.2,
            total_llm_tokens=28500,
        )
        assert meta.total_files == 5


class TestIssueSummary:
    def test_valid(self):
        summary = IssueSummary(
            total=7,
            by_severity={"critical": 2, "high": 3, "medium": 1, "low": 1},
            by_category={"memory_safety": 3, "data_integrity": 2},
            by_language={"c": 4, "proc": 2, "sql": 1},
            by_file={"/app/calc.c": 4, "/app/process.pc": 2},
        )
        assert summary.total == 7


class TestRiskAssessment:
    def test_critical(self):
        risk = RiskAssessment(
            deployment_risk="CRITICAL",
            deployment_allowed=False,
            blocking_issues=["C-001", "C-003"],
            risk_description="Critical 이슈 2건 발견",
        )
        assert risk.deployment_allowed is False

    def test_low_risk(self):
        risk = RiskAssessment(
            deployment_risk="LOW",
            deployment_allowed=True,
            blocking_issues=[],
            risk_description="배포 가능",
        )
        assert risk.deployment_allowed is True

    def test_invalid_risk_level(self):
        with pytest.raises(ValidationError):
            RiskAssessment(
                deployment_risk="UNKNOWN",  # type: ignore[arg-type]
                deployment_allowed=True,
                blocking_issues=[],
                risk_description="test",
            )


class TestSummary:
    def test_valid(self):
        summary = Summary(
            analysis_metadata=AnalysisMetadata(
                session_id="20260224_153000",
                analyzed_at=datetime(2026, 2, 24, 15, 30),
                total_files=5,
                total_lines=2340,
                analysis_duration_seconds=45.2,
                total_llm_tokens=28500,
            ),
            issue_summary=IssueSummary(
                total=7,
                by_severity={"critical": 2, "high": 3, "medium": 1, "low": 1},
                by_category={"memory_safety": 3},
                by_language={"c": 4},
                by_file={"/app/calc.c": 4},
            ),
            risk_assessment=RiskAssessment(
                deployment_risk="CRITICAL",
                deployment_allowed=False,
                blocking_issues=["C-001"],
                risk_description="Critical 이슈 발견",
            ),
        )
        assert summary.risk_assessment.deployment_risk == "CRITICAL"

    def test_json_roundtrip(self):
        summary = Summary(
            analysis_metadata=AnalysisMetadata(
                session_id="20260224_153000",
                analyzed_at=datetime(2026, 2, 24, 15, 30),
                total_files=5,
                total_lines=2340,
                analysis_duration_seconds=45.2,
                total_llm_tokens=28500,
            ),
            issue_summary=IssueSummary(
                total=3,
                by_severity={"high": 2, "low": 1},
                by_category={"security": 2, "code_quality": 1},
                by_language={"javascript": 3},
                by_file={"/app/index.js": 3},
            ),
            risk_assessment=RiskAssessment(
                deployment_risk="HIGH",
                deployment_allowed=True,
                blocking_issues=[],
                risk_description="High 이슈 존재하나 배포 가능",
            ),
        )
        json_str = summary.model_dump_json()
        restored = Summary.model_validate_json(json_str)
        assert restored.issue_summary.total == 3
        assert restored.analysis_metadata.session_id == "20260224_153000"
