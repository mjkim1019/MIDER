"""JavaScriptAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.js_analyzer import JavaScriptAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult


def _make_issue(
    issue_id: str = "JS-001",
    category: str = "security",
    severity: str = "critical",
    title: str = "XSS 취약점",
    description: str = "innerHTML에 사용자 입력 직접 대입",
    file: str = "/app/test.js",
    line_start: int = 10,
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
        "source": "llm",
    }


def _make_llm_response(issues: list[dict] | None = None) -> str:
    """LLM 응답 JSON 문자열 생성."""
    return json.dumps({"issues": issues or []})


@pytest.fixture
def js_file(tmp_path):
    """테스트용 JS 파일."""
    f = tmp_path / "app.js"
    f.write_text(
        "const el = document.getElementById('output');\n"
        "el.innerHTML = userInput;\n"
        "console.log('done');\n"
    )
    return str(f)


@pytest.fixture
def agent():
    """JavaScriptAnalyzerAgent with mocked LLM."""
    agent = JavaScriptAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_analysis_result(self, agent, js_file):
        """run()이 AnalysisResult 형식을 반환한다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        # ESLint 바이너리 없으므로 Heuristic 경로
        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["task_id"] == "task_1"
        assert result["file"] == js_file
        assert result["language"] == "javascript"
        assert result["agent"] == "JavaScriptAnalyzerAgent"
        assert isinstance(result["issues"], list)
        assert isinstance(result["analysis_time_seconds"], float)
        assert isinstance(result["llm_tokens_used"], int)
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_run_with_issues(self, agent, js_file):
        """LLM이 이슈를 반환하면 issues에 포함된다."""
        issue = _make_issue(file=js_file)
        agent._llm_client.chat.return_value = _make_llm_response([issue])

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "JS-001"
        assert result["issues"][0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_run_empty_issues(self, agent, js_file):
        """LLM이 빈 이슈를 반환하면 빈 리스트."""
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["issues"] == []
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error 필드에 메시지."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file="/nonexistent/file.js", language="javascript",
        )

        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, js_file):
        """반환값이 AnalysisResult 스키마를 만족한다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        validated = AnalysisResult.model_validate(result)
        assert validated.task_id == "task_1"


class TestErrorFocusedPath:
    """Error-Focused 경로 테스트."""

    @pytest.mark.asyncio
    async def test_eslint_errors_trigger_error_focused(self, agent, js_file):
        """ESLint 에러가 있으면 Error-Focused 프롬프트 사용."""
        eslint_result = ToolResult(
            success=True,
            data={
                "errors": [{"rule": "no-undef", "message": "test", "line": 1}],
                "warnings": [],
                "total_errors": 1,
                "total_warnings": 0,
            },
        )
        agent._eslint_runner = MagicMock()
        agent._eslint_runner.execute.return_value = eslint_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        # LLM이 호출됨
        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        prompt = call_args.kwargs.get("messages", call_args[1]["messages"] if len(call_args) > 1 else call_args[0][1])[1]["content"]
        # Error-Focused 프롬프트에는 ESLint 결과가 포함됨
        assert "no-undef" in prompt

    @pytest.mark.asyncio
    async def test_eslint_warnings_trigger_error_focused(self, agent, js_file):
        """ESLint 경고만 있어도 Error-Focused 경로."""
        eslint_result = ToolResult(
            success=True,
            data={
                "errors": [],
                "warnings": [{"rule": "no-unused-vars", "message": "test", "line": 1}],
                "total_errors": 0,
                "total_warnings": 1,
            },
        )
        agent._eslint_runner = MagicMock()
        agent._eslint_runner.execute.return_value = eslint_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        agent._llm_client.chat.assert_called_once()


class TestHeuristicPath:
    """Heuristic 경로 테스트."""

    @pytest.mark.asyncio
    async def test_no_eslint_errors_trigger_heuristic(self, agent, js_file):
        """ESLint 에러 없으면 Heuristic 프롬프트 사용."""
        eslint_result = ToolResult(
            success=True,
            data={
                "errors": [], "warnings": [],
                "total_errors": 0, "total_warnings": 0,
            },
        )
        agent._eslint_runner = MagicMock()
        agent._eslint_runner.execute.return_value = eslint_result
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        agent._llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_eslint_failure_fallback_to_heuristic(self, agent, js_file):
        """ESLint 실행 실패 시 Heuristic으로 전환."""
        agent._eslint_runner = MagicMock()
        agent._eslint_runner.execute.side_effect = Exception("binary not found")
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["error"] is None
        agent._llm_client.chat.assert_called_once()


class TestLLMFailure:
    """LLM 실패 시 동작 테스트."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, agent, js_file):
        """LLM 호출 실패 시 error 필드에 메시지."""
        agent._llm_client.chat.side_effect = Exception("API error")

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["error"] is not None
        assert "API error" in result["error"]
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json(self, agent, js_file):
        """LLM이 잘못된 JSON을 반환하면 error."""
        agent._llm_client.chat.return_value = "not json"

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_non_dict_response(self, agent, js_file):
        """LLM이 list를 반환하면 error."""
        agent._llm_client.chat.return_value = json.dumps([1, 2, 3])

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
        )

        assert result["error"] is not None


class TestFileContext:
    """file_context 전달 테스트."""

    @pytest.mark.asyncio
    async def test_file_context_included_in_prompt(self, agent, js_file):
        """file_context가 프롬프트에 포함된다."""
        eslint_result = ToolResult(
            success=True,
            data={
                "errors": [{"rule": "test", "message": "test", "line": 1}],
                "warnings": [],
                "total_errors": 1, "total_warnings": 0,
            },
        )
        agent._eslint_runner = MagicMock()
        agent._eslint_runner.execute.return_value = eslint_result
        agent._llm_client.chat.return_value = _make_llm_response()

        ctx = {"file": js_file, "imports": [], "calls": []}
        await agent.run(
            task_id="task_1", file=js_file, language="javascript",
            file_context=ctx,
        )

        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "imports" in prompt

    @pytest.mark.asyncio
    async def test_no_file_context(self, agent, js_file):
        """file_context 없어도 정상 동작."""
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=js_file, language="javascript",
            file_context=None,
        )

        assert result["error"] is None


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 gpt-4o."""
        agent = JavaScriptAnalyzerAgent()
        assert agent.model == "gpt-4o"
        assert agent.fallback_model is None

    def test_custom_model(self):
        """커스텀 모델 설정."""
        agent = JavaScriptAnalyzerAgent(model="gpt-4o-mini", fallback_model="gpt-4o")
        assert agent.model == "gpt-4o-mini"
        assert agent.fallback_model == "gpt-4o"
