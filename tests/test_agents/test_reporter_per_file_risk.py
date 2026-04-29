"""ReporterAgent._build_per_file_risks 단위 테스트.

파일별 배포 판정 계산 로직을 검증한다.
"""

from mider.agents.reporter import ReporterAgent
from mider.models.report import FileRiskAssessment


def _issue(file: str, severity: str, issue_id: str) -> dict:
    return {
        "issue_id": issue_id,
        "file": file,
        "severity": severity,
    }


def _result(file: str, error: str | None = None) -> dict:
    return {"file": file, "error": error}


class TestPerFileRisk:
    def test_critical_blocks_only_that_file(self):
        """critical이 있는 파일만 배포 불가, 다른 파일은 LOW."""
        results = [_result("/app/a.c"), _result("/app/b.c")]
        issues = [
            _issue("/app/a.c", "critical", "C-001"),
        ]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=issues,
            analysis_results=results,
            analysis_errors=[],
        )
        # Pydantic 스키마 검증
        for item in per_file:
            FileRiskAssessment.model_validate(item)

        by_file = {item["file"]: item for item in per_file}
        assert by_file["/app/a.c"]["deployment_risk"] == "CRITICAL"
        assert by_file["/app/a.c"]["deployment_allowed"] is False
        assert "C-001" in by_file["/app/a.c"]["blocking_issues"]

        assert by_file["/app/b.c"]["deployment_risk"] == "LOW"
        assert by_file["/app/b.c"]["deployment_allowed"] is True
        assert by_file["/app/b.c"]["blocking_issues"] == []

    def test_high_thresholds_per_file(self):
        """파일별 high 개수에 따라 HIGH(>=3) / MEDIUM(>=1) 분기."""
        results = [
            _result("/app/many_high.c"),
            _result("/app/few_high.c"),
            _result("/app/clean.c"),
        ]
        issues = [
            _issue("/app/many_high.c", "high", "H-001"),
            _issue("/app/many_high.c", "high", "H-002"),
            _issue("/app/many_high.c", "high", "H-003"),
            _issue("/app/few_high.c", "high", "H-004"),
        ]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=issues,
            analysis_results=results,
            analysis_errors=[],
        )
        by_file = {item["file"]: item for item in per_file}
        assert by_file["/app/many_high.c"]["deployment_risk"] == "HIGH"
        assert by_file["/app/many_high.c"]["deployment_allowed"] is True
        assert by_file["/app/many_high.c"]["high_count"] == 3
        assert by_file["/app/few_high.c"]["deployment_risk"] == "MEDIUM"
        assert by_file["/app/few_high.c"]["deployment_allowed"] is True
        assert by_file["/app/few_high.c"]["high_count"] == 1
        assert by_file["/app/clean.c"]["deployment_risk"] == "LOW"

    def test_analysis_error_marks_unable_to_analyze(self):
        """분석 에러 발생 파일은 UNABLE_TO_ANALYZE + 배포 불가."""
        results = [_result("/app/ok.c"), _result("/app/err.c", error="LLM timeout")]
        errors = [_result("/app/err.c", error="LLM timeout")]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=[_issue("/app/ok.c", "high", "H-001")],
            analysis_results=results,
            analysis_errors=errors,
        )
        by_file = {item["file"]: item for item in per_file}
        assert by_file["/app/err.c"]["deployment_risk"] == "UNABLE_TO_ANALYZE"
        assert by_file["/app/err.c"]["deployment_allowed"] is False
        # 다른 파일은 정상 판정
        assert by_file["/app/ok.c"]["deployment_risk"] == "MEDIUM"
        assert by_file["/app/ok.c"]["deployment_allowed"] is True

    def test_zero_issues_files_get_low(self):
        """이슈 0건 파일도 결과에 포함되며 LOW로 판정."""
        results = [_result("/app/clean1.c"), _result("/app/clean2.c")]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=[],
            analysis_results=results,
            analysis_errors=[],
        )
        assert len(per_file) == 2
        for item in per_file:
            assert item["deployment_risk"] == "LOW"
            assert item["deployment_allowed"] is True
            assert item["critical_count"] == 0

    def test_counts_per_severity(self):
        """critical/high/medium 카운트가 파일 단위로 정확하다."""
        results = [_result("/app/x.c")]
        issues = [
            _issue("/app/x.c", "critical", "C-001"),
            _issue("/app/x.c", "critical", "C-002"),
            _issue("/app/x.c", "high", "H-001"),
            _issue("/app/x.c", "medium", "M-001"),
        ]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=issues,
            analysis_results=results,
            analysis_errors=[],
        )
        item = per_file[0]
        assert item["critical_count"] == 2
        assert item["high_count"] == 1
        assert item["medium_count"] == 1
        assert item["deployment_risk"] == "CRITICAL"
        assert set(item["blocking_issues"]) == {"C-001", "C-002"}

    def test_sorted_alphabetically(self):
        """결과는 파일명 알파벳 순으로 정렬된다."""
        results = [
            _result("/app/zeta.c"),
            _result("/app/alpha.c"),
            _result("/app/beta.c"),
        ]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=[],
            analysis_results=results,
            analysis_errors=[],
        )
        files = [item["file"] for item in per_file]
        assert files == ["/app/alpha.c", "/app/beta.c", "/app/zeta.c"]

    def test_fallback_to_issues_when_results_empty(self):
        """analysis_results가 비면 sorted_issues의 file 필드로 fallback."""
        issues = [
            _issue("/app/a.c", "critical", "C-001"),
            _issue("/app/b.c", "low", "L-001"),
        ]
        per_file = ReporterAgent._build_per_file_risks(
            sorted_issues=issues,
            analysis_results=[],
            analysis_errors=[],
        )
        files = {item["file"] for item in per_file}
        assert files == {"/app/a.c", "/app/b.c"}
