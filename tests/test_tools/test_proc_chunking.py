"""T33 ProC 분석 재설계 단위 테스트.

- extract_proc_global_context / build_cursor_lifecycle_map
- classify_proc_functions (함수 패턴 분류)
- SQL 블록 함수 매핑
- _decide_delivery_mode (토큰 기반 분기)
- 단일 호출 / 그룹핑 E2E
"""

import json
import logging
import re
from unittest.mock import AsyncMock

import pytest

from mider.agents.proc_analyzer import ProCAnalyzerAgent
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.utility.token_optimizer import (
    build_cursor_lifecycle_map,
    classify_proc_functions,
    extract_proc_global_context,
    find_function_boundaries,
    group_dispatch_functions,
)


# ──────────────────────────────────────────────
# extract_proc_global_context
# ──────────────────────────────────────────────


class TestExtractProcGlobalContext:
    def test_declare_section(self):
        code = (
            "EXEC SQL BEGIN DECLARE SECTION;\n"
            "  char gs_input[100];\n"
            "EXEC SQL END DECLARE SECTION;\n"
            "void main() { return; }\n"
        )
        assert "gs_input" in extract_proc_global_context(code)

    def test_includes(self):
        code = '#include "pfmcom.h"\nEXEC SQL INCLUDE SQLCA;\nvoid main() { return; }\n'
        result = extract_proc_global_context(code)
        assert "pfmcom.h" in result
        assert "SQLCA" in result

    def test_empty_file(self):
        assert "글로벌 컨텍스트 없음" in extract_proc_global_context("")


# ──────────────────────────────────────────────
# build_cursor_lifecycle_map
# ──────────────────────────────────────────────


class TestBuildCursorLifecycleMap:
    def test_full_lifecycle(self):
        code = (
            "void b10() {\n  EXEC SQL DECLARE C1 CURSOR FOR SELECT 1;\n"
            "  EXEC SQL OPEN C1;\n  return;\n}\n"
            "void b20() {\n  EXEC SQL FETCH C1 INTO :h;\n  return;\n}\n"
            "void b30() {\n  EXEC SQL CLOSE C1;\n  return;\n}\n"
        )
        result = build_cursor_lifecycle_map(code)
        assert "C1" in result
        assert "미발견" not in result

    def test_close_missing(self):
        code = (
            "void b10() {\n  EXEC SQL DECLARE C1 CURSOR FOR SELECT 1;\n"
            "  EXEC SQL OPEN C1;\n  EXEC SQL FETCH C1 INTO :h;\n  return;\n}\n"
        )
        assert "미발견" in build_cursor_lifecycle_map(code)

    def test_no_cursors(self):
        assert "커서 없음" in build_cursor_lifecycle_map("void foo() { return; }\n")


# ──────────────────────────────────────────────
# classify_proc_functions
# ──────────────────────────────────────────────


def _classify(code: str) -> dict:
    """헬퍼: 코드에서 함수 분류."""
    lines = code.splitlines()
    boundaries = find_function_boundaries(lines, "proc")
    pat = re.compile(r"^\s*(?:static\s+)?(?:void|int|char|long)\s+(\w+)\s*\(")
    func_names: dict[int, str] = {}
    for start, _end in boundaries:
        m = pat.match(lines[start - 1])
        if m:
            func_names[start] = m.group(1)
    return classify_proc_functions(code, boundaries, func_names)


class TestClassifyProcFunctions:
    def test_boilerplate(self):
        code = (
            "long main(long argc, char **argv) {\n  return 0;\n}\n"
            "void ord_init_proc() {\n  return;\n}\n"
            "void ord_exit_proc() {\n  return;\n}\n"
        )
        r = _classify(code)
        assert "main" in r["boilerplate"]
        assert "ord_init_proc" in r["boilerplate"]
        assert "ord_exit_proc" in r["boilerplate"]

    def test_dispatch_pattern(self):
        """동일 접두사+번호 3개 이상 → 디스패치."""
        code = "\n".join(
            f"void vrf_work_proc{i}() {{\n  return;\n}}\n"
            for i in range(1, 6)
        )
        r = _classify(code)
        assert len(r["dispatch"]) == 5

    def test_utility_grouping(self):
        code = (
            "void z00_print() {\n  return;\n}\n"
            "void z10_detail() {\n  return;\n}\n"
            "void z99_error() {\n  return;\n}\n"
        )
        r = _classify(code)
        assert len(r["utility_groups"]) >= 1
        z_names = {name for g in r["utility_groups"] for name in g}
        assert "z00_print" in z_names
        assert "z99_error" in z_names


    def test_dispatch_groups_returned(self):
        """classify 결과에 dispatch_groups가 포함되어야 한다."""
        code = "\n".join(
            f"void work_proc{i}() {{\n  return;\n}}\n"
            for i in range(1, 6)
        )
        r = _classify(code)
        assert "dispatch_groups" in r
        # 5개 함수(각 3줄) → 15줄, 1000줄 미만이므로 1그룹
        assert len(r["dispatch_groups"]) == 1
        assert len(r["dispatch_groups"][0]) == 5


# ──────────────────────────────────────────────
# group_dispatch_functions
# ──────────────────────────────────────────────


class TestGroupDispatchFunctions:
    """줄 수 기반 dispatch 그룹핑 테스트."""

    def test_small_functions_single_group(self):
        """모든 함수가 hard_cap 이내면 1그룹."""
        boundaries = [(1, 100), (101, 200), (201, 300)]
        func_names = {1: "f1", 101: "f2", 201: "f3"}
        groups = group_dispatch_functions(
            ["f1", "f2", "f3"], boundaries, func_names,
        )
        assert len(groups) == 1
        assert groups[0] == ["f1", "f2", "f3"]

    def test_split_at_hard_cap(self):
        """hard_cap(1200줄) 초과 시 그룹 분리."""
        # f1=300, f2=500, f3=600, f4=500
        boundaries = [(1, 300), (301, 800), (801, 1400), (1401, 1900)]
        func_names = {1: "f1", 301: "f2", 801: "f3", 1401: "f4"}
        groups = group_dispatch_functions(
            ["f1", "f2", "f3", "f4"], boundaries, func_names,
        )
        # f1+f2=800, +f3=1400>1200 → 그룹1=[f1,f2], 그룹2=[f3,f4]=1100
        assert len(groups) == 2
        assert groups[0] == ["f1", "f2"]
        assert groups[1] == ["f3", "f4"]

    def test_single_oversized_function(self):
        """1500줄 함수 → 단독 그룹."""
        boundaries = [(1, 300), (301, 1800), (1801, 2300)]
        func_names = {1: "f1", 301: "f2", 1801: "f3"}
        groups = group_dispatch_functions(
            ["f1", "f2", "f3"], boundaries, func_names,
        )
        # f1=300, f2=1500(단독), f3=500
        # f1 시작 → +f2: 300+1500=1800>1200 → 그룹1=[f1], 그룹2=[f2](단독), 그룹3=[f3]
        assert len(groups) == 3
        assert groups[0] == ["f1"]
        assert groups[1] == ["f2"]
        assert groups[2] == ["f3"]

    def test_greedy_packing(self):
        """1200줄 이내라면 최대한 묶기."""
        # f1=450, f2=420, f3=160, f4=130 → 합계 1160 ≤ 1200
        boundaries = [(1, 450), (451, 870), (871, 1030), (1031, 1160)]
        func_names = {1: "f1", 451: "f2", 871: "f3", 1031: "f4"}
        groups = group_dispatch_functions(
            ["f1", "f2", "f3", "f4"], boundaries, func_names,
        )
        assert len(groups) == 1
        assert groups[0] == ["f1", "f2", "f3", "f4"]

    def test_empty_input(self):
        """빈 입력 → 빈 결과."""
        groups = group_dispatch_functions([], [], {})
        assert groups == []


# ──────────────────────────────────────────────
# SQL 블록 함수 매핑
# ──────────────────────────────────────────────


class TestSQLBlockFunctionMapping:
    def test_mapped(self, tmp_path):
        content = "void update_order() {\n  EXEC SQL UPDATE T SET X = :v;\n  return;\n}\n"
        f = tmp_path / "test.pc"
        f.write_text(content)
        result = SQLExtractor().execute(file=str(f))
        assert result.data["sql_blocks"][0]["function"] == "update_order"

    def test_outside_function(self, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1 FROM DUAL;\n")
        result = SQLExtractor().execute(file=str(f))
        assert result.data["sql_blocks"][0]["function"] is None


# ──────────────────────────────────────────────
# _decide_delivery_mode
# ──────────────────────────────────────────────


class TestDecideDeliveryMode:
    def test_small_single(self):
        assert ProCAnalyzerAgent._decide_delivery_mode("x" * 100_000) == "single"

    def test_large_grouped(self):
        assert ProCAnalyzerAgent._decide_delivery_mode("x" * 400_000) == "grouped"


# ──────────────────────────────────────────────
# E2E: 단일 호출
# ──────────────────────────────────────────────


def _make_proc_file(tmp_path, num_functions=3, lines_per_func=20):
    parts = [
        '#include <stdio.h>\nEXEC SQL INCLUDE SQLCA;\n'
        'EXEC SQL BEGIN DECLARE SECTION;\n  long gl_ret;\nEXEC SQL END DECLARE SECTION;\n\n'
    ]
    for i in range(num_functions):
        fname = f"func_{i:03d}"
        parts.append(f"void {fname}() {{\n")
        parts.append(f"  EXEC SQL SELECT {i} INTO :gl_ret FROM DUAL;\n")
        for j in range(lines_per_func - 3):
            parts.append(f"  /* {j} */\n")
        parts.append("  return;\n}\n\n")
    f = tmp_path / "test.pc"
    f.write_text("".join(parts))
    return str(f)


class TestSingleCallE2E:
    @pytest.fixture
    def agent(self):
        a = ProCAnalyzerAgent(model="gpt-5")
        a._llm_client = AsyncMock()
        # V3 파이프라인 비활성화 → V1 fallback 테스트
        a._run_v3_pipeline = AsyncMock(side_effect=Exception("V3 disabled"))
        return a

    @pytest.mark.asyncio
    async def test_small_file_single_call(self, agent, tmp_path, caplog):
        pc_file = _make_proc_file(tmp_path, num_functions=3, lines_per_func=20)

        agent._llm_client.chat.side_effect = [
            json.dumps({"risky_functions": [{"function_name": "func_000", "reason": "test"}]}),
            json.dumps({"issues": []}),
        ]

        with caplog.at_level(logging.INFO):
            result = await agent.run(task_id="t1", file=pc_file, language="proc")

        assert result["error"] is None
        assert any("단일 호출" in r.message for r in caplog.records)
        assert agent._llm_client.chat.call_count == 2  # Pass 1 + single

    @pytest.mark.asyncio
    async def test_one_function_no_pass1(self, agent, tmp_path):
        """함수 1개 → Pass 1 건너뛰고 단일 호출."""
        f = tmp_path / "small.pc"
        f.write_text("void foo() {\n  EXEC SQL SELECT 1 FROM DUAL;\n  return;\n}\n")

        agent._llm_client.chat.return_value = json.dumps({"issues": []})
        result = await agent.run(task_id="t1", file=str(f), language="proc")

        assert result["error"] is None
        assert agent._llm_client.chat.call_count == 1


# ──────────────────────────────────────────────
# E2E: 그룹핑 호출
# ──────────────────────────────────────────────


class TestGroupedCallE2E:
    @pytest.fixture
    def agent(self):
        a = ProCAnalyzerAgent(model="gpt-5")
        a._llm_client = AsyncMock()
        # V3 파이프라인 비활성화 → V1 fallback 테스트
        a._run_v3_pipeline = AsyncMock(side_effect=Exception("V3 disabled"))
        return a

    @pytest.mark.asyncio
    async def test_large_file_grouped(self, agent, tmp_path, caplog):
        pc_file = _make_proc_file(tmp_path, num_functions=5, lines_per_func=6000)

        responses = [json.dumps({"risky_functions": []})]
        for _ in range(10):
            responses.append(json.dumps({"issues": []}))
        agent._llm_client.chat.side_effect = responses

        with caplog.at_level(logging.INFO):
            result = await agent.run(task_id="t1", file=pc_file, language="proc")

        assert result["error"] is None
        assert any("스마트 그룹핑" in r.message for r in caplog.records)
