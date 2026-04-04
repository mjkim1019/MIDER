"""AnalysisResult 스키마 단위 테스트."""

import pytest
from pydantic import ValidationError

from mider.models.analysis_result import (
    AnalysisResult,
    CodeFix,
    Issue,
    Location,
)


class TestLocation:
    def test_valid(self):
        loc = Location(
            file="/app/calc.c",
            line_start=234,
            line_end=234,
            column_start=5,
            column_end=35,
        )
        assert loc.line_start == 234

    def test_optional_columns(self):
        loc = Location(
            file="/app/calc.c",
            line_start=10,
            line_end=12,
        )
        assert loc.column_start is None
        assert loc.column_end is None


class TestCodeFix:
    def test_valid(self):
        fix = CodeFix(
            before="strcpy(dest, src);",
            after="strncpy(dest, src, sizeof(dest) - 1);",
            description="strcpy를 strncpy로 교체",
        )
        assert "strncpy" in fix.after


class TestIssue:
    def test_valid(self):
        issue = Issue(
            issue_id="C-001",
            category="memory_safety",
            severity="critical",
            title="strcpy 버퍼 오버플로우 위험",
            description="strcpy()는 버퍼 크기를 검증하지 않습니다.",
            location=Location(
                file="/app/calc.c",
                line_start=234,
                line_end=234,
            ),
            fix=CodeFix(
                before="strcpy(dest, src);",
                after="strncpy(dest, src, sizeof(dest) - 1);",
                description="strcpy를 strncpy로 교체",
            ),
            source="hybrid",
            static_tool="clang-tidy",
            static_rule="bugprone-not-null-terminated-result",
        )
        assert issue.issue_id == "C-001"
        assert issue.source == "hybrid"

    def test_invalid_category_fallback(self):
        """허용 목록 밖의 category는 code_quality로 폴백된다."""
        issue = Issue(
            issue_id="X-001",
            category="invalid_category",  # type: ignore[arg-type]
            severity="low",
            title="test",
            description="test",
            location=Location(
                file="/a", line_start=1, line_end=1
            ),
            fix=CodeFix(before="a", after="b", description="c"),
            source="llm",
        )
        assert issue.category == "code_quality"

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            Issue(
                issue_id="JS-001",
                category="security",
                severity="unknown",  # type: ignore[arg-type]
                title="test",
                description="test",
                location=Location(
                    file="/a", line_start=1, line_end=1
                ),
                fix=CodeFix(before="a", after="b", description="c"),
                source="llm",
            )

    def test_optional_static_fields(self):
        issue = Issue(
            issue_id="JS-001",
            category="security",
            severity="high",
            title="XSS 취약점",
            description="innerHTML 사용",
            location=Location(
                file="/app/index.js", line_start=10, line_end=10
            ),
            fix=CodeFix(
                before="el.innerHTML = data;",
                after="el.textContent = data;",
                description="innerHTML 대신 textContent 사용",
            ),
            source="llm",
        )
        assert issue.static_tool is None
        assert issue.static_rule is None


class TestAnalysisResult:
    def test_valid(self):
        result = AnalysisResult(
            task_id="task_2",
            file="/app/calc.c",
            language="c",
            agent="CAnalyzerAgent",
            issues=[],
            analysis_time_seconds=8.5,
            llm_tokens_used=4200,
        )
        assert result.error is None
        assert result.issues == []

    def test_with_error(self):
        result = AnalysisResult(
            task_id="task_1",
            file="/app/test.js",
            language="javascript",
            agent="JavaScriptAnalyzerAgent",
            issues=[],
            analysis_time_seconds=0.5,
            llm_tokens_used=0,
            error="LLM API timeout after 3 retries",
        )
        assert result.error is not None

    def test_json_roundtrip(self):
        issue = Issue(
            issue_id="C-001",
            category="memory_safety",
            severity="critical",
            title="버퍼 오버플로우",
            description="strcpy 위험",
            location=Location(
                file="/app/calc.c",
                line_start=234,
                line_end=234,
                column_start=5,
                column_end=35,
            ),
            fix=CodeFix(
                before="strcpy(dest, src);",
                after="strncpy(dest, src, sizeof(dest) - 1);",
                description="strncpy로 교체",
            ),
            source="hybrid",
            static_tool="clang-tidy",
            static_rule="bugprone-not-null-terminated-result",
        )
        result = AnalysisResult(
            task_id="task_2",
            file="/app/calc.c",
            language="c",
            agent="CAnalyzerAgent",
            issues=[issue],
            analysis_time_seconds=8.5,
            llm_tokens_used=4200,
        )
        json_str = result.model_dump_json()
        restored = AnalysisResult.model_validate_json(json_str)
        assert restored.issues[0].issue_id == "C-001"
        assert restored.issues[0].location.column_start == 5
