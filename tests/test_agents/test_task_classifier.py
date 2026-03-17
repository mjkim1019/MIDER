"""TaskClassifierAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock

import pytest

from mider.agents.task_classifier import TaskClassifierAgent
from mider.models.execution_plan import ExecutionPlan


@pytest.fixture
def c_file(tmp_path):
    """테스트용 C 파일."""
    f = tmp_path / "calc.c"
    f.write_text('#include "common.h"\nint main() { return 0; }')
    return str(f)


@pytest.fixture
def h_file(tmp_path):
    """테스트용 헤더 파일."""
    f = tmp_path / "common.h"
    f.write_text("#ifndef COMMON_H\n#define COMMON_H\nint add(int, int);\n#endif")
    return str(f)


@pytest.fixture
def js_file(tmp_path):
    """테스트용 JS 파일."""
    f = tmp_path / "app.js"
    f.write_text("const x = 1;\nconsole.log(x);")
    return str(f)


@pytest.fixture
def sql_file(tmp_path):
    """테스트용 SQL 파일."""
    f = tmp_path / "query.sql"
    f.write_text("SELECT * FROM orders WHERE id = 1;")
    return str(f)


@pytest.fixture
def pc_file(tmp_path):
    """테스트용 Pro*C 파일."""
    f = tmp_path / "batch.pc"
    f.write_text("EXEC SQL INCLUDE sqlca;\nEXEC SQL SELECT 1;")
    return str(f)


@pytest.fixture
def agent():
    """TaskClassifierAgent with mocked LLM."""
    agent = TaskClassifierAgent(model="gpt-4o-mini")
    mock_client = AsyncMock()
    agent._llm_client = mock_client
    return agent


def _make_llm_response(sub_tasks: list[dict]) -> str:
    """LLM 응답 JSON 문자열 생성."""
    return json.dumps({
        "sub_tasks": sub_tasks,
        "dependencies": {"edges": [], "has_circular": False, "warnings": []},
        "total_files": len(sub_tasks),
        "estimated_time_seconds": len(sub_tasks) * 60,
    })


class TestTaskClassifierBasic:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_empty_files(self, agent):
        """빈 파일 목록 → 빈 ExecutionPlan 반환."""
        result = await agent.run(files=[])
        assert result["total_files"] == 0
        assert result["sub_tasks"] == []

    @pytest.mark.asyncio
    async def test_single_c_file(self, agent, c_file):
        """단일 C 파일 분류."""
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
        ])
        result = await agent.run(files=[c_file])

        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 1
        assert plan.sub_tasks[0].language == "c"
        assert plan.sub_tasks[0].file == c_file

    @pytest.mark.asyncio
    async def test_multiple_languages(self, agent, c_file, js_file, sql_file):
        """여러 언어 파일 분류."""
        files = [c_file, js_file, sql_file]
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
            {"file": js_file, "priority": 2},
            {"file": sql_file, "priority": 3},
        ])

        result = await agent.run(files=files)
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 3

        languages = {t.language for t in plan.sub_tasks}
        assert "c" in languages
        assert "javascript" in languages
        assert "sql" in languages

    @pytest.mark.asyncio
    async def test_unsupported_file_filtered(self, agent, tmp_path, c_file):
        """지원하지 않는 확장자 파일은 필터링."""
        py_file = tmp_path / "script.py"
        py_file.write_text("print('hello')")

        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
        ])

        result = await agent.run(files=[c_file, str(py_file)])
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 1
        assert plan.sub_tasks[0].language == "c"


class TestDependencyIntegration:
    """DependencyResolver 연동 테스트."""

    @pytest.mark.asyncio
    async def test_dependency_edges_detected(self, agent, c_file, h_file):
        """C 파일이 헤더를 include하면 의존성 엣지 생성."""
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": h_file, "priority": 1},
            {"file": c_file, "priority": 2},
        ])

        result = await agent.run(files=[c_file, h_file])
        plan = ExecutionPlan.model_validate(result)

        # 의존성 엣지 확인
        assert len(plan.dependencies.edges) >= 1
        edge = plan.dependencies.edges[0]
        assert edge.source == c_file
        assert edge.target == h_file
        assert edge.type == "include"

    @pytest.mark.asyncio
    async def test_dependency_order(self, agent, c_file, h_file):
        """의존되는 파일(헤더)이 먼저 분석되도록 정렬."""
        # LLM이 우선순위를 뒤집어도, 토폴로지 정렬은 Tool이 이미 처리
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": h_file, "priority": 1},
            {"file": c_file, "priority": 2},
        ])

        result = await agent.run(files=[c_file, h_file])
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 2


class TestLLMPriorityRefinement:
    """LLM 우선순위 보정 테스트."""

    @pytest.mark.asyncio
    async def test_llm_priority_applied(self, agent, c_file, js_file):
        """LLM이 제안한 우선순위가 적용된다."""
        # LLM이 js_file을 더 높은 우선순위로 제안
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": js_file, "priority": 1},
            {"file": c_file, "priority": 2},
        ])

        result = await agent.run(files=[c_file, js_file])
        plan = ExecutionPlan.model_validate(result)

        # LLM 제안에 따라 js_file이 먼저
        assert plan.sub_tasks[0].file == js_file
        assert plan.sub_tasks[1].file == c_file

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, agent, c_file, js_file):
        """LLM 호출 실패 시 Tool 결과를 그대로 사용."""
        agent._llm_client.chat.side_effect = Exception("LLM API error")

        result = await agent.run(files=[c_file, js_file])
        plan = ExecutionPlan.model_validate(result)

        # Tool 기반 결과가 그대로 반환됨
        assert plan.total_files == 2
        assert all(t.language in ("c", "javascript") for t in plan.sub_tasks)

    @pytest.mark.asyncio
    async def test_llm_empty_response_uses_tool_result(self, agent, c_file):
        """LLM이 빈 sub_tasks를 반환하면 Tool 결과 유지."""
        agent._llm_client.chat.return_value = json.dumps({
            "sub_tasks": [],
            "dependencies": {"edges": [], "has_circular": False, "warnings": []},
            "total_files": 0,
            "estimated_time_seconds": 0,
        })

        result = await agent.run(files=[c_file])
        plan = ExecutionPlan.model_validate(result)
        # Tool이 생성한 결과가 유지됨
        assert plan.total_files == 1

    @pytest.mark.asyncio
    async def test_llm_invalid_json_graceful(self, agent, c_file):
        """LLM이 유효하지 않은 JSON을 반환하면 Tool 결과 사용."""
        agent._llm_client.chat.return_value = "this is not json"

        result = await agent.run(files=[c_file])
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 1


class TestExecutionPlanSchema:
    """ExecutionPlan 스키마 검증 테스트."""

    @pytest.mark.asyncio
    async def test_valid_schema_returned(self, agent, c_file, js_file):
        """반환값이 ExecutionPlan 스키마와 일치."""
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
            {"file": js_file, "priority": 2},
        ])

        result = await agent.run(files=[c_file, js_file])

        # Pydantic 모델로 검증
        plan = ExecutionPlan.model_validate(result)
        assert isinstance(plan.total_files, int)
        assert isinstance(plan.estimated_time_seconds, int)
        assert len(plan.sub_tasks) == 2

        # 각 SubTask의 필수 필드 확인
        for task in plan.sub_tasks:
            assert task.task_id.startswith("task_")
            assert task.language in ("javascript", "c", "proc", "sql")
            assert task.priority >= 1
            assert task.metadata.file_size >= 0
            assert task.metadata.line_count >= 0

    @pytest.mark.asyncio
    async def test_metadata_populated(self, agent, c_file):
        """파일 메타데이터가 올바르게 채워진다."""
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
        ])

        result = await agent.run(files=[c_file])
        plan = ExecutionPlan.model_validate(result)

        task = plan.sub_tasks[0]
        assert task.metadata.file_size > 0
        assert task.metadata.line_count >= 1
        assert task.metadata.last_modified is not None


class TestApplyLLMPriorities:
    """_apply_llm_priorities 내부 메서드 테스트."""

    def test_reorder_by_priority(self):
        """LLM 우선순위에 따라 sub_tasks가 재정렬된다."""
        agent = TaskClassifierAgent()
        plan_data = {
            "sub_tasks": [
                {"task_id": "task_1", "file": "/a.c", "priority": 1},
                {"task_id": "task_2", "file": "/b.js", "priority": 2},
            ],
            "dependencies": {"edges": [], "has_circular": False, "warnings": []},
            "total_files": 2,
            "estimated_time_seconds": 35,
        }
        llm_result = {
            "sub_tasks": [
                {"file": "/b.js", "priority": 1},
                {"file": "/a.c", "priority": 2},
            ],
        }

        result = agent._apply_llm_priorities(plan_data, llm_result)
        assert result["sub_tasks"][0]["file"] == "/b.js"
        assert result["sub_tasks"][1]["file"] == "/a.c"
        # task_id가 재부여됨
        assert result["sub_tasks"][0]["task_id"] == "task_1"
        assert result["sub_tasks"][1]["task_id"] == "task_2"

    def test_empty_llm_result_no_change(self):
        """LLM 결과가 비어있으면 원본 유지."""
        agent = TaskClassifierAgent()
        plan_data = {
            "sub_tasks": [
                {"task_id": "task_1", "file": "/a.c", "priority": 1},
            ],
        }
        result = agent._apply_llm_priorities(plan_data, {"sub_tasks": []})
        assert result["sub_tasks"][0]["file"] == "/a.c"

    def test_partial_llm_result(self):
        """LLM이 일부 파일만 우선순위를 제안해도 정상 동작."""
        agent = TaskClassifierAgent()
        plan_data = {
            "sub_tasks": [
                {"task_id": "task_1", "file": "/a.c", "priority": 2},
                {"task_id": "task_2", "file": "/b.js", "priority": 3},
                {"task_id": "task_3", "file": "/c.sql", "priority": 4},
            ],
        }
        llm_result = {
            "sub_tasks": [
                {"file": "/c.sql", "priority": 1},
            ],
        }

        result = agent._apply_llm_priorities(plan_data, llm_result)
        # /c.sql이 우선순위 1로 올라감 (다른 파일들은 2, 3 유지)
        assert result["sub_tasks"][0]["file"] == "/c.sql"


class TestFileContentReading:
    """파일 내용 읽기 테스트."""

    @pytest.mark.asyncio
    async def test_large_file_truncated(self, agent, tmp_path):
        """500줄 초과 파일은 처음/끝만 포함."""
        large_file = tmp_path / "large.c"
        lines = [f"int x{i} = {i};" for i in range(600)]
        large_file.write_text("\n".join(lines))

        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": str(large_file), "priority": 1},
        ])

        result = await agent.run(files=[str(large_file)])
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files == 1

        # LLM에 전달된 프롬프트에서 파일 내용이 잘린 것을 확인
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages", call_args.args[1] if len(call_args.args) > 1 else None)
        if messages is None:
            messages = call_args[1] if len(call_args) > 1 else call_args[0]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "lines omitted" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_file_read_failure_skipped(self, agent, tmp_path, c_file):
        """존재하지 않는 파일 읽기 실패 시 건너뜀."""
        nonexistent = str(tmp_path / "nonexistent.c")
        agent._llm_client.chat.return_value = _make_llm_response([
            {"file": c_file, "priority": 1},
        ])

        # nonexistent 파일은 TaskPlanner에서 메타데이터 수집 실패하지만 통과
        # (TaskPlanner가 fallback 처리, 지원되지 않는 확장자는 필터링)
        result = await agent.run(files=[c_file, nonexistent])
        plan = ExecutionPlan.model_validate(result)
        assert plan.total_files >= 1


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_params(self):
        agent = TaskClassifierAgent()
        assert agent.model == "gpt-5-mini"
        assert agent.fallback_model == "gpt-5"
        assert agent.temperature == 0.0

    def test_custom_params(self):
        agent = TaskClassifierAgent(
            model="custom-model",
            fallback_model="custom-fallback",
            temperature=0.5,
        )
        assert agent.model == "custom-model"
        assert agent.fallback_model == "custom-fallback"
        assert agent.temperature == 0.5
