"""ProCAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.proc_analyzer import ProCAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult


def _make_issue(
    issue_id: str = "PC-001",
    category: str = "data_integrity",
    severity: str = "critical",
    title: str = "EXEC SQL UPDATE 후 SQLCA 에러 체크 누락",
    file: str = "/app/batch.pc",
    line_start: int = 45,
) -> dict:
    """테스트용 이슈 딕셔너리 생성."""
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "UPDATE 후 sqlca.sqlcode 미검사",
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_start + 2,
        },
        "fix": {
            "before": "EXEC SQL UPDATE ORDERS SET STATUS = :status;",
            "after": "EXEC SQL UPDATE ORDERS SET STATUS = :status;\nif (sqlca.sqlcode != 0) goto error_handler;",
            "description": "SQLCA 에러 체크 추가",
        },
        "source": "llm",
    }


def _make_llm_response(issues: list[dict] | None = None) -> str:
    return json.dumps({"issues": issues or []})


@pytest.fixture
def pc_file(tmp_path):
    """테스트용 Pro*C 파일."""
    f = tmp_path / "batch.pc"
    f.write_text(
        '#include <stdio.h>\n'
        'EXEC SQL INCLUDE sqlca;\n'
        '\n'
        'int main() {\n'
        '    EXEC SQL BEGIN DECLARE SECTION;\n'
        '    int id;\n'
        '    char status[10];\n'
        '    EXEC SQL END DECLARE SECTION;\n'
        '\n'
        '    EXEC SQL SELECT STATUS INTO :status FROM ORDERS WHERE ID = :id;\n'
        '    EXEC SQL UPDATE ORDERS SET STATUS = :status WHERE ID = :id;\n'
        '    EXEC SQL COMMIT;\n'
        '    return 0;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def agent():
    """ProCAnalyzerAgent with mocked LLM."""
    agent = ProCAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_analysis_result(self, agent, pc_file):
        """run()이 AnalysisResult 형식을 반환한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=pc_file, language="proc")

        assert result["task_id"] == "task_1"
        assert result["file"] == pc_file
        assert result["language"] == "proc"
        assert result["agent"] == "ProCAnalyzerAgent"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_run_with_issues(self, agent, pc_file):
        """LLM이 이슈를 반환하면 issues에 포함된다."""
        issue = _make_issue(file=pc_file)
        agent._llm_client.chat.return_value = _make_llm_response([issue])

        result = await agent.run(task_id="task_1", file=pc_file, language="proc")

        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "PC-001"
        assert result["issues"][0]["category"] == "data_integrity"

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, pc_file):
        """반환값이 AnalysisResult 스키마를 만족한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=pc_file, language="proc")

        validated = AnalysisResult.model_validate(result)
        assert validated.agent == "ProCAnalyzerAgent"


class TestErrorFocusedPath:
    """Error-Focused 경로 테스트."""

    @pytest.mark.asyncio
    async def test_proc_errors_trigger_error_focused(self, agent, pc_file):
        """proc 에러가 있으면 Error-Focused 경로."""
        proc_result = ToolResult(
            success=True,
            data={
                "errors": [{"line": 10, "message": "PCC-S-02201", "code": "PCC-S-02201", "file": pc_file, "column": 0}],
                "success": False,
                "total_errors": 1,
            },
        )
        sql_result = ToolResult(
            success=True,
            data={"sql_blocks": [], "total_blocks": 0},
        )
        agent._proc_runner = MagicMock()
        agent._proc_runner.execute.return_value = proc_result
        agent._sql_extractor = MagicMock()
        agent._sql_extractor.execute.return_value = sql_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=pc_file, language="proc")

        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "PCC-S-02201" in prompt

    @pytest.mark.asyncio
    async def test_missing_sqlca_trigger_error_focused(self, agent, pc_file):
        """SQLCA 미검사 SQL 블록이 있으면 Error-Focused 경로."""
        proc_result = ToolResult(
            success=True,
            data={"errors": [], "success": True, "total_errors": 0},
        )
        sql_result = ToolResult(
            success=True,
            data={
                "sql_blocks": [
                    {"id": 0, "sql": "UPDATE ORDERS SET STATUS = :status",
                     "host_variables": ["status"], "indicator_variables": [],
                     "has_sqlca_check": False, "line": 11}
                ],
                "total_blocks": 1,
            },
        )
        agent._proc_runner = MagicMock()
        agent._proc_runner.execute.return_value = proc_result
        agent._sql_extractor = MagicMock()
        agent._sql_extractor.execute.return_value = sql_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=pc_file, language="proc")

        agent._llm_client.chat.assert_called_once()


class TestHeuristicPath:
    """Heuristic 경로 테스트."""

    @pytest.mark.asyncio
    async def test_no_errors_and_sqlca_ok_trigger_heuristic(self, agent, pc_file):
        """에러 없고 SQLCA 체크도 있으면 Heuristic 경로."""
        proc_result = ToolResult(
            success=True,
            data={"errors": [], "success": True, "total_errors": 0},
        )
        sql_result = ToolResult(
            success=True,
            data={
                "sql_blocks": [
                    {"id": 0, "sql": "SELECT 1", "host_variables": [],
                     "indicator_variables": [], "has_sqlca_check": True, "line": 5}
                ],
                "total_blocks": 1,
            },
        )
        agent._proc_runner = MagicMock()
        agent._proc_runner.execute.return_value = proc_result
        agent._sql_extractor = MagicMock()
        agent._sql_extractor.execute.return_value = sql_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=pc_file, language="proc")

        agent._llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_proc_failure_fallback_to_heuristic(self, agent, pc_file):
        """proc 실행 실패 시에도 SQL 블록 추출 후 분석 진행."""
        agent._proc_runner = MagicMock()
        agent._proc_runner.execute.side_effect = Exception("binary not found")
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=pc_file, language="proc")

        assert result["error"] is None


class TestLLMFailure:
    """LLM 실패 시 동작 테스트."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, agent, pc_file):
        """LLM 호출 실패 시 error 필드."""
        agent._llm_client.chat.side_effect = Exception("API error")

        result = await agent.run(task_id="task_1", file=pc_file, language="proc")

        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file="/nonexistent/batch.pc", language="proc",
        )

        assert result["error"] is not None


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 gpt-4o, fallback 없음."""
        agent = ProCAnalyzerAgent()
        assert agent.model == "gpt-4o"
        assert agent.fallback_model is None
