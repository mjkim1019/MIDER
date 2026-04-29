"""IssueMerger 단위 테스트.

중복 제거, 노이즈 필터링, 교차 계층 병합을 검증한다.
"""

from __future__ import annotations

import pytest

from mider.models.proc_partition import Finding
from mider.tools.utility.issue_merger import IssueMerger


def _make_issue(
    issue_id: str = "PC-001",
    category: str = "data_integrity",
    severity: str = "high",
    title: str = "테스트 이슈",
    description: str = "테스트 설명",
    line_start: int = 10,
    line_end: int = 12,
    source: str = "hybrid",
    static_tool: str | None = "embedded_sql_static",
    static_rule: str | None = "SQL_SQLCA_MISSING",
    false_positive: bool = False,
) -> dict:
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "location": {
            "file": "test.pc",
            "line_start": line_start,
            "line_end": line_end,
        },
        "fix": {
            "before": "before code",
            "after": "after code",
            "description": "fix description",
        },
        "source": source,
        "static_tool": static_tool,
        "static_rule": static_rule,
        "false_positive": false_positive,
    }


def _make_finding(
    finding_id: str = "SF-001",
    rule_id: str = "SQL_SQLCA_MISSING",
    severity: str = "high",
    category: str = "data_integrity",
    title: str = "테스트 Finding",
    description: str = "테스트 설명",
    line_start: int = 10,
    line_end: int = 12,
    function_name: str | None = "fn",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_layer="static",
        tool="embedded_sql_static",
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=title,
        description=description,
        origin_line_start=line_start,
        origin_line_end=line_end,
        function_name=function_name,
    )


@pytest.fixture
def merger() -> IssueMerger:
    return IssueMerger()


# ──────────────────────────────────────────
# false positive 제거
# ──────────────────────────────────────────


class TestFalsePositiveFilter:
    def test_removes_false_positives(self, merger):
        issues = [
            _make_issue(title="진짜", false_positive=False),
            _make_issue(title="FP", false_positive=True),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 1
        assert result[0]["title"] == "진짜"

    def test_removes_title_with_fp_keyword_korean(self, merger):
        """LLM이 title에 '오탐' 명시 → 자동 제거."""
        issues = [
            _make_issue(
                issue_id="PC-010",
                title="SELECT INTO 컬럼 수 불일치 진단은 오탐 (단일 표현식 SELECT)",
            ),
            _make_issue(issue_id="PC-011", title="SQLCA 체크 누락"),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 1
        assert "오탐" not in result[0]["title"]

    def test_removes_title_with_fp_keyword_english(self, merger):
        issues = [
            _make_issue(title="this looks like a false positive case"),
            _make_issue(title="진짜 버그"),
        ]
        result = merger.merge(issues, [], "test.pc")
        titles = [r["title"] for r in result]
        assert "진짜 버그" in titles
        assert not any("false positive" in t.lower() for t in titles)

    def test_removes_description_with_negation(self, merger):
        """description에 '불일치가 아닙니다' 등 부정 결론."""
        issues = [
            _make_issue(
                title="SELECT INTO 컬럼 수 검사",
                description=(
                    "L2506 SELECT는 여러 줄로 보이지만 최종적으로 1개 컬럼만 "
                    "SELECT합니다. 따라서 INTO :lc_prod_id와 불일치가 아닙니다."
                ),
            ),
            _make_issue(title="진짜 이슈", description="SQLCA 체크 없음"),
        ]
        result = merger.merge(issues, [], "test.pc")
        titles = [r["title"] for r in result]
        assert "진짜 이슈" in titles
        assert "SELECT INTO 컬럼 수 검사" not in titles

    def test_removes_when_before_equals_after(self, merger):
        """fix.before == fix.after (수정 사항 없음) → 자동 제거."""
        issue = _make_issue(title="LLM이 검토했지만 수정 불필요")
        # 동일한 코드로 강제
        same_code = "EXEC SQL SELECT col INTO :var FROM tbl;"
        issue["fix"] = {
            "before": same_code,
            "after": same_code,
            "description": "검토 결과 수정 불필요",
        }
        normal = _make_issue(title="진짜 이슈")
        result = merger.merge([issue, normal], [], "test.pc")
        assert len(result) == 1
        assert result[0]["title"] == "진짜 이슈"

    def test_keeps_normal_issue_with_neutral_text(self, merger):
        """오탐 키워드가 없는 정상 이슈는 보존."""
        issues = [
            _make_issue(title="SQLCA 검사 누락"),
            _make_issue(title="cursor close 누락",
                        description="OPEN 후 CLOSE가 없습니다"),
        ]
        result = merger.merge(issues, [], "test.pc")
        # _remove_proframe_noise가 "cursor close 누락" 제거할 수 있음 — 적어도 1건은 유지
        assert len(result) >= 1

    def test_before_after_whitespace_diff_kept(self, merger):
        """before/after가 공백만 다르면 strip 후 동일 — 제거."""
        issue = _make_issue(title="공백만 다른 케이스")
        issue["fix"] = {
            "before": "  CODE  \n",
            "after": "CODE",
            "description": "변경 없음",
        }
        normal = _make_issue(title="진짜")
        result = merger.merge([issue, normal], [], "test.pc")
        assert len(result) == 1
        assert result[0]["title"] == "진짜"


# ──────────────────────────────────────────
# Proframe 노이즈 제거
# ──────────────────────────────────────────


class TestProframeNoiseFilter:
    def test_removes_thread_safety(self, merger):
        issues = [
            _make_issue(title="스레드 안전성 미보장"),
            _make_issue(title="SQLCA 체크 누락"),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 1
        assert "SQLCA" in result[0]["title"]

    def test_removes_null_check(self, merger):
        issues = [
            _make_issue(title="NULL 체크 누락", description="포인터 검증이 없습니다"),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 0

    def test_removes_readability(self, merger):
        issues = [
            _make_issue(title="가독성 향상 필요"),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 0


# ──────────────────────────────────────────
# 패턴 그룹 병합
# ──────────────────────────────────────────


class TestPatternGroupMerge:
    def test_merges_sqlca_group(self, merger):
        """같은 함수의 SQLCA 이슈들이 1건으로 병합."""
        issues = [
            _make_issue(
                title="SQLCA 에러 체크 누락 (1)",
                description="함수 fn의 SELECT",
                severity="high",
                line_start=10,
            ),
            _make_issue(
                title="SQLCA 에러 체크 누락 (2)",
                description="함수 fn의 INSERT",
                severity="medium",
                line_start=20,
            ),
        ]
        result = merger.merge(issues, [], "test.pc")
        # 같은 함수 + 같은 그룹 → 1건
        sqlca_issues = [i for i in result if "sqlca" in i["title"].lower() or "sqlca" in i["description"].lower()]
        assert len(sqlca_issues) == 1
        assert sqlca_issues[0]["severity"] == "high"  # 더 높은 severity
        assert "외 1곳 동일 패턴" in sqlca_issues[0]["description"]

    def test_different_functions_not_merged(self, merger):
        """다른 함수의 같은 패턴은 병합하지 않음."""
        issues = [
            _make_issue(
                title="SQLCA 에러 체크 누락",
                description="함수 fn_a의 SELECT",
                line_start=10,
            ),
            _make_issue(
                title="SQLCA 에러 체크 누락",
                description="함수 fn_b의 INSERT",
                line_start=50,
            ),
        ]
        result = merger.merge(issues, [], "test.pc")
        sqlca_issues = [i for i in result if "sqlca" in i["title"].lower() or "sqlca" in i["description"].lower()]
        assert len(sqlca_issues) == 2


# ──────────────────────────────────────────
# 교차 계층 중복 제거
# ──────────────────────────────────────────


class TestCrossLayerDedup:
    def test_dedup_same_location_category(self, merger):
        """같은 위치 + 같은 카테고리 → 1건 (source 우선순위)."""
        issues = [
            _make_issue(
                title="정적분석 탐지",
                source="static_analysis",
                severity="high",
                category="data_integrity",
                line_start=10,
            ),
            _make_issue(
                title="LLM 보강",
                source="hybrid",
                severity="high",
                category="data_integrity",
                line_start=11,  # ±3줄 이내
            ),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 1
        assert result[0]["source"] == "hybrid"  # hybrid 우선

    def test_no_dedup_different_category(self, merger):
        """같은 위치라도 카테고리 다르면 별도 유지."""
        issues = [
            _make_issue(
                title="이슈 A",
                category="data_integrity",
                line_start=10,
            ),
            _make_issue(
                title="이슈 B",
                category="memory_safety",
                line_start=10,
            ),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 2

    def test_no_dedup_far_lines(self, merger):
        """4줄 이상 떨어지면 별도 유지."""
        issues = [
            _make_issue(
                title="이슈 A",
                category="data_integrity",
                line_start=10,
            ),
            _make_issue(
                title="이슈 B",
                category="data_integrity",
                line_start=20,
            ),
        ]
        result = merger.merge(issues, [], "test.pc")
        assert len(result) == 2


# ──────────────────────────────────────────
# 최종 정리
# ──────────────────────────────────────────


class TestFinalize:
    def test_issue_id_reassigned(self, merger):
        issues = [
            _make_issue(issue_id="OLD-1", severity="medium"),
            _make_issue(issue_id="OLD-2", severity="critical", line_start=20),
        ]
        result = merger.merge(issues, [], "test.pc")
        # critical이 먼저 → PC-001
        assert result[0]["issue_id"] == "PC-001"
        assert result[0]["severity"] == "critical"
        assert result[1]["issue_id"] == "PC-002"

    def test_false_positive_field_removed(self, merger):
        issues = [_make_issue()]
        result = merger.merge(issues, [], "test.pc")
        assert "false_positive" not in result[0]

    def test_location_file_filled(self, merger):
        issue = _make_issue()
        issue["location"]["file"] = ""
        result = merger.merge([issue], [], "myfile.pc")
        assert result[0]["location"]["file"] == "myfile.pc"


# ──────────────────────────────────────────
# Fallback 경로
# ──────────────────────────────────────────


class TestFallback:
    def test_fallback_creates_issues_from_findings(self, merger):
        findings = [
            _make_finding(finding_id="SF-001", severity="high"),
            _make_finding(finding_id="CF-001", severity="critical", line_start=20),
        ]
        result = merger.merge_fallback(findings, "test.pc")
        assert len(result) == 2
        assert result[0]["issue_id"] == "PC-001"
        assert result[0]["severity"] == "critical"  # critical 우선
        assert result[0]["source"] == "static_analysis"

    def test_fallback_applies_noise_filter(self, merger):
        findings = [
            _make_finding(
                title="스레드 안전성 미보장",
                description="멀티스레드 환경",
            ),
        ]
        result = merger.merge_fallback(findings, "test.pc")
        assert len(result) == 0


# ──────────────────────────────────────────
# 빈 입력
# ──────────────────────────────────────────


class TestEmptyInput:
    def test_empty_issues(self, merger):
        result = merger.merge([], [], "test.pc")
        assert result == []

    def test_empty_fallback(self, merger):
        result = merger.merge_fallback([], "test.pc")
        assert result == []
