"""프롬프트 템플릿 검증 테스트.

15개 프롬프트 파일의 존재 여부, 로드, 변수 치환을 검증한다.
"""

import pytest

from mider.config.prompt_loader import PROMPTS_DIR, load_prompt


# 모든 프롬프트 파일 목록
ALL_PROMPTS = [
    "orchestrator",
    "task_classifier",
    "context_collector",
    "js_analyzer_error_focused",
    "js_analyzer_heuristic",
    "c_analyzer_error_focused",
    "c_analyzer_heuristic",
    "proc_analyzer_error_focused",
    "proc_analyzer_heuristic",
    "sql_analyzer_error_focused",
    "sql_analyzer_heuristic",
    "reporter",
    "c_prescan_fewshot",
    "xml_analyzer_error_focused",
    "xml_analyzer_heuristic",
    "proc_analyzer_function",
    "proc_prescan",
]

# 각 프롬프트의 필수 변수 매핑
PROMPT_VARIABLES = {
    "orchestrator": {
        "file_list": "test.c\ntest.js",
        "current_phase": "0",
        "previous_results": "없음",
    },
    "task_classifier": {
        "file_list": "/app/test.c",
        "file_contents": "int main() {}",
    },
    "context_collector": {
        "execution_plan": '{"sub_tasks": []}',
        "file_contents": "int main() {}",
    },
    "js_analyzer_error_focused": {
        "eslint_errors": '[{"line": 1}]',
        "file_path": "/app/test.js",
        "structure_summary": "[파일 정보] 1줄, 언어: javascript",
        "error_functions": "const x = 1;",
        "file_context": '{"imports": []}',
    },
    "js_analyzer_heuristic": {
        "file_path": "/app/test.js",
        "file_content_optimized": "const x = 1;",
    },
    "c_analyzer_error_focused": {
        "clang_tidy_warnings": '[{"line": 1}]',
        "file_path": "/app/test.c",
        "structure_summary": "[파일 정보] 1줄, 언어: c",
        "error_functions": "int main() {}",
        "file_context": '{"imports": []}',
    },
    "c_analyzer_heuristic": {
        "file_path": "/app/test.c",
        "file_content_optimized": "int main() {}",
    },
    "proc_analyzer_error_focused": {
        "proc_errors": '[{"line": 1}]',
        "sql_blocks": '[{"sql": "SELECT 1"}]',
        "scanner_findings": "없음",
        "file_path": "/app/test.pc",
        "structure_summary": "[파일 정보] 1줄, 언어: proc",
        "error_functions": "EXEC SQL SELECT 1;",
        "file_context": '{"imports": []}',
    },
    "proc_analyzer_heuristic": {
        "sql_blocks": '[{"sql": "SELECT 1"}]',
        "file_path": "/app/test.pc",
        "file_content_optimized": "EXEC SQL SELECT 1;",
    },
    "sql_analyzer_error_focused": {
        "static_patterns": '[{"type": "full_table_scan"}]',
        "file_path": "/app/test.sql",
        "file_context": '{"imports": []}',
        "file_content": "SELECT * FROM orders;",
        "syntax_errors": '[{"line": 1, "rule": "missing_from"}]',
        "explain_plan": "",
    },
    "sql_analyzer_heuristic": {
        "file_path": "/app/test.sql",
        "file_content": "SELECT * FROM orders;",
        "explain_plan": "",
    },
    "reporter": {
        "analysis_results": '[{"task_id": "task_1"}]',
        "generated_at": "2026-02-27T00:00:00",
        "session_id": "20260227_000000",
    },
    "c_prescan_fewshot": {
        "file_path": "/app/test.c",
        "total_functions": "10",
        "total_findings": "5",
        "function_findings_summary": "### 함수: foo (2개 패턴)",
        "all_functions_summary": "[L1-L30] int foo(...) — 30줄",
    },
    "xml_analyzer_error_focused": {
        "file_path": "/app/screen.xml",
        "parse_errors": "[]",
        "duplicate_ids": "[]",
        "missing_handlers": "[]",
        "data_lists": "[]",
        "events": "[]",
        "js_file": "없음",
    },
    "xml_analyzer_heuristic": {
        "file_path": "/app/screen.xml",
        "data_lists": "[]",
        "events": "[]",
        "component_ids": "[]",
        "js_file": "없음",
    },
    "proc_analyzer_function": {
        "global_context": "(글로벌 컨텍스트)",
        "cursor_lifecycle_map": "(커서 없음)",
        "structure_summary": "[파일 정보] 100줄, 언어: proc",
        "function_code": "void foo() { }",
        "function_sql_blocks": "(없음)",
        "function_scanner_findings": "(없음)",
        "function_proc_errors": "(없음)",
        "file_path": "/app/test.pc",
    },
    "proc_prescan": {
        "file_path": "/app/test.pc",
        "total_functions": "10",
        "total_findings": "3",
        "all_functions_summary": "[L1-L30] void foo(...) — 30줄",
        "function_findings_summary": "### foo\n- SQLCA 미검사 L20",
        "cursor_lifecycle_map": "(커서 없음)",
    },
}


class TestPromptFilesExist:
    """모든 프롬프트 파일이 존재하는지 검증."""

    @pytest.mark.parametrize("name", ALL_PROMPTS)
    def test_prompt_file_exists(self, name):
        prompt_path = PROMPTS_DIR / f"{name}.txt"
        assert prompt_path.exists(), f"프롬프트 파일 없음: {prompt_path}"

    def test_total_prompt_count(self):
        txt_files = list(PROMPTS_DIR.glob("*.txt"))
        assert len(txt_files) == 17, f"프롬프트 파일 수: {len(txt_files)} (기대: 17)"


class TestPromptLoad:
    """프롬프트 파일이 정상적으로 로드되는지 검증."""

    @pytest.mark.parametrize("name", ALL_PROMPTS)
    def test_load_prompt(self, name):
        variables = PROMPT_VARIABLES[name]
        result = load_prompt(name, **variables)
        assert isinstance(result, str)
        assert len(result) > 100, f"프롬프트 내용이 너무 짧음: {len(result)} chars"


class TestPromptVariableSubstitution:
    """변수 치환이 올바르게 작동하는지 검증."""

    def test_orchestrator_substitution(self):
        result = load_prompt("orchestrator", **PROMPT_VARIABLES["orchestrator"])
        assert "test.c" in result
        assert "test.js" in result

    def test_task_classifier_substitution(self):
        result = load_prompt(
            "task_classifier", **PROMPT_VARIABLES["task_classifier"]
        )
        assert "/app/test.c" in result
        assert "int main()" in result

    def test_js_analyzer_error_focused_substitution(self):
        result = load_prompt(
            "js_analyzer_error_focused",
            **PROMPT_VARIABLES["js_analyzer_error_focused"],
        )
        assert "/app/test.js" in result
        assert "const x = 1;" in result  # error_functions
        assert "eslint" in result.lower() or "ESLint" in result

    def test_c_analyzer_error_focused_substitution(self):
        result = load_prompt(
            "c_analyzer_error_focused",
            **PROMPT_VARIABLES["c_analyzer_error_focused"],
        )
        assert "/app/test.c" in result
        assert "clang-tidy" in result or "clang_tidy" in result

    def test_proc_analyzer_error_focused_substitution(self):
        result = load_prompt(
            "proc_analyzer_error_focused",
            **PROMPT_VARIABLES["proc_analyzer_error_focused"],
        )
        assert "/app/test.pc" in result
        assert "SQLCA" in result or "sqlca" in result

    def test_sql_analyzer_error_focused_substitution(self):
        result = load_prompt(
            "sql_analyzer_error_focused",
            **PROMPT_VARIABLES["sql_analyzer_error_focused"],
        )
        assert "/app/test.sql" in result
        assert "SELECT * FROM orders;" in result  # error_functions

    def test_reporter_substitution(self):
        result = load_prompt("reporter", **PROMPT_VARIABLES["reporter"])
        assert "20260227_000000" in result
        assert "2026-02-27T00:00:00" in result


class TestPromptContent:
    """프롬프트 내용의 핵심 요소가 포함되어 있는지 검증."""

    def test_orchestrator_has_phases(self):
        result = load_prompt("orchestrator", **PROMPT_VARIABLES["orchestrator"])
        assert "Phase 0" in result
        assert "Phase 1" in result
        assert "Phase 2" in result
        assert "Phase 3" in result

    def test_task_classifier_has_language_rules(self):
        result = load_prompt(
            "task_classifier", **PROMPT_VARIABLES["task_classifier"]
        )
        assert "javascript" in result
        assert "proc" in result
        assert "sql" in result

    def test_context_collector_has_pattern_types(self):
        result = load_prompt(
            "context_collector", **PROMPT_VARIABLES["context_collector"]
        )
        assert "error_handling" in result
        assert "memory_management" in result

    def test_js_analyzer_has_security_patterns(self):
        result = load_prompt(
            "js_analyzer_heuristic",
            **PROMPT_VARIABLES["js_analyzer_heuristic"],
        )
        assert "innerHTML" in result
        assert "XSS" in result or "xss" in result

    def test_c_analyzer_has_memory_patterns(self):
        result = load_prompt(
            "c_analyzer_heuristic",
            **PROMPT_VARIABLES["c_analyzer_heuristic"],
        )
        assert "malloc" in result
        assert "free" in result
        assert "strcpy" in result

    def test_proc_analyzer_has_sqlca(self):
        result = load_prompt(
            "proc_analyzer_heuristic",
            **PROMPT_VARIABLES["proc_analyzer_heuristic"],
        )
        assert "SQLCA" in result or "sqlca" in result
        assert "INDICATOR" in result or "indicator" in result

    def test_sql_analyzer_has_performance_patterns(self):
        result = load_prompt(
            "sql_analyzer_heuristic",
            **PROMPT_VARIABLES["sql_analyzer_heuristic"],
        )
        assert "Full Table Scan" in result or "full_table_scan" in result
        assert "인덱스" in result

    def test_error_focused_has_structure_summary(self):
        """Error-Focused 프롬프트에 structure_summary 변수가 포함되는지 검증 (SQL 제외)."""
        for name in [
            "js_analyzer_error_focused",
            "c_analyzer_error_focused",
            "proc_analyzer_error_focused",
        ]:
            result = load_prompt(name, **PROMPT_VARIABLES[name])
            assert "파일 정보" in result, f"{name}: structure_summary 미치환"

    def test_reporter_has_risk_assessment(self):
        result = load_prompt("reporter", **PROMPT_VARIABLES["reporter"])
        assert "deployment_risk" in result
        assert "deployment_allowed" in result
        assert "CRITICAL" in result

    def test_all_analyzer_prompts_have_json_output(self):
        """모든 analyzer 프롬프트가 JSON 출력 형식을 포함하는지 검증."""
        analyzer_prompts = [
            "js_analyzer_error_focused",
            "js_analyzer_heuristic",
            "c_analyzer_error_focused",
            "c_analyzer_heuristic",
            "proc_analyzer_error_focused",
            "proc_analyzer_heuristic",
            "sql_analyzer_error_focused",
            "sql_analyzer_heuristic",
        ]
        for name in analyzer_prompts:
            result = load_prompt(name, **PROMPT_VARIABLES[name])
            assert "issue_id" in result, f"{name}: issue_id 출력 형식 누락"
            assert "severity" in result, f"{name}: severity 출력 형식 누락"
            assert "issues" in result, f"{name}: issues 출력 형식 누락"

    def test_all_prompts_have_korean_instruction(self):
        """모든 프롬프트에 한국어 지시사항이 포함되어 있는지 검증."""
        for name in ALL_PROMPTS:
            result = load_prompt(name, **PROMPT_VARIABLES[name])
            has_korean = any("\uac00" <= c <= "\ud7a3" for c in result)
            assert has_korean, f"{name}: 한국어 내용 없음"
