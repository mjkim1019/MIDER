"""CAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.c_analyzer import CAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult


def _make_issue(
    issue_id: str = "C-001",
    category: str = "memory_safety",
    severity: str = "critical",
    title: str = "strcpy 사용으로 버퍼 오버플로우 위험",
    file: str = "/app/calc.c",
    line_start: int = 23,
) -> dict:
    """테스트용 이슈 딕셔너리 생성."""
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "strcpy는 대상 버퍼 크기를 검증하지 않습니다.",
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_start,
        },
        "fix": {
            "before": "strcpy(dest, src);",
            "after": "strncpy(dest, src, sizeof(dest) - 1);",
            "description": "strncpy 사용",
        },
        "source": "hybrid",
        "static_tool": "clang-tidy",
        "static_rule": "bugprone-not-null-terminated-result",
    }


def _make_llm_response(issues: list[dict] | None = None) -> str:
    return json.dumps({"issues": issues or []})


@pytest.fixture
def c_file(tmp_path):
    """테스트용 C 파일."""
    f = tmp_path / "calc.c"
    f.write_text(
        '#include <stdio.h>\n'
        '#include <string.h>\n'
        'int main() {\n'
        '    char buf[10];\n'
        '    strcpy(buf, "hello world");\n'
        '    return 0;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def agent():
    """CAnalyzerAgent with mocked LLM."""
    agent = CAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_analysis_result(self, agent, c_file):
        """run()이 AnalysisResult 형식을 반환한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert result["task_id"] == "task_1"
        assert result["file"] == c_file
        assert result["language"] == "c"
        assert result["agent"] == "CAnalyzerAgent"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_run_with_issues(self, agent, c_file):
        """LLM이 이슈를 반환하면 issues에 포함된다."""
        issue = _make_issue(file=c_file)
        agent._llm_client.chat.return_value = _make_llm_response([issue])

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "C-001"
        assert result["issues"][0]["category"] == "memory_safety"

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, c_file):
        """반환값이 AnalysisResult 스키마를 만족한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        validated = AnalysisResult.model_validate(result)
        assert validated.agent == "CAnalyzerAgent"


class TestErrorFocusedPath:
    """Error-Focused 경로 테스트."""

    @pytest.mark.asyncio
    async def test_clang_warnings_trigger_error_focused(self, agent, c_file):
        """clang-tidy 경고가 있으면 Error-Focused 프롬프트."""
        clang_result = ToolResult(
            success=True,
            data={
                "warnings": [
                    {"check": "bugprone-strcpy", "message": "test",
                     "line": 5, "column": 5, "severity": "warning",
                     "file": c_file}
                ],
                "total_warnings": 1,
            },
        )
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.return_value = clang_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=c_file, language="c")

        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "bugprone-strcpy" in prompt


class TestHeuristicPath:
    """Heuristic 경로 테스트."""

    @pytest.mark.asyncio
    async def test_no_clang_warnings_trigger_heuristic(self, agent, c_file):
        """clang-tidy 경고 없으면 Heuristic 경로."""
        clang_result = ToolResult(
            success=True,
            data={"warnings": [], "total_warnings": 0},
        )
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.return_value = clang_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=c_file, language="c")

        agent._llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_clang_failure_fallback_to_heuristic(self, agent, c_file):
        """clang-tidy 실행 실패 시 Heuristic으로 전환."""
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.side_effect = Exception("binary not found")
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert result["error"] is None


class TestLLMFailure:
    """LLM 실패 시 동작 테스트."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, agent, c_file):
        """LLM 호출 실패 시 error 필드."""
        agent._llm_client.chat.side_effect = Exception("API error")

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json(self, agent, c_file):
        """LLM이 잘못된 JSON을 반환하면 error."""
        agent._llm_client.chat.return_value = "not json"

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file="/nonexistent/calc.c", language="c",
        )

        assert result["error"] is not None


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 gpt-4o, fallback 없음."""
        agent = CAnalyzerAgent()
        assert agent.model == "gpt-4o"
        assert agent.fallback_model is None
