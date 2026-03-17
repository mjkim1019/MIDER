"""ReporterAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock

import pytest

from mider.agents.reporter import ReporterAgent
from mider.models.report import (
    Checklist,
    IssueList,
    RiskAssessment,
    Summary,
)


def _make_issue(
    issue_id: str = "JS-001",
    category: str = "security",
    severity: str = "critical",
    title: str = "XSS 취약점",
    description: str = "innerHTML에 사용자 입력 직접 대입",
    file: str = "/app/test.js",
    line_start: int = 10,
    source: str = "llm",
) -> dict:
    """테스트용 이슈 딕셔너리 생성."""
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_start,
        },
        "fix": {
            "before": "element.innerHTML = userInput;",
            "after": "element.textContent = userInput;",
            "description": "textContent 사용",
        },
        "source": source,
    }


def _make_analysis_result(
    task_id: str = "task_1",
    file: str = "/app/test.js",
    language: str = "javascript",
    issues: list | None = None,
    llm_tokens_used: int = 100,
) -> dict:
    """테스트용 AnalysisResult 딕셔너리 생성."""
    return {
        "task_id": task_id,
        "file": file,
        "language": language,
        "agent": "TestAgent",
        "issues": issues or [],
        "analysis_time_seconds": 1.5,
        "llm_tokens_used": llm_tokens_used,
        "error": None,
    }


def _make_llm_response(risk_description: str = "테스트 위험 설명") -> str:
    """LLM 응답 JSON 문자열 생성."""
    return json.dumps({
        "summary": {
            "risk_assessment": {
                "risk_description": risk_description,
            },
        },
    })


@pytest.fixture
def agent():
    """ReporterAgent with mocked LLM."""
    agent = ReporterAgent(model="gpt-4o-mini", fallback_model="gpt-4o")
    agent._llm_client = AsyncMock()
    agent._llm_client.chat.return_value = _make_llm_response()
    return agent


@pytest.fixture
def run_kwargs():
    """공통 run() keyword arguments."""
    return {
        "session_id": "test-session-001",
        "total_files": 3,
        "total_lines": 500,
        "analysis_duration_seconds": 10.5,
    }


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_three_reports(self, agent, run_kwargs):
        """run()이 issue_list, checklist, summary 3개를 반환한다."""
        result = await agent.run(analysis_results=[], **run_kwargs)

        assert "issue_list" in result
        assert "checklist" in result
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_empty_results(self, agent, run_kwargs):
        """빈 분석 결과로도 정상 동작한다."""
        result = await agent.run(analysis_results=[], **run_kwargs)

        assert result["issue_list"]["total_issues"] == 0
        assert result["issue_list"]["issues"] == []
        assert result["checklist"]["total_checks"] == 0
        assert result["summary"]["issue_summary"]["total"] == 0

    @pytest.mark.asyncio
    async def test_session_id_propagated(self, agent, run_kwargs):
        """session_id가 모든 리포트에 전파된다."""
        result = await agent.run(analysis_results=[], **run_kwargs)

        assert result["issue_list"]["session_id"] == "test-session-001"
        assert result["checklist"]["session_id"] == "test-session-001"
        assert (
            result["summary"]["analysis_metadata"]["session_id"]
            == "test-session-001"
        )

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, run_kwargs):
        """반환값이 Pydantic 스키마를 만족한다."""
        issues = [_make_issue()]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        IssueList.model_validate(result["issue_list"])
        Checklist.model_validate(result["checklist"])
        Summary.model_validate(result["summary"])


class TestIssueList:
    """IssueList 생성 테스트."""

    @pytest.mark.asyncio
    async def test_issues_sorted_by_severity(self, agent, run_kwargs):
        """이슈가 심각도순으로 정렬된다 (critical → low)."""
        issues = [
            _make_issue(issue_id="JS-001", severity="low"),
            _make_issue(issue_id="JS-002", severity="critical"),
            _make_issue(issue_id="JS-003", severity="high"),
            _make_issue(issue_id="JS-004", severity="medium"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        issue_ids = [i["issue_id"] for i in result["issue_list"]["issues"]]
        assert issue_ids == ["JS-002", "JS-003", "JS-004", "JS-001"]

    @pytest.mark.asyncio
    async def test_by_severity_counts(self, agent, run_kwargs):
        """by_severity 통계가 정확하다."""
        issues = [
            _make_issue(issue_id="JS-001", severity="critical"),
            _make_issue(issue_id="JS-002", severity="critical"),
            _make_issue(issue_id="JS-003", severity="high"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        by_sev = result["issue_list"]["by_severity"]
        assert by_sev["critical"] == 2
        assert by_sev["high"] == 1
        assert by_sev["medium"] == 0
        assert by_sev["low"] == 0

    @pytest.mark.asyncio
    async def test_multi_file_issues_merged(self, agent, run_kwargs):
        """여러 파일의 이슈가 통합된다."""
        results = [
            _make_analysis_result(
                task_id="task_1",
                file="/app/a.js",
                issues=[_make_issue(issue_id="JS-001", file="/app/a.js")],
            ),
            _make_analysis_result(
                task_id="task_2",
                file="/app/b.c",
                language="c",
                issues=[_make_issue(issue_id="C-001", file="/app/b.c",
                                    category="memory_safety")],
            ),
        ]
        result = await agent.run(analysis_results=results, **run_kwargs)

        assert result["issue_list"]["total_issues"] == 2

    @pytest.mark.asyncio
    async def test_same_severity_sorted_by_file(self, agent, run_kwargs):
        """같은 심각도 내에서 파일명순 정렬."""
        results = [
            _make_analysis_result(
                file="/app/z.js",
                issues=[_make_issue(issue_id="JS-001", severity="high",
                                    file="/app/z.js")],
            ),
            _make_analysis_result(
                file="/app/a.js",
                issues=[_make_issue(issue_id="JS-002", severity="high",
                                    file="/app/a.js")],
            ),
        ]
        result = await agent.run(analysis_results=results, **run_kwargs)

        issue_ids = [i["issue_id"] for i in result["issue_list"]["issues"]]
        assert issue_ids == ["JS-002", "JS-001"]


class TestChecklist:
    """Checklist 생성 테스트."""

    @pytest.mark.asyncio
    async def test_only_critical_high_included(self, agent, run_kwargs):
        """critical/high 이슈만 체크리스트에 포함된다."""
        issues = [
            _make_issue(issue_id="JS-001", severity="critical"),
            _make_issue(issue_id="JS-002", severity="high"),
            _make_issue(issue_id="JS-003", severity="medium"),
            _make_issue(issue_id="JS-004", severity="low"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        checklist = result["checklist"]
        assert checklist["total_checks"] > 0

        # 체크리스트 항목의 related_issues에 medium/low 이슈 없음
        all_related = []
        for item in checklist["items"]:
            all_related.extend(item["related_issues"])
        assert "JS-003" not in all_related
        assert "JS-004" not in all_related

    @pytest.mark.asyncio
    async def test_empty_checklist_for_low_issues(self, agent, run_kwargs):
        """medium/low 이슈만 있으면 체크리스트가 비어있다."""
        issues = [
            _make_issue(issue_id="JS-001", severity="medium"),
            _make_issue(issue_id="JS-002", severity="low"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        assert result["checklist"]["total_checks"] == 0
        assert result["checklist"]["items"] == []


class TestRiskAssessment:
    """RiskAssessment 생성 테스트."""

    @pytest.mark.asyncio
    async def test_critical_issues_block_deployment(self, agent, run_kwargs):
        """critical 이슈가 있으면 CRITICAL, 배포 차단."""
        issues = [_make_issue(severity="critical")]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert risk["deployment_risk"] == "CRITICAL"
        assert risk["deployment_allowed"] is False
        assert len(risk["blocking_issues"]) > 0

    @pytest.mark.asyncio
    async def test_high_3_or_more_blocks_deployment(self, agent, run_kwargs):
        """high 이슈 3개 이상이면 HIGH, 배포 차단."""
        issues = [
            _make_issue(issue_id="JS-001", severity="high"),
            _make_issue(issue_id="JS-002", severity="high"),
            _make_issue(issue_id="JS-003", severity="high"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert risk["deployment_risk"] == "HIGH"
        assert risk["deployment_allowed"] is False

    @pytest.mark.asyncio
    async def test_high_1_2_allows_deployment(self, agent, run_kwargs):
        """high 이슈 1-2개면 MEDIUM, 배포 허용."""
        issues = [
            _make_issue(issue_id="JS-001", severity="high"),
            _make_issue(issue_id="JS-002", severity="high"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert risk["deployment_risk"] == "MEDIUM"
        assert risk["deployment_allowed"] is True

    @pytest.mark.asyncio
    async def test_no_critical_high_low_risk(self, agent, run_kwargs):
        """critical/high 이슈 없으면 LOW, 배포 허용."""
        issues = [
            _make_issue(issue_id="JS-001", severity="medium"),
            _make_issue(issue_id="JS-002", severity="low"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert risk["deployment_risk"] == "LOW"
        assert risk["deployment_allowed"] is True
        assert risk["blocking_issues"] == []

    @pytest.mark.asyncio
    async def test_risk_schema_validation(self, agent, run_kwargs):
        """RiskAssessment가 Pydantic 스키마를 만족한다."""
        issues = [_make_issue(severity="critical")]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        RiskAssessment.model_validate(result["summary"]["risk_assessment"])


class TestSummaryStatistics:
    """Summary 통계 테스트."""

    @pytest.mark.asyncio
    async def test_by_category_counts(self, agent, run_kwargs):
        """by_category 통계가 정확하다."""
        issues = [
            _make_issue(issue_id="JS-001", category="security"),
            _make_issue(issue_id="JS-002", category="security"),
            _make_issue(issue_id="C-001", category="memory_safety"),
        ]
        results = [_make_analysis_result(issues=issues)]
        result = await agent.run(analysis_results=results, **run_kwargs)

        by_cat = result["summary"]["issue_summary"]["by_category"]
        assert by_cat["security"] == 2
        assert by_cat["memory_safety"] == 1

    @pytest.mark.asyncio
    async def test_by_language_counts(self, agent, run_kwargs):
        """by_language 통계가 정확하다."""
        results = [
            _make_analysis_result(
                file="/app/a.js",
                language="javascript",
                issues=[_make_issue(issue_id="JS-001", file="/app/a.js")],
            ),
            _make_analysis_result(
                file="/app/b.c",
                language="c",
                issues=[
                    _make_issue(issue_id="C-001", file="/app/b.c"),
                    _make_issue(issue_id="C-002", file="/app/b.c"),
                ],
            ),
        ]
        result = await agent.run(analysis_results=results, **run_kwargs)

        by_lang = result["summary"]["issue_summary"]["by_language"]
        assert by_lang["javascript"] == 1
        assert by_lang["c"] == 2

    @pytest.mark.asyncio
    async def test_by_file_counts(self, agent, run_kwargs):
        """by_file 통계가 정확하다."""
        results = [
            _make_analysis_result(
                file="/app/a.js",
                issues=[
                    _make_issue(issue_id="JS-001", file="/app/a.js"),
                    _make_issue(issue_id="JS-002", file="/app/a.js"),
                ],
            ),
        ]
        result = await agent.run(analysis_results=results, **run_kwargs)

        by_file = result["summary"]["issue_summary"]["by_file"]
        assert by_file["/app/a.js"] == 2

    @pytest.mark.asyncio
    async def test_metadata_fields(self, agent, run_kwargs):
        """analysis_metadata 필드가 정확하다."""
        results = [
            _make_analysis_result(llm_tokens_used=200),
            _make_analysis_result(llm_tokens_used=300),
        ]
        result = await agent.run(analysis_results=results, **run_kwargs)

        meta = result["summary"]["analysis_metadata"]
        assert meta["total_files"] == 3
        assert meta["total_lines"] == 500
        assert meta["analysis_duration_seconds"] == 10.5
        assert meta["total_llm_tokens"] == 500


class TestLLMIntegration:
    """LLM 연동 테스트."""

    @pytest.mark.asyncio
    async def test_llm_risk_description(self, agent, run_kwargs):
        """LLM이 risk_description을 생성한다."""
        agent._llm_client.chat.return_value = _make_llm_response(
            "Critical 이슈 발견. 즉시 수정 필요."
        )
        issues = [_make_issue(severity="critical")]
        results = [_make_analysis_result(issues=issues)]

        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert risk["risk_description"] == "Critical 이슈 발견. 즉시 수정 필요."

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, agent, run_kwargs):
        """LLM 실패 시 기본 risk_description을 사용한다."""
        agent._llm_client.chat.side_effect = Exception("API error")
        issues = [_make_issue(severity="critical")]
        results = [_make_analysis_result(issues=issues)]

        result = await agent.run(analysis_results=results, **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert "Critical" in risk["risk_description"]
        assert risk["deployment_risk"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_llm_invalid_json_graceful(self, agent, run_kwargs):
        """LLM이 잘못된 JSON을 반환하면 기본 메시지 사용."""
        agent._llm_client.chat.return_value = "not json"

        result = await agent.run(analysis_results=[], **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert len(risk["risk_description"]) > 0

    @pytest.mark.asyncio
    async def test_llm_empty_description_fallback(self, agent, run_kwargs):
        """LLM이 빈 description을 반환하면 기본 메시지 사용."""
        agent._llm_client.chat.return_value = json.dumps({
            "summary": {"risk_assessment": {"risk_description": ""}},
        })

        result = await agent.run(analysis_results=[], **run_kwargs)

        risk = result["summary"]["risk_assessment"]
        assert len(risk["risk_description"]) > 0


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 settings.yaml의 reporter 설정값."""
        agent = ReporterAgent()
        assert agent.model == "gpt-4.1-mini"
        assert agent.fallback_model == "gpt-4.1"
        assert agent.temperature == 0.0

    def test_custom_model(self):
        """커스텀 모델 설정."""
        agent = ReporterAgent(model="custom-model", fallback_model="custom-fallback")
        assert agent.model == "custom-model"
        assert agent.fallback_model == "custom-fallback"
