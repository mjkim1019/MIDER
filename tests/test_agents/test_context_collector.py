"""ContextCollectorAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock

import pytest

from mider.agents.context_collector import ContextCollectorAgent
from mider.models.file_context import FileContext


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────


@pytest.fixture
def c_file(tmp_path):
    """테스트용 C 파일."""
    f = tmp_path / "calc.c"
    f.write_text(
        '#include <stdio.h>\n'
        '#include "common.h"\n'
        '\n'
        'int calculate(int a, int b) {\n'
        '    char *buf = malloc(256);\n'
        '    if (buf == NULL) {\n'
        '        log_error("malloc failed");\n'
        '        return -1;\n'
        '    }\n'
        '    printf("result: %d\\n", a + b);\n'
        '    free(buf);\n'
        '    return a + b;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def h_file(tmp_path):
    """테스트용 헤더 파일."""
    f = tmp_path / "common.h"
    f.write_text(
        "#ifndef COMMON_H\n"
        "#define COMMON_H\n"
        "int calculate(int, int);\n"
        "void log_error(const char *);\n"
        "#endif\n"
    )
    return str(f)


@pytest.fixture
def js_file(tmp_path):
    """테스트용 JS 파일."""
    f = tmp_path / "app.js"
    f.write_text(
        "import { utils } from './utils';\n"
        "const http = require('http');\n"
        "\n"
        "function handleRequest(req, res) {\n"
        "    try {\n"
        "        const data = utils.parse(req.body);\n"
        "        console.log('parsed:', data);\n"
        "    } catch (err) {\n"
        "        console.error('Error:', err);\n"
        "    }\n"
        "}\n"
    )
    return str(f)


@pytest.fixture
def sql_file(tmp_path):
    """테스트용 SQL 파일."""
    f = tmp_path / "query.sql"
    f.write_text(
        "SELECT * FROM orders WHERE id = 1;\n"
        "COMMIT;\n"
    )
    return str(f)


@pytest.fixture
def pc_file(tmp_path):
    """테스트용 Pro*C 파일."""
    f = tmp_path / "batch.pc"
    f.write_text(
        'EXEC SQL INCLUDE sqlca;\n'
        '#include "common.h"\n'
        '\n'
        'void process() {\n'
        '    EXEC SQL SELECT 1 INTO :val FROM DUAL;\n'
        '    if (sqlca.sqlcode != 0) {\n'
        '        log_error("SQL error");\n'
        '    }\n'
        '    EXEC SQL COMMIT;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def agent():
    """ContextCollectorAgent with mocked LLM."""
    agent = ContextCollectorAgent(model="gpt-4o-mini")
    mock_client = AsyncMock()
    agent._llm_client = mock_client
    return agent


def _make_execution_plan(files_with_lang: list[tuple[str, str]]) -> dict:
    """테스트용 ExecutionPlan dict 생성."""
    sub_tasks = []
    for idx, (file_path, lang) in enumerate(files_with_lang, start=1):
        sub_tasks.append({
            "task_id": f"task_{idx}",
            "file": file_path,
            "language": lang,
            "priority": idx,
            "metadata": {
                "file_size": 100,
                "line_count": 10,
                "last_modified": "2026-03-01T00:00:00",
            },
        })
    return {
        "sub_tasks": sub_tasks,
        "dependencies": {
            "edges": [],
            "has_circular": False,
            "warnings": [],
        },
        "total_files": len(sub_tasks),
        "estimated_time_seconds": len(sub_tasks) * 30,
    }


def _make_llm_response(file_contexts: list[dict]) -> str:
    """LLM 응답 JSON 문자열 생성."""
    return json.dumps({
        "file_contexts": file_contexts,
        "dependencies": {"edges": [], "has_circular": False, "warnings": []},
        "common_patterns": {
            "error_handling": 0,
            "logging": 0,
            "transaction": 0,
            "memory_management": 0,
        },
    })


# ──────────────────────────────────────────────
# 기본 동작 테스트
# ──────────────────────────────────────────────


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_empty_execution_plan(self, agent):
        """빈 ExecutionPlan → 빈 FileContext 반환."""
        plan = {"sub_tasks": [], "dependencies": {}, "total_files": 0}
        result = await agent.run(execution_plan=plan)

        ctx = FileContext.model_validate(result)
        assert ctx.file_contexts == []
        assert ctx.common_patterns == {}

    @pytest.mark.asyncio
    async def test_single_c_file(self, agent, c_file):
        """단일 C 파일 컨텍스트 수집."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        assert len(ctx.file_contexts) == 1
        fc = ctx.file_contexts[0]
        assert fc.file == c_file
        assert fc.language == "c"

    @pytest.mark.asyncio
    async def test_multiple_files(self, agent, c_file, js_file, sql_file):
        """여러 언어 파일 컨텍스트 수집."""
        plan = _make_execution_plan([
            (c_file, "c"),
            (js_file, "javascript"),
            (sql_file, "sql"),
        ])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        assert len(ctx.file_contexts) == 3
        languages = {fc.language for fc in ctx.file_contexts}
        assert languages == {"c", "javascript", "sql"}


# ──────────────────────────────────────────────
# Import/Include 추출 테스트
# ──────────────────────────────────────────────


class TestImportExtraction:
    """Import/Include 추출 테스트."""

    @pytest.mark.asyncio
    async def test_c_include_extraction(self, agent, c_file, h_file):
        """C 파일의 #include 추출."""
        plan = _make_execution_plan([(c_file, "c"), (h_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        c_ctx = next(fc for fc in ctx.file_contexts if fc.file == c_file)
        assert len(c_ctx.imports) == 2

        # stdio.h → 외부
        stdio_import = next(
            i for i in c_ctx.imports if "stdio.h" in i.statement
        )
        assert stdio_import.is_external is True
        assert stdio_import.resolved_path is None

        # common.h → 내부 (분석 대상 파일에 매칭)
        common_import = next(
            i for i in c_ctx.imports if "common.h" in i.statement
        )
        assert common_import.is_external is False
        assert common_import.resolved_path == h_file

    @pytest.mark.asyncio
    async def test_js_import_extraction(self, agent, js_file):
        """JS 파일의 import/require 추출."""
        plan = _make_execution_plan([(js_file, "javascript")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        js_ctx = ctx.file_contexts[0]
        assert len(js_ctx.imports) == 2

        # ./utils → 내부 (상대경로)
        utils_import = next(
            i for i in js_ctx.imports if "utils" in i.statement
        )
        assert utils_import.is_external is False

        # http → 외부
        http_import = next(
            i for i in js_ctx.imports if "http" in i.statement
        )
        assert http_import.is_external is True

    @pytest.mark.asyncio
    async def test_proc_include_extraction(self, agent, pc_file, h_file):
        """Pro*C 파일의 EXEC SQL INCLUDE + #include 추출."""
        plan = _make_execution_plan([(pc_file, "proc"), (h_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        pc_ctx = next(fc for fc in ctx.file_contexts if fc.file == pc_file)
        assert len(pc_ctx.imports) == 2

        # sqlca → Oracle 내장 (외부)
        sqlca_import = next(
            i for i in pc_ctx.imports if "sqlca" in i.statement.lower()
        )
        assert sqlca_import.is_external is True

    @pytest.mark.asyncio
    async def test_sql_no_imports(self, agent, sql_file):
        """SQL 파일은 import 없음."""
        plan = _make_execution_plan([(sql_file, "sql")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        sql_ctx = ctx.file_contexts[0]
        assert sql_ctx.imports == []


# ──────────────────────────────────────────────
# 함수 호출 추출 테스트
# ──────────────────────────────────────────────


class TestCallExtraction:
    """함수 호출 추출 테스트."""

    @pytest.mark.asyncio
    async def test_c_function_calls(self, agent, c_file):
        """C 파일의 함수 호출 추출."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        c_ctx = ctx.file_contexts[0]
        call_names = {c.function_name for c in c_ctx.calls}
        assert "malloc" in call_names
        assert "log_error" in call_names
        assert "printf" in call_names
        assert "free" in call_names

    @pytest.mark.asyncio
    async def test_js_function_calls(self, agent, js_file):
        """JS 파일의 함수 호출 추출."""
        plan = _make_execution_plan([(js_file, "javascript")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        js_ctx = ctx.file_contexts[0]
        call_names = {c.function_name for c in js_ctx.calls}
        # handleRequest는 함수 정의이지만 regex로는 호출처럼 보일 수 있음
        # 핵심은 키워드(if, for 등)가 제외되는 것
        assert "if" not in call_names
        assert "for" not in call_names

    @pytest.mark.asyncio
    async def test_sql_no_calls(self, agent, sql_file):
        """SQL 파일은 함수 호출 없음."""
        plan = _make_execution_plan([(sql_file, "sql")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)
        assert ctx.file_contexts[0].calls == []


# ──────────────────────────────────────────────
# 패턴 탐지 테스트
# ──────────────────────────────────────────────


class TestPatternDetection:
    """코드 패턴 탐지 테스트."""

    @pytest.mark.asyncio
    async def test_c_patterns_detected(self, agent, c_file):
        """C 파일의 에러 처리, 로깅, 메모리 관리 패턴 탐지."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        c_ctx = ctx.file_contexts[0]
        pattern_types = {p.pattern_type for p in c_ctx.patterns}
        assert "error_handling" in pattern_types  # if (buf == NULL)
        assert "logging" in pattern_types  # printf
        assert "memory_management" in pattern_types  # malloc, free

    @pytest.mark.asyncio
    async def test_proc_patterns_detected(self, agent, pc_file):
        """Pro*C 파일의 SQLCA 체크, 트랜잭션 패턴 탐지."""
        plan = _make_execution_plan([(pc_file, "proc")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        pc_ctx = ctx.file_contexts[0]
        pattern_types = {p.pattern_type for p in pc_ctx.patterns}
        assert "error_handling" in pattern_types  # sqlca.sqlcode
        assert "transaction" in pattern_types  # EXEC SQL COMMIT

    @pytest.mark.asyncio
    async def test_js_patterns_detected(self, agent, js_file):
        """JS 파일의 try-catch, console.log 패턴 탐지."""
        plan = _make_execution_plan([(js_file, "javascript")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        js_ctx = ctx.file_contexts[0]
        pattern_types = {p.pattern_type for p in js_ctx.patterns}
        assert "error_handling" in pattern_types  # try, catch
        assert "logging" in pattern_types  # console.log, console.error

    @pytest.mark.asyncio
    async def test_sql_transaction_pattern(self, agent, sql_file):
        """SQL 파일의 COMMIT 트랜잭션 패턴 탐지."""
        plan = _make_execution_plan([(sql_file, "sql")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        sql_ctx = ctx.file_contexts[0]
        pattern_types = {p.pattern_type for p in sql_ctx.patterns}
        assert "transaction" in pattern_types  # COMMIT

    @pytest.mark.asyncio
    async def test_common_patterns_aggregated(self, agent, c_file, js_file):
        """common_patterns가 전체 파일에 걸쳐 집계된다."""
        plan = _make_execution_plan([(c_file, "c"), (js_file, "javascript")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        # 두 파일 모두 error_handling, logging 패턴이 있음
        assert ctx.common_patterns["error_handling"] >= 2
        assert ctx.common_patterns["logging"] >= 2


# ──────────────────────────────────────────────
# LLM 보정 테스트
# ──────────────────────────────────────────────


class TestLLMRefinement:
    """LLM 보정 테스트."""

    @pytest.mark.asyncio
    async def test_llm_target_file_applied(self, agent, c_file, h_file):
        """LLM이 제안한 target_file이 호출에 적용된다."""
        plan = _make_execution_plan([(c_file, "c"), (h_file, "c")])
        agent._llm_client.chat.return_value = _make_llm_response([
            {
                "file": c_file,
                "language": "c",
                "imports": [],
                "calls": [
                    {
                        "function_name": "log_error",
                        "line": 7,
                        "target_file": h_file,
                    }
                ],
                "patterns": [],
            }
        ])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        c_ctx = next(fc for fc in ctx.file_contexts if fc.file == c_file)
        log_call = next(
            (c for c in c_ctx.calls if c.function_name == "log_error"),
            None,
        )
        assert log_call is not None
        assert log_call.target_file == h_file

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, agent, c_file):
        """LLM 호출 실패 시 Tool 결과를 그대로 사용."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.side_effect = Exception("LLM API error")

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        # Tool 결과가 그대로 반환됨
        assert len(ctx.file_contexts) == 1
        assert ctx.file_contexts[0].file == c_file
        assert ctx.file_contexts[0].language == "c"
        assert len(ctx.file_contexts[0].imports) >= 1

    @pytest.mark.asyncio
    async def test_llm_invalid_json_graceful(self, agent, c_file):
        """LLM이 유효하지 않은 JSON을 반환하면 Tool 결과 사용."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.return_value = "this is not json"

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)
        assert len(ctx.file_contexts) == 1

    @pytest.mark.asyncio
    async def test_llm_empty_response_uses_tool(self, agent, c_file):
        """LLM이 빈 file_contexts를 반환하면 Tool 결과 유지."""
        plan = _make_execution_plan([(c_file, "c")])
        agent._llm_client.chat.return_value = json.dumps({
            "file_contexts": [],
            "dependencies": {"edges": [], "has_circular": False, "warnings": []},
            "common_patterns": {},
        })

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)
        assert len(ctx.file_contexts) == 1
        assert len(ctx.file_contexts[0].imports) >= 1


# ──────────────────────────────────────────────
# FileContext 스키마 검증 테스트
# ──────────────────────────────────────────────


class TestFileContextSchema:
    """FileContext 스키마 검증 테스트."""

    @pytest.mark.asyncio
    async def test_valid_schema_returned(self, agent, c_file, js_file):
        """반환값이 FileContext 스키마와 일치."""
        plan = _make_execution_plan([(c_file, "c"), (js_file, "javascript")])
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        assert len(ctx.file_contexts) == 2
        for fc in ctx.file_contexts:
            assert fc.file
            assert fc.language in ("javascript", "c", "proc", "sql")
            assert isinstance(fc.imports, list)
            assert isinstance(fc.calls, list)
            assert isinstance(fc.patterns, list)

    @pytest.mark.asyncio
    async def test_dependencies_preserved(self, agent, c_file, h_file):
        """ExecutionPlan의 dependencies가 FileContext에 그대로 전달된다."""
        plan = _make_execution_plan([(c_file, "c"), (h_file, "c")])
        plan["dependencies"] = {
            "edges": [
                {"source": c_file, "target": h_file, "type": "include"},
            ],
            "has_circular": False,
            "warnings": [],
        }
        agent._llm_client.chat.return_value = _make_llm_response([])

        result = await agent.run(execution_plan=plan)
        ctx = FileContext.model_validate(result)

        assert len(ctx.dependencies.edges) == 1
        assert ctx.dependencies.edges[0].source == c_file
        assert ctx.dependencies.edges[0].target == h_file


# ──────────────────────────────────────────────
# 내부 메서드 테스트
# ──────────────────────────────────────────────


class TestInternalMethods:
    """내부 메서드 단위 테스트."""

    def test_aggregate_patterns(self):
        """패턴 집계."""
        agent = ContextCollectorAgent()
        file_contexts = [
            {
                "patterns": [
                    {"pattern_type": "error_handling", "line": 1},
                    {"pattern_type": "logging", "line": 2},
                ]
            },
            {
                "patterns": [
                    {"pattern_type": "error_handling", "line": 5},
                    {"pattern_type": "memory_management", "line": 10},
                ]
            },
        ]
        result = ContextCollectorAgent._aggregate_patterns(file_contexts)
        assert result == {
            "error_handling": 2,
            "logging": 1,
            "transaction": 0,
            "memory_management": 1,
        }

    def test_merge_calls_target_file_applied(self):
        """LLM이 target_file을 보강하면 적용된다."""
        tool_calls = [
            {"function_name": "log_error", "line": 7, "target_file": None},
        ]
        llm_calls = [
            {"function_name": "log_error", "line": 7, "target_file": "/a/common.h"},
        ]
        result = ContextCollectorAgent._merge_calls(tool_calls, llm_calls)
        assert result[0]["target_file"] == "/a/common.h"

    def test_merge_calls_llm_only_added(self):
        """LLM에만 있는 호출도 추가된다."""
        tool_calls = [
            {"function_name": "malloc", "line": 5, "target_file": None},
        ]
        llm_calls = [
            {"function_name": "custom_func", "line": 20, "target_file": "/a/b.c"},
        ]
        result = ContextCollectorAgent._merge_calls(tool_calls, llm_calls)
        assert len(result) == 2
        names = {c["function_name"] for c in result}
        assert "malloc" in names
        assert "custom_func" in names

    def test_merge_patterns_dedup(self):
        """중복 패턴이 제거된다."""
        tool_patterns = [
            {"pattern_type": "logging", "description": "printf", "line": 10},
        ]
        llm_patterns = [
            {"pattern_type": "logging", "description": "printf call", "line": 10},
            {"pattern_type": "error_handling", "description": "if check", "line": 5},
        ]
        result = ContextCollectorAgent._merge_patterns(tool_patterns, llm_patterns)
        assert len(result) == 2  # 중복 제거됨

    def test_merge_patterns_invalid_type_filtered(self):
        """유효하지 않은 패턴 유형은 필터링된다."""
        tool_patterns = []
        llm_patterns = [
            {"pattern_type": "invalid_type", "description": "...", "line": 1},
            {"pattern_type": "logging", "description": "...", "line": 2},
        ]
        result = ContextCollectorAgent._merge_patterns(tool_patterns, llm_patterns)
        assert len(result) == 1
        assert result[0]["pattern_type"] == "logging"


# ──────────────────────────────────────────────
# Agent 초기화 테스트
# ──────────────────────────────────────────────


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_params(self):
        agent = ContextCollectorAgent()
        assert agent.model == "gpt-5-mini"
        assert agent.fallback_model == "gpt-5"
        assert agent.temperature == 0.0

    def test_custom_params(self):
        agent = ContextCollectorAgent(
            model="custom-model",
            fallback_model="custom-fallback",
            temperature=0.5,
        )
        assert agent.model == "custom-model"
        assert agent.fallback_model == "custom-fallback"
        assert agent.temperature == 0.5
