"""scanner_to_issue 단위 테스트.

high-confidence scanner finding의 Issue 변환과 dedup 로직을 검증한다.
"""

from mider.models.analysis_result import Issue
from mider.tools.utility.scanner_to_issue import (
    dedupe_issues,
    finding_to_issue,
    is_promotable,
    promote_findings,
)


class TestPromotableWhitelist:
    def test_loop_init_missing_promotable(self):
        assert is_promotable("LOOP_INIT_MISSING")

    def test_cursor_dup_close_promotable(self):
        assert is_promotable("CURSOR_DUPLICATE_CLOSE")

    def test_format_arg_mismatch_promotable(self):
        assert is_promotable("FORMAT_ARG_MISMATCH")

    def test_unsafe_func_not_promotable(self):
        """범용 정적 룰은 LLM 검증 거쳐야 하므로 직접 promotion 안 함."""
        assert not is_promotable("UNSAFE_FUNC")

    def test_uninit_var_not_promotable(self):
        assert not is_promotable("UNINIT_VAR")


class TestFindingToIssue:
    def test_loop_init_with_variable(self):
        finding = {
            "pattern_id": "LOOP_INIT_MISSING",
            "severity": "high",
            "line": 5951,
            "variable": "gst_sec_06",
            "code": "while(li_loop_flag == TRUE)",
            "description": "구조체 gst_sec_06 초기화 누락",
        }
        issue = finding_to_issue(
            finding, file="/app/x.pc", issue_id="PC-S-001",
            static_tool="proc_heuristic",
        )
        assert issue is not None
        assert issue["issue_id"] == "PC-S-001"
        assert issue["category"] == "data_integrity"
        assert issue["severity"] == "high"
        assert "gst_sec_06" in issue["title"]
        assert issue["location"]["line_start"] == 5951
        assert issue["location"]["file"] == "/app/x.pc"
        assert issue["source"] == "static_analysis"
        assert issue["static_rule"] == "LOOP_INIT_MISSING"
        assert issue["static_tool"] == "proc_heuristic"
        # Pydantic 스키마 검증
        Issue.model_validate(issue)

    def test_cursor_dup_close_uses_all_lines(self):
        finding = {
            "pattern_id": "CURSOR_DUPLICATE_CLOSE",
            "severity": "high",
            "line": 515,
            "variable": "zord_x_f0003",
            "all_lines": [515, 535, 583],
            "function": "b200_suces_pen_mth",
            "code": "rc = mpfmdbio_cclose_ar(...)",
            "description": "...",
        }
        issue = finding_to_issue(
            finding, file="/app/x.c", issue_id="C-S-001", static_tool="c_heuristic",
        )
        assert issue["location"]["line_start"] == 515
        assert issue["location"]["line_end"] == 583
        Issue.model_validate(issue)

    def test_format_arg_mismatch_includes_counts(self):
        finding = {
            "pattern_id": "FORMAT_ARG_MISMATCH",
            "severity": "critical",
            "line": 886,
            "function_call": "snprintf",
            "format_count": 27,
            "arg_count": 26,
            "code": "snprintf(buf, ...)",
            "description": "...",
        }
        issue = finding_to_issue(
            finding, file="/x.pc", issue_id="PC-S-002",
        )
        assert issue["category"] == "memory_safety"
        assert issue["severity"] == "critical"
        assert "27" in issue["fix"]["after"]
        assert "26" in issue["fix"]["after"]
        Issue.model_validate(issue)

    def test_non_promotable_returns_none(self):
        finding = {
            "pattern_id": "UNSAFE_FUNC",
            "severity": "high",
            "line": 10,
            "code": "strcpy(dst, src);",
        }
        assert finding_to_issue(
            finding, file="/x.c", issue_id="C-S-001",
        ) is None


class TestPromoteFindings:
    def test_filters_to_promotable_only(self):
        findings = [
            {"pattern_id": "LOOP_INIT_MISSING", "severity": "high",
             "line": 100, "variable": "v1", "code": "while(...)"},
            {"pattern_id": "UNSAFE_FUNC", "severity": "high", "line": 50,
             "code": "strcpy(...)"},  # not promotable
            {"pattern_id": "CURSOR_DUPLICATE_CLOSE", "severity": "high",
             "line": 200, "variable": "cur1", "all_lines": [200, 220],
             "code": "..."},
        ]
        issues = promote_findings(findings, file="/x.pc", id_prefix="PC-S")
        assert len(issues) == 2
        assert issues[0]["issue_id"] == "PC-S-001"
        assert issues[1]["issue_id"] == "PC-S-002"
        assert {i["static_rule"] for i in issues} == {
            "LOOP_INIT_MISSING", "CURSOR_DUPLICATE_CLOSE"
        }

    def test_empty_input(self):
        assert promote_findings([], file="/x.pc") == []

    def test_no_promotable_returns_empty(self):
        findings = [{"pattern_id": "UNSAFE_FUNC", "line": 10}]
        assert promote_findings(findings, file="/x.pc") == []


class TestDedupeIssues:
    def _llm_issue(self, line: int, rule: str | None = None,
                   title: str = "이슈") -> dict:
        return {
            "issue_id": f"PC-{line}",
            "category": "data_integrity",
            "severity": "high",
            "title": title,
            "description": "...",
            "location": {"file": "/x.pc", "line_start": line, "line_end": line},
            "fix": {"before": "x", "after": "y", "description": ""},
            "source": "llm",
            "static_tool": None,
            "static_rule": rule,
        }

    def _static_issue(self, line: int, rule: str) -> dict:
        return {
            "issue_id": f"PC-S-{line}",
            "category": "data_integrity",
            "severity": "high",
            "title": "scanner-version",
            "description": "...",
            "location": {"file": "/x.pc", "line_start": line, "line_end": line},
            "fix": {"before": "x", "after": "y", "description": ""},
            "source": "static_analysis",
            "static_tool": "proc_heuristic",
            "static_rule": rule,
        }

    def test_no_collision_keeps_all(self):
        issues = [
            self._llm_issue(100, "LOOP_INIT_MISSING"),
            self._llm_issue(200, "CURSOR_DUPLICATE_CLOSE"),
        ]
        out = dedupe_issues(issues, prefer_static=True)
        assert len(out) == 2

    def test_collision_static_wins(self):
        """같은 (file, line, rule) → static_analysis 버전 우선."""
        scanner = self._static_issue(100, "LOOP_INIT_MISSING")
        llm = self._llm_issue(100, "LOOP_INIT_MISSING")
        out = dedupe_issues([scanner, llm], prefer_static=True)
        assert len(out) == 1
        assert out[0]["source"] == "static_analysis"
        assert out[0]["title"] == "scanner-version"

    def test_collision_static_wins_regardless_of_order(self):
        scanner = self._static_issue(100, "LOOP_INIT_MISSING")
        llm = self._llm_issue(100, "LOOP_INIT_MISSING")
        out = dedupe_issues([llm, scanner], prefer_static=True)
        assert len(out) == 1
        assert out[0]["source"] == "static_analysis"

    def test_different_rule_same_line_kept(self):
        """같은 라인이라도 rule이 다르면 별도 이슈."""
        a = self._static_issue(100, "LOOP_INIT_MISSING")
        b = self._static_issue(100, "CURSOR_DUPLICATE_CLOSE")
        out = dedupe_issues([a, b], prefer_static=True)
        assert len(out) == 2

    def test_llm_no_static_rule_uses_title_prefix(self):
        """LLM이 static_rule 없이 보고한 이슈도 dedup에 들어감."""
        a = self._llm_issue(100, None, title="중복 ID 이슈")
        b = self._llm_issue(100, None, title="중복 ID 이슈")
        out = dedupe_issues([a, b], prefer_static=True)
        assert len(out) == 1
