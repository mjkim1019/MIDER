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
        """기본 모델은 settings.yaml의 sql_analyzer 설정값."""
        agent = SQLAnalyzerAgent()
        assert agent.model == "gpt-5"
        assert agent.fallback_model == "gpt-5-mini"

    def test_has_syntax_checker(self):
        """SQLSyntaxChecker가 초기화된다."""
        agent = SQLAnalyzerAgent()
        assert agent._syntax_checker is not None

    def test_has_explain_plan_parser(self):
        """ExplainPlanParser가 초기화된다."""
        agent = SQLAnalyzerAgent()
        assert agent._explain_plan_parser is not None


# --- T24: 정적 이슈 자동 생성 + 병합 테스트 ---


def _make_tuning_point(
    step_id: int = 1,
    operation: str = "TABLE ACCESS FULL",
    obj: str = "ORDERS",
    cost: int = 1500,
    rows: int = 50000,
    severity: str = "high",
    suggestion: str = "Full Table Scan — 인덱스 생성 권장",
) -> dict:
    """테스트용 튜닝 포인트 생성."""
    return {
        "step_id": step_id,
        "operation": operation,
        "object": obj,
        "cost": cost,
        "rows": rows,
        "severity": severity,
        "suggestion": suggestion,
    }


class TestGenerateStaticIssues:
    """_generate_static_issues() 테스트."""

    def test_none_explain_plan_returns_empty(self):
        """explain_plan_data가 None이면 빈 리스트."""
        result = SQLAnalyzerAgent._generate_static_issues(None, "/app/q.sql")
        assert result == []

    def test_no_tuning_points_returns_empty(self):
        """튜닝 포인트가 없으면 빈 리스트."""
        data = {"steps": [], "tuning_points": []}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")
        assert result == []

    def test_medium_severity_ignored(self):
        """medium severity 튜닝 포인트는 무시된다."""
        tp = _make_tuning_point(severity="medium")
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")
        assert result == []

    def test_low_severity_ignored(self):
        """low severity 튜닝 포인트는 무시된다."""
        tp = _make_tuning_point(severity="low")
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")
        assert result == []

    def test_cartesian_generates_critical_issue(self):
        """MERGE JOIN CARTESIAN → critical 이슈 생성."""
        tp = _make_tuning_point(
            operation="MERGE JOIN CARTESIAN",
            obj="",
            severity="critical",
            suggestion="Cartesian Join",
        )
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 1
        assert result[0]["severity"] == "critical"
        assert result[0]["source"] == "static_analysis"
        assert "CARTESIAN" in result[0]["title"]

    def test_pk_index_high_cost_generates_high_issue(self):
        """PK 인덱스 고비용 RANGE SCAN → high 이슈 생성."""
        tp = _make_tuning_point(
            operation="INDEX RANGE SCAN",
            obj="ZORD_WIRE_SVC_DC_PK",
            cost=148,
            severity="high",
            suggestion=(
                "PK 인덱스 사용이지만 Cost=148으로 높음 "
                "— 조인 컬럼 (svc_mgmt_num) 기반 인덱스 힌트 검토: "
                "/*+ INDEX(alias (svc_mgmt_num)) */"
            ),
        )
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 1
        assert result[0]["severity"] == "high"
        assert "ZORD_WIRE_SVC_DC_PK" in result[0]["title"]
        assert "/*+ INDEX(alias (svc_mgmt_num)) */" in result[0]["fix"]["after"]

    def test_table_access_full_generates_high_issue(self):
        """TABLE ACCESS FULL → high 이슈 생성."""
        tp = _make_tuning_point(
            operation="TABLE ACCESS FULL",
            obj="ORDERS",
            cost=1500,
            severity="high",
        )
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 1
        assert result[0]["severity"] == "high"
        assert "ORDERS" in result[0]["title"]
        assert result[0]["source"] == "static_analysis"

    def test_multiple_tuning_points(self):
        """여러 튜닝 포인트가 이슈로 변환된다."""
        tp1 = _make_tuning_point(
            operation="MERGE JOIN CARTESIAN", obj="", severity="critical",
            suggestion="Cartesian Join",
        )
        tp2 = _make_tuning_point(
            step_id=2,
            operation="INDEX RANGE SCAN", obj="TABLE_PK", cost=200,
            severity="high",
            suggestion="PK 인덱스 Cost=200 /*+ INDEX(alias (col)) */",
        )
        tp3 = _make_tuning_point(
            step_id=3, severity="medium",
        )
        data = {"steps": [], "tuning_points": [tp1, tp2, tp3]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 2  # medium은 제외
        assert result[0]["severity"] == "critical"
        assert result[1]["severity"] == "high"

    def test_duplicate_object_dedup(self):
        """같은 object의 중복 이슈는 첫 번째만 유지한다."""
        tp1 = _make_tuning_point(
            step_id=1, operation="MERGE JOIN CARTESIAN", obj="",
            severity="critical", suggestion="Cartesian Join",
        )
        tp2 = _make_tuning_point(
            step_id=2, operation="MERGE JOIN CARTESIAN", obj="",
            severity="critical", suggestion="Cartesian Join",
        )
        data = {"steps": [], "tuning_points": [tp1, tp2]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 1  # 중복 제거

    def test_issue_has_correct_file_path(self):
        """생성된 이슈의 location.file이 올바르다."""
        tp = _make_tuning_point(
            operation="MERGE JOIN CARTESIAN", severity="critical",
            suggestion="Cartesian Join",
        )
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/query.sql")

        assert result[0]["location"]["file"] == "/app/query.sql"

    def test_pk_index_without_hint_in_suggestion(self):
        """suggestion에 /*+ 힌트가 없으면 기본 텍스트를 사용한다."""
        tp = _make_tuning_point(
            operation="INDEX RANGE SCAN",
            obj="MY_TABLE_PK",
            cost=150,
            severity="high",
            suggestion="PK 인덱스 사용이지만 Cost=150으로 높음",
        )
        data = {"steps": [], "tuning_points": [tp]}
        result = SQLAnalyzerAgent._generate_static_issues(data, "/app/q.sql")

        assert len(result) == 1
        assert "조인 컬럼 기반 인덱스 힌트" in result[0]["fix"]["after"]


class TestMergeIssues:
    """_merge_issues() 테스트."""

    def test_empty_static_returns_llm_only(self):
        """정적 이슈가 없으면 LLM 이슈만 반환."""
        llm = [_make_issue(issue_id="SQL-001")]
        result = SQLAnalyzerAgent._merge_issues(llm, [])
        assert len(result) == 1
        assert result[0]["issue_id"] == "SQL-001"

    def test_empty_llm_returns_static_only(self):
        """LLM 이슈가 없으면 정적 이슈만 반환."""
        static = [{
            "issue_id": "SQL-S001",
            "category": "performance",
            "severity": "critical",
            "title": "CARTESIAN",
            "description": "...",
            "location": {"file": "/app/q.sql", "line_start": 0, "line_end": 0},
            "fix": {"before": "MERGE JOIN CARTESIAN", "after": "JOIN 추가", "description": "..."},
            "source": "static_analysis",
        }]
        result = SQLAnalyzerAgent._merge_issues([], static)

        assert len(result) == 1
        assert result[0]["issue_id"] == "SQL-001"  # 재번호
        assert result[0]["source"] == "static_analysis"

    def test_duplicate_object_llm_wins(self):
        """같은 object가 LLM과 정적에 모두 있으면 LLM 이슈만 유지."""
        llm = [_make_issue(
            issue_id="SQL-001",
            title="ZORD_WIRE_SVC_DC 인덱스 문제",
        )]
        static = [{
            "issue_id": "SQL-S001",
            "category": "performance",
            "severity": "high",
            "title": "PK 인덱스 비효율",
            "description": "...",
            "location": {"file": "/app/q.sql", "line_start": 0, "line_end": 0},
            "fix": {
                "before": "INDEX RANGE SCAN (ZORD_WIRE_SVC_DC_PK)",
                "after": "/*+ INDEX(c (svc_mgmt_num)) */",
                "description": "...",
            },
            "source": "static_analysis",
        }]

        # LLM 이슈에 ZORD_WIRE_SVC_DC가 포함되므로 정적 이슈는 중복으로 제거
        result = SQLAnalyzerAgent._merge_issues(llm, static)
        assert len(result) == 1
        assert result[0]["source"] == "hybrid"  # LLM 이슈

    def test_non_duplicate_static_added(self):
        """LLM이 놓친 정적 이슈는 추가된다."""
        llm = [_make_issue(issue_id="SQL-001", title="NVL 함수 인덱스 억제")]
        static = [{
            "issue_id": "SQL-S001",
            "category": "performance",
            "severity": "critical",
            "title": "MERGE JOIN CARTESIAN",
            "description": "...",
            "location": {"file": "/app/q.sql", "line_start": 0, "line_end": 0},
            "fix": {
                "before": "MERGE JOIN CARTESIAN",
                "after": "JOIN 조건 추가",
                "description": "...",
            },
            "source": "static_analysis",
        }]

        result = SQLAnalyzerAgent._merge_issues(llm, static)
        assert len(result) == 2
        # LLM 이슈가 먼저
        assert result[0]["title"] == "NVL 함수 인덱스 억제"
        # 정적 이슈가 추가됨
        assert result[1]["source"] == "static_analysis"

    def test_issue_id_renumbered(self):
        """병합 후 issue_id가 SQL-001부터 순차 재번호된다."""
        llm = [
            _make_issue(issue_id="SQL-001"),
            _make_issue(issue_id="SQL-002", title="서브쿼리 문제"),
        ]
        static = [{
            "issue_id": "SQL-S001",
            "category": "performance",
            "severity": "critical",
            "title": "CARTESIAN",
            "description": "...",
            "location": {"file": "/app/q.sql", "line_start": 0, "line_end": 0},
            "fix": {"before": "MERGE JOIN CARTESIAN", "after": "...", "description": "..."},
            "source": "static_analysis",
        }]

        result = SQLAnalyzerAgent._merge_issues(llm, static)
        ids = [r["issue_id"] for r in result]
        assert ids == ["SQL-001", "SQL-002", "SQL-003"]

    def test_both_empty(self):
        """양쪽 모두 비어있으면 빈 리스트."""
        result = SQLAnalyzerAgent._merge_issues([], [])
        assert result == []

    def test_pk_index_not_in_llm_gets_added(self):
        """LLM이 PK 인덱스 이슈를 놓치면 정적 이슈가 추가된다.

        이슈 #004의 핵심 시나리오: CLI 실행 시 LLM이 인덱스 힌트를 누락해도
        정적 이슈가 보충하여 항상 포함되도록 보장한다.
        """
        # LLM이 NVL, LIKE 이슈만 잡고 PK 인덱스 이슈는 놓침
        llm = [
            _make_issue(issue_id="SQL-001", title="NVL 함수 인덱스 억제"),
            _make_issue(issue_id="SQL-002", title="LIKE 선행 와일드카드"),
        ]
        # 정적 분석에서 PK 인덱스 이슈 탐지
        static = [{
            "issue_id": "SQL-S001",
            "category": "performance",
            "severity": "high",
            "title": "PK 인덱스 비효율 — ZORD_WIRE_SVC_DC_PK",
            "description": "Cost=148으로 높음",
            "location": {"file": "/app/q.sql", "line_start": 0, "line_end": 0},
            "fix": {
                "before": "INDEX RANGE SCAN (ZORD_WIRE_SVC_DC_PK)",
                "after": "/*+ INDEX(alias (svc_mgmt_num)) */",
                "description": "조인 컬럼 기반 인덱스 힌트",
            },
            "source": "static_analysis",
        }]

        result = SQLAnalyzerAgent._merge_issues(llm, static)

        # PK 인덱스 이슈가 추가됨 (LLM 이슈에 ZORD_WIRE_SVC_DC 언급 없으므로)
        assert len(result) == 3
        pk_issues = [r for r in result if "ZORD_WIRE_SVC_DC_PK" in r.get("fix", {}).get("before", "")]
        assert len(pk_issues) == 1
        assert pk_issues[0]["source"] == "static_analysis"


class TestRunWithStaticIssues:
    """run() 메서드에서 정적 이슈가 올바르게 병합되는지 테스트."""

    @pytest.fixture
    def explain_plan_with_cartesian(self, tmp_path):
        """CARTESIAN이 포함된 Explain Plan 파일."""
        f = tmp_path / "explain.txt"
        f.write_text(
            "---------------------------------------------------------------------------\n"
            "| Id  | Operation                | Name   | Rows  | Bytes | Cost (%CPU)| Time     |\n"
            "---------------------------------------------------------------------------\n"
            "|   0 | SELECT STATEMENT         |        |     1 |    50 |    10  (1) | 00:00:01 |\n"
            "|   1 |  MERGE JOIN CARTESIAN    |        |     1 |    50 |    10  (1) | 00:00:01 |\n"
            "---------------------------------------------------------------------------\n"
        )
        return str(f)

    @pytest.mark.asyncio
    async def test_static_issues_merged_when_llm_misses(
        self, agent, sql_file, explain_plan_with_cartesian,
    ):
        """LLM이 이슈를 놓쳐도 정적 이슈가 보충된다."""
        # LLM은 빈 이슈 반환 (놓침)
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(
            task_id="task_1",
            file=sql_file,
            language="sql",
            explain_plan_file=explain_plan_with_cartesian,
        )

        # 정적 이슈가 추가됨
        assert len(result["issues"]) >= 1
        has_cartesian = any(
            "CARTESIAN" in (i.get("title", "") + i.get("description", ""))
            for i in result["issues"]
        )
        assert has_cartesian

    @pytest.mark.asyncio
    async def test_no_explain_plan_no_static_issues(self, agent, sql_file):
        """Explain Plan이 없으면 정적 이슈 없이 LLM 이슈만."""
        llm_issue = _make_issue(file=sql_file)
        agent._llm_client.chat.return_value = _make_llm_response([llm_issue])

        result = await agent.run(
            task_id="task_1", file=sql_file, language="sql",
        )

        assert len(result["issues"]) == 1
        assert result["issues"][0]["source"] == "hybrid"
