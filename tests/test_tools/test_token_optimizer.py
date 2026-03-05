"""TokenOptimizer 단위 테스트."""

import pytest

from mider.tools.utility.token_optimizer import (
    CodeBlock,
    build_structure_summary,
    extract_error_functions,
    optimize_file_content,
)


# --- extract_error_functions 테스트 ---


class TestExtractErrorFunctionsJS:
    """JavaScript 함수 추출 테스트."""

    def test_extract_function_containing_error(self):
        """에러 라인을 포함하는 함수를 추출한다."""
        code = (
            "function foo() {\n"
            "  const x = 1;\n"
            "  return x;\n"
            "}\n"
            "\n"
            "function bar() {\n"
            "  el.innerHTML = userInput;\n"  # line 7: error
            "  return true;\n"
            "}\n"
        )
        blocks = extract_error_functions(code, [7], "javascript")

        assert len(blocks) == 1
        assert blocks[0].line_start == 6
        assert blocks[0].line_end == 9
        assert "innerHTML" in blocks[0].content

    def test_error_outside_function(self):
        """함수 밖 에러는 ±20줄을 추출한다."""
        lines = [f"// line {i + 1}" for i in range(50)]
        lines[25] = "el.innerHTML = userInput;"
        code = "\n".join(lines)

        blocks = extract_error_functions(code, [26], "javascript")

        assert len(blocks) == 1
        assert blocks[0].line_start == 6  # 26 - 20
        assert blocks[0].line_end == 46  # 26 + 20

    def test_empty_error_lines(self):
        """에러 라인이 비어있으면 빈 리스트."""
        blocks = extract_error_functions("some code", [], "javascript")
        assert blocks == []

    def test_empty_file_content(self):
        """파일 내용이 비어있으면 빈 리스트."""
        blocks = extract_error_functions("", [1], "javascript")
        assert blocks == []

    def test_multiple_errors_same_function(self):
        """같은 함수 내 여러 에러는 한 번만 추출."""
        code = (
            "function foo() {\n"
            "  el.innerHTML = a;\n"  # line 2
            "  el.innerHTML = b;\n"  # line 3
            "  return true;\n"
            "}\n"
        )
        blocks = extract_error_functions(code, [2, 3], "javascript")

        assert len(blocks) == 1

    def test_errors_in_different_functions(self):
        """다른 함수의 에러는 각각 추출."""
        code = (
            "function foo() {\n"
            "  el.innerHTML = a;\n"  # line 2
            "}\n"
            "\n"
            "function bar() {\n"
            "  eval(userInput);\n"  # line 6
            "}\n"
        )
        blocks = extract_error_functions(code, [2, 6], "javascript")

        assert len(blocks) == 2


class TestExtractErrorFunctionsC:
    """C 언어 함수 추출 테스트."""

    def test_extract_c_function(self):
        """C 함수 경계를 인식하여 추출한다."""
        code = (
            "#include <string.h>\n"
            "\n"
            "void process(char *input) {\n"
            "    char buf[10];\n"
            "    strcpy(buf, input);\n"  # line 5: error
            "}\n"
            "\n"
            "int main() {\n"
            "    return 0;\n"
            "}\n"
        )
        blocks = extract_error_functions(code, [5], "c")

        assert len(blocks) == 1
        assert blocks[0].line_start == 3
        assert blocks[0].line_end == 6
        assert "strcpy" in blocks[0].content


class TestExtractErrorFunctionsSQL:
    """SQL 문 추출 테스트."""

    def test_extract_sql_statement(self):
        """에러 포함 SQL 문을 추출한다."""
        code = (
            "SELECT *\n"
            "FROM orders\n"
            "WHERE YEAR(order_date) = 2026;\n"  # line 3: error
            "\n"
            "INSERT INTO logs (msg)\n"
            "VALUES ('ok');\n"
        )
        blocks = extract_error_functions(code, [3], "sql")

        assert len(blocks) == 1
        assert "SELECT" in blocks[0].content
        assert "YEAR" in blocks[0].content
        assert blocks[0].line_start == 1
        assert blocks[0].line_end == 3

    def test_multiple_sql_statements(self):
        """다른 SQL 문의 에러는 각각 추출."""
        code = (
            "SELECT * FROM a;\n"  # line 1
            "\n"
            "UPDATE b SET x = 1;\n"  # line 3
        )
        blocks = extract_error_functions(code, [1, 3], "sql")

        assert len(blocks) == 2


# --- build_structure_summary 테스트 ---


class TestBuildStructureSummary:
    """구조 요약 생성 테스트."""

    def test_basic_summary(self):
        """기본 구조 요약 생성."""
        code = "function foo() {}\nfunction bar() {}\n"
        summary = build_structure_summary(code, None, "javascript")

        assert "2줄" in summary
        assert "javascript" in summary

    def test_with_file_context(self):
        """file_context가 있으면 imports/calls 포함."""
        code = "const x = require('fs');\n"
        context = {
            "imports": [{"module": "fs"}],
            "calls": [{"function": "readFile"}],
            "common_patterns": {"error_handling": 3},
        }
        summary = build_structure_summary(code, context, "javascript")

        assert "fs" in summary
        assert "readFile" in summary
        assert "error_handling" in summary

    def test_with_function_signatures_c(self):
        """C 함수 시그니처를 추출하여 표시."""
        code = (
            "int calculate(int a, int b) {\n"
            "    return a + b;\n"
            "}\n"
            "void process(char *buf) {\n"
            "    return;\n"
            "}\n"
        )
        summary = build_structure_summary(code, None, "c")

        assert "calculate" in summary
        assert "process" in summary

    def test_with_globals_js(self):
        """JS 전역 변수를 추출하여 표시."""
        code = (
            "const API_URL = 'http://api.example.com';\n"
            "let counter = 0;\n"
            "function foo() {}\n"
        )
        summary = build_structure_summary(code, None, "javascript")

        assert "API_URL" in summary
        assert "counter" in summary

    def test_empty_context(self):
        """file_context가 None이어도 동작."""
        code = "int main() { return 0; }\n"
        summary = build_structure_summary(code, None, "c")

        assert "1줄" in summary


# --- optimize_file_content 테스트 ---


class TestOptimizeFileContent:
    """파일 내용 최적화 테스트."""

    def test_short_file_unchanged(self):
        """500줄 이하 파일은 그대로 반환."""
        code = "\n".join([f"line {i}" for i in range(100)])
        result = optimize_file_content(code, None, "javascript")

        assert result == code

    def test_exact_500_lines_unchanged(self):
        """정확히 500줄은 그대로 반환."""
        code = "\n".join([f"line {i}" for i in range(500)])
        result = optimize_file_content(code, None, "javascript")

        assert result == code

    def test_long_file_optimized(self):
        """500줄 초과 파일은 head + tail + 구조요약."""
        lines = [f"line {i}" for i in range(600)]
        code = "\n".join(lines)
        result = optimize_file_content(code, None, "javascript")

        assert "파일 앞부분" in result
        assert "중략" in result
        assert "파일 끝부분" in result
        assert "구조 요약" in result
        # head(200줄)의 첫 번째 줄과 마지막 줄 확인
        assert "line 0" in result
        assert "line 199" in result
        # tail(100줄) 확인
        assert "line 599" in result
        # 생략 줄 수 확인 (600 - 300 = 300)
        assert "300줄 생략" in result

    def test_long_file_with_context(self):
        """긴 파일에 file_context가 있으면 구조 요약에 포함."""
        lines = [f"line {i}" for i in range(600)]
        code = "\n".join(lines)
        context = {"imports": [{"module": "express"}]}
        result = optimize_file_content(code, context, "javascript")

        assert "express" in result
