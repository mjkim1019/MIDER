"""SQLAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.sql_analyzer import SQLAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult


def _make_issue(
    issue_id: str = "SQL-001",
    category: str = "performance",
    severity: str = "high",
    title: str = "WHERE 절 컬럼에 함수 적용으로 인덱스 억제",
    file: str = "/app/query.sql",
    line_start: int = 15,
) -> dict:
    """테스트용 이슈 딕셔너리 생성."""
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "YEAR() 함수가 인덱스 사용을 방해합니다.",
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_start + 2,
        },
        "fix": {
            "before": "WHERE YEAR(order_date) = 2026",
            "after": "WHERE order_date >= '2026-01-01'\n  AND order_date < '2027-01-01'",
            "description": "범위 조건으로 변환",
        },
        "source": "hybrid",
    }


def _make_llm_response(issues: list[dict] | None = None) -> str:
    return json.dumps({"issues": issues or []})


@pytest.fixture
def sql_file(tmp_path):
    """테스트용 SQL 파일."""
    f = tmp_path / "orders.sql"
    f.write_text(
        "SELECT * FROM orders\n"
        "WHERE YEAR(order_date) = 2026\n"
        "AND status LIKE '%active%'\n"
        "ORDER BY id;\n"
    )
    return str(f)


@pytest.fixture
def clean_sql_file(tmp_path):
    """패턴이 없는 SQL 파일."""
    f = tmp_path / "clean.sql"
    f.write_text(
        "SELECT id, name, status\n"
        "FROM orders\n"
        "WHERE id = 123;\n"
    )
    return str(f)


@pytest.fixture
def agent():
    """SQLAnalyzerAgent with mocked LLM."""
    agent = SQLAnalyzerAgent(model="gpt-4o-mini")
    agent._llm_client = AsyncMock()
    return agent


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_analysis_result(self, agent, sql_file):
        """run()이 AnalysisResult 형식을 반환한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert result["task_id"] == "task_1"
        assert result["file"] == sql_file
        assert result["language"] == "sql"
        assert result["agent"] == "SQLAnalyzerAgent"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_run_with_issues(self, agent, sql_file):
        """LLM이 이슈를 반환하면 issues에 포함된다."""
        issue = _make_issue(file=sql_file)
        agent._llm_client.chat.return_value = _make_llm_response([issue])

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "SQL-001"
        assert result["issues"][0]["category"] == "performance"

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, sql_file):
        """반환값이 AnalysisResult 스키마를 만족한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        validated = AnalysisResult.model_validate(result)
        assert validated.agent == "SQLAnalyzerAgent"


class TestErrorFocusedPath:
    """Error-Focused 경로 테스트."""

    @pytest.mark.asyncio
    async def test_patterns_found_trigger_error_focused(self, agent, sql_file):
        """정적 패턴이 발견되면 Error-Focused 경로."""
        agent._llm_client.chat.return_value = _make_llm_response()

        # sql_file에는 SELECT *, YEAR(), LIKE '%...' 패턴이 있음
        await agent.run(task_id="task_1", file=sql_file, language="sql")

        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        # Error-Focused 프롬프트에는 정적 패턴 결과가 포함됨
        assert "select_star" in prompt or "function_in_where" in prompt or "like_wildcard" in prompt


class TestHeuristicPath:
    """Heuristic 경로 테스트."""

    @pytest.mark.asyncio
    async def test_no_patterns_trigger_heuristic(self, agent, clean_sql_file):
        """정적 패턴 없으면 Heuristic 경로."""
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=clean_sql_file, language="sql")

        agent._llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_ast_grep_failure_fallback_to_heuristic(self, agent, sql_file):
        """AstGrepSearch 실패 시 Heuristic으로 전환."""
        agent._ast_grep = MagicMock()
        agent._ast_grep.execute.side_effect = Exception("pattern error")
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert result["error"] is None


class TestPatternSearch:
    """정적 패턴 검색 테스트."""

    def test_search_patterns_finds_select_star(self, agent, sql_file):
        """SELECT * 패턴을 탐지한다."""
        patterns = agent._search_patterns(sql_file)

        pattern_names = [p["pattern"] for p in patterns]
        assert "select_star" in pattern_names

    def test_search_patterns_finds_function_in_where(self, agent, sql_file):
        """WHERE 절 함수 패턴을 탐지한다."""
        patterns = agent._search_patterns(sql_file)

        pattern_names = [p["pattern"] for p in patterns]
        assert "function_in_where" in pattern_names

    def test_search_patterns_finds_like_wildcard(self, agent, sql_file):
        """LIKE '%...' 패턴을 탐지한다."""
        patterns = agent._search_patterns(sql_file)

        pattern_names = [p["pattern"] for p in patterns]
        assert "like_wildcard" in pattern_names

    def test_search_patterns_clean_file_returns_empty(self, agent, clean_sql_file):
        """패턴 없는 파일은 빈 리스트 반환."""
        patterns = agent._search_patterns(clean_sql_file)

        assert len(patterns) == 0


class TestLLMFailure:
    """LLM 실패 시 동작 테스트."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, agent, sql_file):
        """LLM 호출 실패 시 error 필드."""
        agent._llm_client.chat.side_effect = Exception("API error")

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json(self, agent, sql_file):
        """LLM이 잘못된 JSON을 반환하면 error."""
        agent._llm_client.chat.return_value = "not json"

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file="/nonexistent/query.sql", language="sql",
        )

        assert result["error"] is not None


class TestFileContext:
    """file_context 전달 테스트."""

    @pytest.mark.asyncio
    async def test_file_context_in_error_focused(self, agent, sql_file):
        """Error-Focused 경로에서 file_context가 프롬프트에 포함된다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        ctx = {"file": sql_file, "imports": [], "calls": []}
        await agent.run(
            task_id="task_1", file=sql_file, language="sql",
            file_context=ctx,
        )

        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "imports" in prompt


class TestExplainPlan:
    """Explain Plan 연동 테스트."""

    @pytest.fixture
    def explain_plan_file(self, tmp_path):
        """테스트용 Explain Plan 파일."""
        f = tmp_path / "explain.txt"
        f.write_text(
            "---------------------------------------------------------------------------\n"
            "| Id  | Operation          | Name   | Rows  | Bytes | Cost (%CPU)| Time     |\n"
            "---------------------------------------------------------------------------\n"
            "|   0 | SELECT STATEMENT   |        |    50 |  2400 |   234  (1) | 00:00:01 |\n"
            "|   1 |  TABLE ACCESS FULL | ORDERS |    50 |  2400 |   234  (1) | 00:00:01 |\n"
            "---------------------------------------------------------------------------\n"
        )
        return str(f)

    @pytest.mark.asyncio
    async def test_explain_plan_file_passed_to_prompt(self, agent, sql_file, explain_plan_file):
        """explain_plan_file이 프롬프트에 포함된다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(
            task_id="task_1", file=sql_file, language="sql",
            explain_plan_file=explain_plan_file,
        )

        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "TABLE ACCESS FULL" in prompt

    @pytest.mark.asyncio
    async def test_explain_plan_none_no_error(self, agent, sql_file):
        """explain_plan_file=None이면 정상 동작."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=sql_file, language="sql",
            explain_plan_file=None,
        )

        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_explain_plan_invalid_file_no_crash(self, agent, sql_file):
        """존재하지 않는 explain_plan_file이면 무시하고 계속."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=sql_file, language="sql",
            explain_plan_file="/nonexistent/explain.txt",
        )

        assert result["error"] is None


class TestSyntaxCheck:
    """SQL 문법 검증 연동 테스트."""

    @pytest.mark.asyncio
    async def test_syntax_errors_trigger_error_focused(self, agent, tmp_path):
        """문법 오류가 있으면 Error-Focused 경로."""
        f = tmp_path / "bad.sql"
        f.write_text("SELECT COUNT( FROM orders;\n")
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=str(f), language="sql")

        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        # 문법 오류가 Error-Focused 프롬프트에 포함
        assert "unmatched_paren" in prompt or "괄호" in prompt

    @pytest.mark.asyncio
    async def test_syntax_checker_failure_graceful(self, agent, sql_file):
        """문법 검증 도구 실패 시 빈 결과로 계속."""
        agent._syntax_checker.execute = MagicMock(side_effect=Exception("checker fail"))
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=sql_file, language="sql")

        assert result["error"] is None


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 gpt-4o-mini, fallback gpt-4o."""
        agent = SQLAnalyzerAgent()
        assert agent.model == "gpt-4o-mini"
        assert agent.fallback_model == "gpt-4o"

    def test_has_syntax_checker(self):
        """SQLSyntaxChecker가 초기화된다."""
        agent = SQLAnalyzerAgent()
        assert agent._syntax_checker is not None

    def test_has_explain_plan_parser(self):
        """ExplainPlanParser가 초기화된다."""
        agent = SQLAnalyzerAgent()
        assert agent._explain_plan_parser is not None
