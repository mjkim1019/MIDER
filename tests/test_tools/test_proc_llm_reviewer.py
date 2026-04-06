"""ProCLLMReviewer 단위 테스트.

LLM 호출은 모킹하여 프롬프트 구축, 정렬, 그룹핑, fallback 변환을 검증한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from mider.models.proc_partition import (
    CSegment,
    CursorLifecycleEvent,
    CursorUnit,
    EmbeddedSQLUnit,
    Finding,
    FunctionUnit,
    GlobalContext,
    HostVarUnit,
    PartitionResult,
    SQLKind,
    TransactionPoint,
)
from mider.tools.utility.proc_llm_reviewer import ProCLLMReviewer


def _make_finding(
    finding_id: str = "SF-001",
    rule_id: str = "SQL_SQLCA_MISSING",
    severity: str = "high",
    category: str = "data_integrity",
    source_layer: str = "static",
    tool: str = "embedded_sql_static",
    function_name: str | None = "fn",
    line_start: int = 10,
    line_end: int = 12,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_layer=source_layer,
        tool=tool,
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=f"Test finding {finding_id}",
        description=f"Test description for {finding_id}",
        origin_line_start=line_start,
        origin_line_end=line_end,
        function_name=function_name,
    )


def _make_partition(**kwargs) -> PartitionResult:
    defaults = dict(
        source_file="test.pc",
        total_lines=100,
        file_content="\n".join([f"line {i}" for i in range(100)]),
        global_context=GlobalContext(),
        functions=[],
        c_segments=[],
        sql_blocks=[],
        host_variables=[],
        cursor_map=[],
        transaction_points=[],
    )
    defaults.update(kwargs)
    return PartitionResult(**defaults)


# ──────────────────────────────────────────
# 정렬 테스트
# ──────────────────────────────────────────


class TestFindingSorting:
    def test_sort_by_severity(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [
            _make_finding(finding_id="SF-001", severity="low"),
            _make_finding(finding_id="SF-002", severity="critical"),
            _make_finding(finding_id="SF-003", severity="high"),
        ]
        sorted_f = reviewer._sort_findings(findings)
        assert sorted_f[0].severity == "critical"
        assert sorted_f[1].severity == "high"
        assert sorted_f[2].severity == "low"

    def test_sort_by_layer(self):
        """동일 severity 시 static > cross 순."""
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [
            _make_finding(finding_id="CF-001", severity="high", source_layer="cross"),
            _make_finding(finding_id="SF-001", severity="high", source_layer="static"),
        ]
        sorted_f = reviewer._sort_findings(findings)
        assert sorted_f[0].source_layer == "static"
        assert sorted_f[1].source_layer == "cross"


# ──────────────────────────────────────────
# 함수 그룹핑 테스트
# ──────────────────────────────────────────


class TestGroupByFunction:
    def test_groups_by_function(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [
            _make_finding(finding_id="SF-001", function_name="fn_a"),
            _make_finding(finding_id="SF-002", function_name="fn_b"),
            _make_finding(finding_id="SF-003", function_name="fn_a"),
        ]
        groups = reviewer._group_by_function(findings)
        assert len(groups) == 2
        sizes = sorted(len(g) for g in groups)
        assert sizes == [1, 2]

    def test_global_findings_separate_group(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [
            _make_finding(finding_id="SF-001", function_name=None),
            _make_finding(finding_id="SF-002", function_name="fn"),
        ]
        groups = reviewer._group_by_function(findings)
        assert len(groups) == 2


# ──────────────────────────────────────────
# 프롬프트 구축 테스트
# ──────────────────────────────────────────


class TestPromptBuilding:
    def test_prompt_contains_findings(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [_make_finding()]
        partition = _make_partition()
        prompt = reviewer._build_review_prompt(findings, partition, "test.pc")
        assert "SF-001" in prompt
        assert "SQL_SQLCA_MISSING" in prompt
        assert "정적 분석 결과" in prompt
        assert "Proframe" in prompt

    def test_prompt_contains_code_snippet(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [_make_finding(line_start=5, line_end=7)]
        partition = _make_partition()
        prompt = reviewer._build_review_prompt(findings, partition, "test.pc")
        # 코드 스니펫에 >>> 마커가 있어야 함
        assert ">>>" in prompt

    def test_prompt_contains_host_vars(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [_make_finding(function_name="fn")]
        partition = _make_partition(
            host_variables=[
                HostVarUnit(
                    name="svc_cd",
                    declared_type="char[32]",
                    declared_in_function="fn",
                    declared_line=5,
                ),
            ],
        )
        prompt = reviewer._build_review_prompt(findings, partition, "test.pc")
        assert "svc_cd" in prompt


# ──────────────────────────────────────────
# Fallback 변환 테스트
# ──────────────────────────────────────────


class TestFallbackConversion:
    def test_converts_findings_to_issues(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [
            _make_finding(finding_id="SF-001", rule_id="SQL_SQLCA_MISSING"),
            _make_finding(finding_id="CF-001", rule_id="CROSS_HOST_VAR_UNDECLARED"),
        ]
        issues = reviewer.convert_findings_to_issues(findings, "test.pc")
        assert len(issues) == 2
        assert issues[0]["issue_id"] == "PC-001"
        assert issues[0]["source"] == "static_analysis"
        assert issues[0]["static_rule"] == "SQL_SQLCA_MISSING"

    def test_location_has_file(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        findings = [_make_finding()]
        issues = reviewer.convert_findings_to_issues(findings, "test.pc")
        assert issues[0]["location"]["file"] == "test.pc"


# ──────────────────────────────────────────
# LLM 호출 모킹 테스트
# ──────────────────────────────────────────


class TestLLMReview:
    @pytest.mark.asyncio
    async def test_empty_findings_skip_llm(self):
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        reviewer._stats = {}
        result = await reviewer.review(
            findings=[], partition=_make_partition(), file_path="test.pc",
        )
        assert result["issues"] == []
        assert result["tokens_used"] == 0

    @pytest.mark.asyncio
    async def test_single_call_with_mock(self):
        """1~20 findings → 단일 호출."""
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        reviewer._stats = {"tokens_used": 0, "llm_calls": 0}
        reviewer.model = "test-model"
        reviewer.fallback_model = None
        reviewer.temperature = 0.0
        reviewer.max_retries = 1
        reviewer._llm_client = None

        mock_response = json.dumps({
            "issues": [{
                "issue_id": "PC-001",
                "category": "data_integrity",
                "severity": "high",
                "title": "SQLCA 체크 누락",
                "description": "DML 후 SQLCA 에러 체크가 없습니다.",
                "location": {"file": "test.pc", "line_start": 10, "line_end": 12},
                "fix": {"before": "EXEC SQL ...", "after": "EXEC SQL ...\nif (sqlca.sqlcode < 0) ...", "description": "SQLCA 체크 추가"},
                "source": "hybrid",
                "static_tool": "embedded_sql_static",
                "static_rule": "SQL_SQLCA_MISSING",
                "confidence": 0.95,
                "false_positive": False,
            }],
        })

        with patch.object(reviewer, "call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await reviewer.review(
                findings=[_make_finding()],
                partition=_make_partition(),
                file_path="test.pc",
            )

        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "PC-001"

    @pytest.mark.asyncio
    async def test_false_positive_removed(self):
        """false_positive=True인 이슈는 제거."""
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        reviewer._stats = {"tokens_used": 0, "llm_calls": 0}
        reviewer.model = "test-model"
        reviewer.fallback_model = None
        reviewer.temperature = 0.0
        reviewer.max_retries = 1
        reviewer._llm_client = None

        mock_response = json.dumps({
            "issues": [
                {
                    "issue_id": "PC-001",
                    "category": "data_integrity",
                    "severity": "high",
                    "title": "진짜 이슈",
                    "description": "설명",
                    "location": {"file": "test.pc", "line_start": 10, "line_end": 12},
                    "fix": {"before": "a", "after": "b", "description": "c"},
                    "source": "hybrid",
                    "false_positive": False,
                },
                {
                    "issue_id": "PC-002",
                    "category": "null_safety",
                    "severity": "medium",
                    "title": "FP 이슈",
                    "description": "설명",
                    "location": {"file": "test.pc", "line_start": 20, "line_end": 22},
                    "fix": {"before": "a", "after": "b", "description": "c"},
                    "source": "hybrid",
                    "false_positive": True,
                },
            ],
        })

        with patch.object(reviewer, "call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await reviewer.review(
                findings=[_make_finding(), _make_finding(finding_id="SF-002", line_start=20, line_end=22)],
                partition=_make_partition(),
                file_path="test.pc",
            )

        assert len(result["issues"]) == 1
        assert result["issues"][0]["title"] == "진짜 이슈"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        """LLM 실패 시 빈 리스트 반환."""
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        reviewer._stats = {"tokens_used": 0, "llm_calls": 0}
        reviewer.model = "test-model"
        reviewer.fallback_model = None
        reviewer.temperature = 0.0
        reviewer.max_retries = 1
        reviewer._llm_client = None

        with patch.object(reviewer, "call_llm", new_callable=AsyncMock, side_effect=Exception("API 에러")):
            result = await reviewer.review(
                findings=[_make_finding()],
                partition=_make_partition(),
                file_path="test.pc",
            )

        assert result["issues"] == []
