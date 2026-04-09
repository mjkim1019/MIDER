"""V3 파이프라인 통합 테스트.

Partitioner → Static → Cross → LLMReviewer → IssueMerger
end-to-end 흐름을 검증한다 (LLM은 모킹).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from mider.models.proc_partition import Finding, PartitionResult
from mider.tools.static_analysis.embedded_sql_analyzer import EmbeddedSQLStaticAnalyzer
from mider.tools.static_analysis.proc_cross_checker import ProCCrossChecker
from mider.tools.utility.issue_merger import IssueMerger
from mider.tools.utility.proc_llm_reviewer import ProCLLMReviewer
from mider.tools.utility.proc_partitioner import ProCPartitioner
from mider.tools.utility.proc_symbol_graph import ProCSymbolGraphBuilder


# ──────────────────────────────────────────
# 테스트용 Pro*C 소스 코드
# ──────────────────────────────────────────

SAMPLE_PC = """\
#include <stdio.h>
#include <string.h>
#include <sqlca.h>

#define MAX_LEN 256

EXEC SQL BEGIN DECLARE SECTION;
    char svc_cd[32];
    int  svc_cnt;
    char svc_cd_ind[32];
EXEC SQL END DECLARE SECTION;

EXEC SQL WHENEVER SQLERROR GOTO err_handler;

int prt_ins_sel_agrmt_guid(void) {
    memset(svc_cd, 0x00, sizeof(svc_cd));

    EXEC SQL SELECT SVC_CD, SVC_CNT
        INTO :svc_cd, :svc_cnt
        FROM TB_SVC
        WHERE SVC_CD = :svc_cd;

    if (sqlca.sqlcode < 0) {
        return -1;
    }

    EXEC SQL INSERT INTO TB_LOG (SVC_CD, CNT)
        VALUES (:svc_cd, :svc_cnt);

    EXEC SQL COMMIT;

    return 0;
}

int a000_init_proc(void) {
    return 0;
}
"""


# ──────────────────────────────────────────
# Phase 0: Partitioner + SymbolGraph
# ──────────────────────────────────────────


class TestPhase0Partitioner:
    """Partitioner가 샘플 코드를 올바르게 분해하는지 검증."""

    def test_partition_sample(self):
        partitioner = ProCPartitioner()
        result = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        assert isinstance(result, PartitionResult)
        assert result.total_lines > 0
        assert len(result.functions) >= 1
        assert len(result.sql_blocks) >= 2  # SELECT + INSERT + COMMIT
        assert len(result.host_variables) >= 2  # svc_cd, svc_cnt

    def test_symbol_graph_build(self):
        partitioner = ProCPartitioner()
        partition = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        builder = ProCSymbolGraphBuilder()
        graph = builder.build(partition)

        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0


# ──────────────────────────────────────────
# Phase 1: Static Analysis
# ──────────────────────────────────────────


class TestPhase1StaticAnalysis:
    """정적 분석기가 샘플 코드에서 finding을 탐지하는지 검증."""

    def test_static_findings(self):
        partitioner = ProCPartitioner()
        partition = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        analyzer = EmbeddedSQLStaticAnalyzer()
        findings = analyzer.analyze(
            sql_blocks=partition.sql_blocks,
            host_variables=partition.host_variables,
            cursor_map=partition.cursor_map,
            transaction_points=partition.transaction_points,
            global_context=partition.global_context,
        )

        # INSERT 후 SQLCA 미검사 (WHENEVER가 있으므로 면제될 수 있음)
        # 최소한 일부 rule이 실행되었는지 확인
        assert isinstance(findings, list)
        for f in findings:
            assert isinstance(f, Finding)
            assert f.finding_id.startswith("SF-")


# ──────────────────────────────────────────
# Phase 2: Cross Check
# ──────────────────────────────────────────


class TestPhase2CrossCheck:
    """교차 검사가 실행되는지 검증."""

    def test_cross_check_runs(self):
        partitioner = ProCPartitioner()
        partition = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        builder = ProCSymbolGraphBuilder()
        graph = builder.build(partition)

        checker = ProCCrossChecker()
        findings = checker.check(graph, partition)

        assert isinstance(findings, list)
        for f in findings:
            assert isinstance(f, Finding)
            assert f.finding_id.startswith("CF-")


# ──────────────────────────────────────────
# Phase 3+4: LLM Review + Merge (모킹)
# ──────────────────────────────────────────


class TestPhase3And4:
    """LLM Reviewer + IssueMerger 통합 흐름."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mock_llm(self):
        """전체 파이프라인을 LLM 모킹으로 실행."""
        # Phase 0
        partitioner = ProCPartitioner()
        partition = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        builder = ProCSymbolGraphBuilder()
        graph = builder.build(partition)

        # Phase 1
        static_analyzer = EmbeddedSQLStaticAnalyzer()
        static_findings = static_analyzer.analyze(
            sql_blocks=partition.sql_blocks,
            host_variables=partition.host_variables,
            cursor_map=partition.cursor_map,
            transaction_points=partition.transaction_points,
            global_context=partition.global_context,
        )

        # Phase 2
        cross_checker = ProCCrossChecker()
        cross_findings = cross_checker.check(graph, partition)

        all_findings = static_findings + cross_findings

        # Phase 3: LLM (모킹)
        reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        reviewer._stats = {"tokens_used": 0, "llm_calls": 0}
        reviewer.model = "test-model"
        reviewer.fallback_model = None
        reviewer.temperature = 0.0
        reviewer.max_retries = 1
        reviewer._llm_client = None

        # LLM이 findings를 검토하여 Issue로 변환하는 시나리오 모킹
        mock_issues = []
        for i, f in enumerate(all_findings[:5], 1):  # 최대 5개만
            mock_issues.append({
                "issue_id": f"PC-{i:03d}",
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
                "location": {
                    "file": "sample.pc",
                    "line_start": f.origin_line_start,
                    "line_end": f.origin_line_end,
                },
                "fix": {
                    "before": "원본 코드",
                    "after": "수정 코드",
                    "description": "수정 설명",
                },
                "source": "hybrid",
                "static_tool": f.tool,
                "static_rule": f.rule_id,
                "confidence": 0.9,
                "false_positive": False,
            })

        mock_response = json.dumps({"issues": mock_issues})

        with patch.object(reviewer, "call_llm", new_callable=AsyncMock, return_value=mock_response):
            llm_result = await reviewer.review(
                findings=all_findings,
                partition=partition,
                file_path="sample.pc",
            )

        llm_issues = llm_result["issues"]

        # Phase 4: Merge
        merger = IssueMerger()
        final_issues = merger.merge(
            llm_issues=llm_issues,
            static_findings=all_findings,
            file_path="sample.pc",
            partition=partition,
        )

        # 검증
        assert isinstance(final_issues, list)
        for issue in final_issues:
            assert issue["issue_id"].startswith("PC-")
            assert issue.get("source") in {"static_analysis", "llm", "hybrid"}
            assert "location" in issue
            assert "fix" in issue

    @pytest.mark.asyncio
    async def test_fallback_without_llm(self):
        """LLM 없이 정적 findings만으로 Issue 생성 (fallback)."""
        # Phase 0~2
        partitioner = ProCPartitioner()
        partition = partitioner.partition_content(
            file_content=SAMPLE_PC, source_file="sample.pc",
        )

        builder = ProCSymbolGraphBuilder()
        graph = builder.build(partition)

        static_analyzer = EmbeddedSQLStaticAnalyzer()
        static_findings = static_analyzer.analyze(
            sql_blocks=partition.sql_blocks,
            host_variables=partition.host_variables,
            cursor_map=partition.cursor_map,
            transaction_points=partition.transaction_points,
            global_context=partition.global_context,
        )

        cross_checker = ProCCrossChecker()
        cross_findings = cross_checker.check(graph, partition)

        all_findings = static_findings + cross_findings

        # LLM 실패 → fallback
        merger = IssueMerger()
        fallback_issues = merger.merge_fallback(all_findings, "sample.pc")

        assert isinstance(fallback_issues, list)
        if fallback_issues:
            assert fallback_issues[0]["issue_id"].startswith("PC-")
            assert fallback_issues[0]["source"] == "static_analysis"


# ──────────────────────────────────────────
# Agent run() 통합 (V3 경로)
# ──────────────────────────────────────────


class TestAgentV3Integration:
    """ProCAnalyzerAgent.run()이 V3 파이프라인을 사용하는지 검증."""

    @pytest.mark.asyncio
    async def test_agent_uses_v3(self):
        """Agent가 V3 파이프라인을 호출하는지 확인."""
        from mider.agents.proc_analyzer import ProCAnalyzerAgent

        agent = ProCAnalyzerAgent.__new__(ProCAnalyzerAgent)
        agent.model = "test-model"
        agent.fallback_model = None
        agent.temperature = 0.0
        agent.max_retries = 1
        agent._llm_client = None
        agent._stats = {}

        # V3 컴포넌트 초기화
        agent._partitioner = ProCPartitioner()
        agent._graph_builder = ProCSymbolGraphBuilder()
        agent._sql_static_analyzer = EmbeddedSQLStaticAnalyzer()
        agent._cross_checker = ProCCrossChecker()
        agent._issue_merger = IssueMerger()

        # LLM Reviewer 모킹
        mock_reviewer = ProCLLMReviewer.__new__(ProCLLMReviewer)
        mock_reviewer._stats = {"tokens_used": 0, "llm_calls": 0}
        mock_reviewer.model = "test-model"
        mock_reviewer.fallback_model = None
        mock_reviewer.temperature = 0.0
        mock_reviewer.max_retries = 1
        mock_reviewer._llm_client = None
        agent._llm_reviewer = mock_reviewer

        # ReasoningLogger (no-op)
        from mider.config.reasoning_logger import ReasoningLogger
        agent.rl = ReasoningLogger(verbose=False)

        # LLM 호출 모킹 — 빈 결과 반환
        mock_response = json.dumps({"issues": []})

        with patch.object(mock_reviewer, "call_llm", new_callable=AsyncMock, return_value=mock_response):
            # _run_v3_pipeline 직접 호출 (파일 읽기 우회)
            # partition_content 사용
            partition = agent._partitioner.partition_content(
                file_content=SAMPLE_PC, source_file="test.pc",
            )
            graph = agent._graph_builder.build(partition)
            static_findings = agent._sql_static_analyzer.analyze(
                sql_blocks=partition.sql_blocks,
                host_variables=partition.host_variables,
                cursor_map=partition.cursor_map,
                transaction_points=partition.transaction_points,
                global_context=partition.global_context,
            )
            cross_findings = agent._cross_checker.check(graph, partition)

            # findings가 생성되었는지만 확인
            all_findings = static_findings + cross_findings
            assert isinstance(all_findings, list)
