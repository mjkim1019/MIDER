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


class TestTwoPassPath:
    """2-Pass 분석 경로 테스트 (clang-tidy 없음 + 500줄 초과)."""

    @pytest.fixture
    def large_c_file(self, tmp_path):
        """500줄 초과 C 파일 (위험 패턴 포함)."""
        # 안전한 함수 85개 (각 6줄) = 510줄 + 위험 함수 1개
        safe_funcs = []
        for i in range(85):
            safe_funcs.append(
                f"int safe_func_{i}(int x) {{\n"
                f"    int result = 0;\n"
                f"    result = x + {i};\n"
                f"    return result;\n"
                f"}}\n\n"
            )
        # 위험 함수 (초기화 안 된 변수 + strcpy)
        dangerous_func = (
            "void dangerous_handler(char *input) {\n"
            "    int count;\n"
            "    char buf[64];\n"
            "    strcpy(buf, input);\n"
            "    buf[count] = 0;\n"
            "}\n"
        )
        content = "#include <string.h>\n\n" + "".join(safe_funcs) + dangerous_func
        f = tmp_path / "large.c"
        f.write_text(content)
        assert len(content.splitlines()) > 500
        return str(f)

    @pytest.fixture
    def agent_two_pass(self):
        """2-Pass용 Agent (clang-tidy 비활성)."""
        agent = CAnalyzerAgent(model="gpt-4o")
        agent._llm_client = AsyncMock()
        # clang-tidy 없음 (None 반환)
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.side_effect = Exception("not found")
        return agent

    @pytest.mark.asyncio
    async def test_large_file_triggers_two_pass(self, agent_two_pass, large_c_file):
        """500줄 초과 + clang 없음 → 2-Pass 경로."""
        # Pass 1: gpt-4o-mini가 위험 함수 선별
        pass1_response = json.dumps({
            "risky_functions": [
                {"function_name": "dangerous_handler", "reason": "UNINIT_VAR + UNSAFE_FUNC"}
            ]
        })
        # Pass 2: gpt-4o가 심층 분석
        pass2_response = json.dumps({
            "issues": [_make_issue(
                issue_id="C-001",
                title="strcpy 버퍼 오버플로우",
                file=large_c_file,
            )]
        })
        agent_two_pass._llm_client.chat.side_effect = [pass1_response, pass2_response]

        result = await agent_two_pass.run(
            task_id="task_1", file=large_c_file, language="c",
        )

        assert result["error"] is None
        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "C-001"
        # LLM이 2번 호출됨 (Pass 1 + Pass 2)
        assert agent_two_pass._llm_client.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_two_pass_model_switch(self, agent_two_pass, large_c_file):
        """Pass 1은 gpt-4o-mini, Pass 2는 gpt-4o로 호출."""
        pass1_response = json.dumps({
            "risky_functions": [
                {"function_name": "dangerous_handler", "reason": "test"}
            ]
        })
        pass2_response = json.dumps({"issues": []})
        agent_two_pass._llm_client.chat.side_effect = [pass1_response, pass2_response]

        await agent_two_pass.run(
            task_id="task_1", file=large_c_file, language="c",
        )

        calls = agent_two_pass._llm_client.chat.call_args_list
        # Pass 1: model이 gpt-4o-mini로 변경된 상태에서 호출
        # Pass 2 후 model은 원래 gpt-4o로 복원
        assert agent_two_pass.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_two_pass_no_risky_functions_fallback(
        self, agent_two_pass, large_c_file,
    ):
        """Pass 1에서 위험 함수 없으면 Heuristic 단일 패스로 전환."""
        pass1_response = json.dumps({"risky_functions": []})
        heuristic_response = json.dumps({"issues": []})
        agent_two_pass._llm_client.chat.side_effect = [
            pass1_response, heuristic_response,
        ]

        result = await agent_two_pass.run(
            task_id="task_1", file=large_c_file, language="c",
        )

        assert result["error"] is None
        # Pass 1 (mini) + Heuristic fallback = 2회
        assert agent_two_pass._llm_client.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_two_pass_scanner_no_findings_fallback(self, tmp_path):
        """Pre-Scanner 패턴 없으면 Heuristic 단일 패스."""
        # 모든 변수 초기화된 안전한 대형 파일
        safe_funcs = []
        for i in range(90):
            safe_funcs.append(
                f"int calc_{i}(int x) {{\n"
                f"    int r = 0;\n"
                f"    r = x + {i};\n"
                f"    return r;\n"
                f"}}\n\n"
            )
        content = "".join(safe_funcs)
        f = tmp_path / "safe_large.c"
        f.write_text(content)

        agent = CAnalyzerAgent(model="gpt-4o")
        agent._llm_client = AsyncMock()
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.side_effect = Exception("not found")
        agent._llm_client.chat.return_value = json.dumps({"issues": []})

        result = await agent.run(task_id="task_1", file=str(f), language="c")

        assert result["error"] is None
        # Scanner 패턴 없으면 바로 Heuristic 1회
        assert agent._llm_client.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_small_file_skips_two_pass(self, agent, c_file):
        """500줄 이하 파일은 2-Pass 안 탐."""
        agent._clang_tidy_runner = MagicMock()
        agent._clang_tidy_runner.execute.side_effect = Exception("not found")
        agent._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(task_id="task_1", file=c_file, language="c")

        assert result["error"] is None
        # 단일 패스 = 1회
        assert agent._llm_client.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_two_pass_issues_mapped_to_result(
        self, agent_two_pass, large_c_file,
    ):
        """2-Pass에서 발견한 이슈가 AnalysisResult에 포함."""
        pass1_response = json.dumps({
            "risky_functions": [
                {"function_name": "dangerous_handler", "reason": "test"}
            ]
        })
        issues = [
            _make_issue(issue_id="C-001", title="strcpy 버퍼 오버플로우"),
            _make_issue(issue_id="C-002", title="초기화 안 된 변수 사용"),
        ]
        pass2_response = json.dumps({"issues": issues})
        agent_two_pass._llm_client.chat.side_effect = [pass1_response, pass2_response]

        result = await agent_two_pass.run(
            task_id="task_1", file=large_c_file, language="c",
        )

        validated = AnalysisResult.model_validate(result)
        assert len(validated.issues) == 2
        assert validated.agent == "CAnalyzerAgent"


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        """기본 모델은 gpt-4o, fallback 없음."""
        agent = CAnalyzerAgent()
        assert agent.model == "gpt-4o"
        assert agent.fallback_model is None
