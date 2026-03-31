"""T33 ProC 함수별 청킹 관련 단위 테스트.

- extract_proc_global_context()
- build_cursor_lifecycle_map()
- SQL 블록 함수 매핑
- ProCAnalyzerAgent 2-Pass E2E
- 진행률 로그 25% 출력
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.proc_analyzer import ProCAnalyzerAgent
from mider.tools.base_tool import ToolResult
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.utility.token_optimizer import (
    build_cursor_lifecycle_map,
    extract_proc_global_context,
)


# ──────────────────────────────────────────────
# extract_proc_global_context 테스트
# ──────────────────────────────────────────────


class TestExtractProcGlobalContext:
    """extract_proc_global_context() 테스트."""

    def test_declare_section_extracted(self):
        """DECLARE SECTION 블록이 추출된다."""
        code = (
            "#include <stdio.h>\n"
            "EXEC SQL INCLUDE SQLCA;\n"
            "\n"
            "EXEC SQL BEGIN DECLARE SECTION;\n"
            "  char gs_input[100];\n"
            "  long gl_ret;\n"
            "EXEC SQL END DECLARE SECTION;\n"
            "\n"
            "void main() {\n"
            "  return;\n"
            "}\n"
        )
        result = extract_proc_global_context(code)
        assert "gs_input" in result
        assert "gl_ret" in result
        assert "BEGIN DECLARE SECTION" in result

    def test_includes_extracted(self):
        """#include와 EXEC SQL INCLUDE가 추출된다."""
        code = (
            '#include "pfmcom.h"\n'
            "#include <stdio.h>\n"
            "EXEC SQL INCLUDE SQLCA;\n"
            "\n"
            "void main() { return; }\n"
        )
        result = extract_proc_global_context(code)
        assert "pfmcom.h" in result
        assert "SQLCA" in result

    def test_global_variables_extracted(self):
        """함수 밖 전역 변수가 추출된다."""
        code = (
            "char gc_proc_cd[2];\n"
            "long gl_count;\n"
            "\n"
            "void foo() {\n"
            "  int local_var = 0;\n"
            "  return;\n"
            "}\n"
        )
        result = extract_proc_global_context(code)
        assert "gc_proc_cd" in result
        assert "gl_count" in result
        # 함수 내부 변수는 제외
        assert "local_var" not in result

    def test_empty_file(self):
        """빈 파일이면 기본 메시지."""
        result = extract_proc_global_context("")
        assert "글로벌 컨텍스트 없음" in result

    def test_no_declare_section(self):
        """DECLARE SECTION이 없어도 include/전역변수는 추출."""
        code = (
            "#include <stdio.h>\n"
            "int g_count;\n"
            "\n"
            "void foo() { return; }\n"
        )
        result = extract_proc_global_context(code)
        assert "stdio.h" in result
        assert "g_count" in result


# ──────────────────────────────────────────────
# build_cursor_lifecycle_map 테스트
# ──────────────────────────────────────────────


class TestBuildCursorLifecycleMap:
    """build_cursor_lifecycle_map() 테스트."""

    def test_full_lifecycle(self):
        """DECLARE/OPEN/FETCH/CLOSE 정상 매핑."""
        code = (
            "void b10_declare() {\n"
            "  EXEC SQL DECLARE C_read CURSOR FOR SELECT * FROM orders;\n"
            "  EXEC SQL OPEN C_read;\n"
            "  return;\n"
            "}\n"
            "\n"
            "void b20_fetch() {\n"
            "  EXEC SQL FETCH C_read INTO :h_id;\n"
            "  return;\n"
            "}\n"
            "\n"
            "void b30_close() {\n"
            "  EXEC SQL CLOSE C_read;\n"
            "  return;\n"
            "}\n"
        )
        result = build_cursor_lifecycle_map(code)
        assert "C_read" in result
        assert "b10_declare" in result
        assert "b20_fetch" in result
        assert "b30_close" in result
        assert "미발견" not in result

    def test_close_missing(self):
        """CLOSE 누락 시 ⚠ 미발견 표시."""
        code = (
            "void b10_func() {\n"
            "  EXEC SQL DECLARE C_order CURSOR FOR SELECT 1;\n"
            "  EXEC SQL OPEN C_order;\n"
            "  EXEC SQL FETCH C_order INTO :h_val;\n"
            "  return;\n"
            "}\n"
        )
        result = build_cursor_lifecycle_map(code)
        assert "C_order" in result
        assert "미발견" in result

    def test_no_cursors(self):
        """커서 없는 파일."""
        code = (
            "void foo() {\n"
            "  EXEC SQL SELECT 1 INTO :h_val FROM DUAL;\n"
            "  return;\n"
            "}\n"
        )
        result = build_cursor_lifecycle_map(code)
        assert "커서 없음" in result

    def test_multiple_cursors(self):
        """여러 커서 동시 추적."""
        code = (
            "void a() {\n"
            "  EXEC SQL DECLARE C_a CURSOR FOR SELECT 1;\n"
            "  EXEC SQL OPEN C_a;\n"
            "  EXEC SQL DECLARE C_b CURSOR FOR SELECT 2;\n"
            "  EXEC SQL OPEN C_b;\n"
            "  return;\n"
            "}\n"
            "void b() {\n"
            "  EXEC SQL FETCH C_a INTO :h;\n"
            "  EXEC SQL CLOSE C_a;\n"
            "  EXEC SQL FETCH C_b INTO :h;\n"
            "  EXEC SQL CLOSE C_b;\n"
            "  return;\n"
            "}\n"
        )
        result = build_cursor_lifecycle_map(code)
        assert "C_a:" in result
        assert "C_b:" in result
        assert "미발견" not in result


# ──────────────────────────────────────────────
# SQL 블록 함수 매핑 테스트
# ──────────────────────────────────────────────


class TestSQLBlockFunctionMapping:
    """SQLExtractor의 function 필드 매핑 테스트."""

    def setup_method(self):
        self.extractor = SQLExtractor()

    def test_sql_inside_function_mapped(self, tmp_path):
        """함수 내부 SQL → function 필드에 함수명 매핑."""
        content = (
            "void update_order() {\n"
            "  EXEC SQL UPDATE ORDERS SET STATUS = :h_status;\n"
            "  return;\n"
            "}\n"
        )
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert block["function"] == "update_order"

    def test_sql_outside_function_none(self, tmp_path):
        """함수 밖 SQL → function=None."""
        content = "EXEC SQL SELECT 1 FROM DUAL;\n"
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert block["function"] is None

    def test_multiple_functions_correct_mapping(self, tmp_path):
        """여러 함수에서 각 SQL이 올바른 함수에 매핑."""
        content = (
            "void func_a() {\n"
            "  EXEC SQL SELECT 1 INTO :h FROM DUAL;\n"
            "  return;\n"
            "}\n"
            "\n"
            "void func_b() {\n"
            "  EXEC SQL UPDATE T SET X = :v;\n"
            "  return;\n"
            "}\n"
        )
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        blocks = result.data["sql_blocks"]
        assert len(blocks) == 2
        assert blocks[0]["function"] == "func_a"
        assert blocks[1]["function"] == "func_b"


# ──────────────────────────────────────────────
# ProCAnalyzerAgent 2-Pass E2E 테스트
# ──────────────────────────────────────────────


def _make_large_proc_file(tmp_path, num_functions=5, lines_per_func=120):
    """함수 N개, 각 120줄 (>500줄 총합)의 대형 Pro*C 파일 생성."""
    parts = [
        '#include <stdio.h>\n',
        'EXEC SQL INCLUDE SQLCA;\n',
        '\n',
        'EXEC SQL BEGIN DECLARE SECTION;\n',
        '  char gs_input[100];\n',
        '  long gl_ret;\n',
        'EXEC SQL END DECLARE SECTION;\n',
        '\n',
    ]
    for i in range(num_functions):
        fname = f"func_{i:03d}"
        parts.append(f"void {fname}() {{\n")
        parts.append(f"  EXEC SQL SELECT {i} INTO :gl_ret FROM DUAL;\n")
        if i % 2 == 0:
            # SQLCA 체크 누락
            parts.append(f"  /* no sqlca check for func_{i:03d} */\n")
        else:
            parts.append("  if (sqlca.sqlcode != 0) return;\n")
        # 패딩
        for j in range(lines_per_func - 4):
            parts.append(f"  /* line {j} of {fname} */\n")
        parts.append("  return;\n")
        parts.append("}\n\n")

    f = tmp_path / "large.pc"
    f.write_text("".join(parts))
    return str(f)


class TestFunctionChunkedPath:
    """2-Pass 함수별 청킹 경로 테스트."""

    @pytest.fixture
    def agent(self):
        agent = ProCAnalyzerAgent(model="gpt-5")
        agent._llm_client = AsyncMock()
        return agent

    @pytest.mark.asyncio
    async def test_large_file_uses_chunked_path(self, agent, tmp_path, caplog):
        """함수 ≥2 AND >500줄 → 2-Pass 경로 진입."""
        pc_file = _make_large_proc_file(tmp_path, num_functions=5, lines_per_func=120)

        # Pass 1 응답: 2개 위험 함수 선별
        pass1_response = json.dumps({
            "risky_functions": [
                {"function_name": "func_000", "reason": "SQLCA 미검사"},
                {"function_name": "func_002", "reason": "SQLCA 미검사"},
            ]
        })
        # Pass 2 응답: 각 함수에서 1개 이슈
        pass2_response = json.dumps({
            "issues": [
                {
                    "issue_id": "PC-001",
                    "category": "data_integrity",
                    "severity": "critical",
                    "title": "SQLCA 미검사",
                    "description": "테스트",
                    "location": {"file": pc_file, "line_start": 10, "line_end": 12},
                    "fix": {"before": "a", "after": "b", "description": "fix"},
                    "source": "llm",
                }
            ]
        })

        agent._llm_client.chat.side_effect = [
            pass1_response,  # Pass 1
            pass2_response,  # Pass 2 func_000
            pass2_response,  # Pass 2 func_002
        ]

        with caplog.at_level(logging.INFO):
            result = await agent.run(task_id="t1", file=pc_file, language="proc")

        assert result["error"] is None
        assert len(result["issues"]) == 2
        # issue_id 재번호 확인
        assert result["issues"][0]["issue_id"] == "PC-001"
        assert result["issues"][1]["issue_id"] == "PC-002"
        # 2-Pass 경로 로그 확인
        assert any("2-Pass 함수별 청킹" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_small_file_uses_single_path(self, agent, tmp_path):
        """≤500줄 → 기존 단일 LLM 호출."""
        content = (
            '#include <stdio.h>\n'
            'void foo() {\n'
            '  EXEC SQL SELECT 1 FROM DUAL;\n'
            '  return;\n'
            '}\n'
        )
        f = tmp_path / "small.pc"
        f.write_text(content)

        agent._llm_client.chat.return_value = json.dumps({"issues": []})

        result = await agent.run(task_id="t1", file=str(f), language="proc")

        assert result["error"] is None
        # 단일 호출 (Pass 1 없음)
        assert agent._llm_client.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_pass1_no_risky_fallback(self, agent, tmp_path, caplog):
        """Pass 1에서 위험 함수 0개 → Heuristic fallback."""
        pc_file = _make_large_proc_file(tmp_path, num_functions=5, lines_per_func=120)

        # Pass 1: 위험 함수 없음
        agent._llm_client.chat.side_effect = [
            json.dumps({"risky_functions": []}),  # Pass 1
            json.dumps({"issues": []}),           # Heuristic fallback
        ]

        with caplog.at_level(logging.INFO):
            result = await agent.run(task_id="t1", file=pc_file, language="proc")

        assert result["error"] is None
        assert any("위험 함수 없음" in r.message for r in caplog.records)


class TestProgressLog:
    """진행률 25% 로그 출력 테스트."""

    @pytest.fixture
    def agent(self):
        agent = ProCAnalyzerAgent(model="gpt-5")
        agent._llm_client = AsyncMock()
        return agent

    @pytest.mark.asyncio
    async def test_progress_logged_at_milestones(self, agent, tmp_path, caplog):
        """4개 함수 분석 시 25%/50%/75%/100% 로그 출력."""
        pc_file = _make_large_proc_file(tmp_path, num_functions=4, lines_per_func=140)

        risky = [
            {"function_name": f"func_{i:03d}", "reason": "test"}
            for i in range(4)
        ]
        pass1_response = json.dumps({"risky_functions": risky})
        pass2_response = json.dumps({"issues": []})

        agent._llm_client.chat.side_effect = [
            pass1_response,
            pass2_response,
            pass2_response,
            pass2_response,
            pass2_response,
        ]

        with caplog.at_level(logging.INFO):
            await agent.run(task_id="t1", file=pc_file, language="proc")

        progress_logs = [r for r in caplog.records if "진행:" in r.message]
        # 4개 함수면 25/50/75/100% = 4번
        assert len(progress_logs) == 4
        assert any("25%" in r.message for r in progress_logs)
        assert any("100%" in r.message for r in progress_logs)
