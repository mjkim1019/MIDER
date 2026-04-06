"""CommentRemover 단위 테스트.

언어별 주석 제거 동작과 줄번호 보존 원칙을 검증한다.
"""

import pytest

from mider.tools.base_tool import ToolExecutionError, ToolResult
from mider.tools.preprocessing.comment_remover import CommentRemover


@pytest.fixture
def remover() -> CommentRemover:
    """CommentRemover 인스턴스."""
    return CommentRemover()


# ──────────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────────


def _cleaned(result: ToolResult) -> str:
    """ToolResult에서 정제된 코드 문자열을 반환한다."""
    return result.data["content"]


def _removed(result: ToolResult) -> int:
    """ToolResult에서 제거된 주석 수를 반환한다."""
    return result.data["removed_count"]


# ──────────────────────────────────────────────
# JavaScript 테스트
# ──────────────────────────────────────────────


class TestJavaScript:
    def test_javascript_line_comment(self, remover: CommentRemover) -> None:
        """// 한 줄 주석이 제거된다."""
        code = "var x = 1; // 이것은 주석\nvar y = 2;\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "// 이것은 주석" not in _cleaned(result)
        assert "var x = 1;" in _cleaned(result)
        assert "var y = 2;" in _cleaned(result)
        assert _removed(result) == 1

    def test_javascript_block_comment(self, remover: CommentRemover) -> None:
        """/* */ 블록 주석이 제거된다."""
        code = "/* 블록 주석 */\nvar x = 1;\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "블록 주석" not in _cleaned(result)
        assert "var x = 1;" in _cleaned(result)
        assert _removed(result) == 1

    def test_javascript_multiline_block_comment_preserves_line_numbers(
        self, remover: CommentRemover
    ) -> None:
        """여러 줄 블록 주석 제거 후 줄 수가 보존된다."""
        code = "var a = 1;\n/*\n  여러 줄\n  블록 주석\n*/\nvar b = 2;\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert code.count("\n") == _cleaned(result).count("\n")
        assert "var a = 1;" in _cleaned(result)
        assert "var b = 2;" in _cleaned(result)
        assert "여러 줄" not in _cleaned(result)

    def test_javascript_url_in_string_preserved(self, remover: CommentRemover) -> None:
        """문자열 내 http:// 는 주석으로 처리되지 않는다."""
        code = 'var url = "http://example.com";\n'
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "http://example.com" in _cleaned(result)
        assert _removed(result) == 0

    def test_javascript_block_comment_in_string_preserved(
        self, remover: CommentRemover
    ) -> None:
        """문자열 내 /* 는 주석으로 처리되지 않는다."""
        code = 'var s = "begin /* not a comment */ end";\n'
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "/* not a comment */" in _cleaned(result)
        assert _removed(result) == 0

    def test_javascript_template_literal_line_comment_preserved(
        self, remover: CommentRemover
    ) -> None:
        """템플릿 리터럴 내 // 는 주석으로 처리되지 않는다."""
        code = "var s = `http://example.com/path`;\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "http://example.com/path" in _cleaned(result)
        assert _removed(result) == 0

    def test_javascript_regex_literal_preserved(self, remover: CommentRemover) -> None:
        """정규식 리터럴 /pattern/g 는 주석으로 처리되지 않는다."""
        code = "var re = /pattern/g;\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        assert "/pattern/g" in _cleaned(result)
        assert _removed(result) == 0

    def test_javascript_inline_comment_after_code(
        self, remover: CommentRemover
    ) -> None:
        """코드 뒤 인라인 주석이 제거되고 코드는 보존된다."""
        code = "var x = 1; // 변수 설명\n"
        result = remover.execute(content=code, language="javascript")
        assert result.success is True
        cleaned = _cleaned(result)
        assert "var x = 1;" in cleaned
        assert "// 변수 설명" not in cleaned
        assert _removed(result) == 1


# ──────────────────────────────────────────────
# C 테스트
# ──────────────────────────────────────────────


class TestC:
    def test_c_line_comment(self, remover: CommentRemover) -> None:
        """C에서 // 한 줄 주석이 제거된다."""
        code = "int x = 1; // 주석\nint y = 2;\n"
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "// 주석" not in _cleaned(result)
        assert "int x = 1;" in _cleaned(result)
        assert _removed(result) == 1

    def test_c_block_comment(self, remover: CommentRemover) -> None:
        """C에서 /* */ 블록 주석이 제거된다."""
        code = "/* 헤더 주석 */\nint x = 0;\n"
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "헤더 주석" not in _cleaned(result)
        assert "int x = 0;" in _cleaned(result)
        assert _removed(result) == 1

    def test_c_url_in_string_preserved(self, remover: CommentRemover) -> None:
        """C 문자열 내 // 는 주석으로 처리되지 않는다."""
        code = 'char *url = "http://example.com";\n'
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "http://example.com" in _cleaned(result)
        assert _removed(result) == 0

    def test_c_char_literal_preserved(self, remover: CommentRemover) -> None:
        """C 문자 리터럴 '/' 는 보존된다."""
        code = "char slash = '/';\n"
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "'/'" in _cleaned(result)
        assert _removed(result) == 0

    def test_c_division_operator_preserved(self, remover: CommentRemover) -> None:
        """C 나눗셈 연산자 a / b 는 보존된다."""
        code = "int result = a / b;\n"
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "a / b" in _cleaned(result)
        assert _removed(result) == 0

    def test_c_escaped_quote_in_string_preserved(
        self, remover: CommentRemover
    ) -> None:
        """이스케이프된 쌍따옴표가 포함된 문자열에서 // 가 보존된다."""
        code = 'char *s = "escaped \\" // not a comment";\n'
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert "// not a comment" in _cleaned(result)
        assert _removed(result) == 0


# ──────────────────────────────────────────────
# Pro*C 테스트
# ──────────────────────────────────────────────


class TestProC:
    def test_proc_c_style_line_comment_removed(self, remover: CommentRemover) -> None:
        """Pro*C에서 // C 스타일 주석이 제거된다."""
        code = "int x = 1; // C 주석\n"
        result = remover.execute(content=code, language="proc")
        assert result.success is True
        assert "// C 주석" not in _cleaned(result)
        assert _removed(result) == 1

    def test_proc_c_style_block_comment_removed(self, remover: CommentRemover) -> None:
        """Pro*C에서 /* */ C 스타일 블록 주석이 제거된다."""
        code = "/* Pro*C 헤더 */\nint x = 0;\n"
        result = remover.execute(content=code, language="proc")
        assert result.success is True
        assert "Pro*C 헤더" not in _cleaned(result)
        assert _removed(result) == 1

    def test_proc_exec_sql_dash_comment_removed(self, remover: CommentRemover) -> None:
        """EXEC SQL 블록 내 -- 주석이 제거된다."""
        code = "EXEC SQL SELECT col -- SQL 주석\n  FROM tbl;\n"
        result = remover.execute(content=code, language="proc")
        assert result.success is True
        assert "SQL 주석" not in _cleaned(result)
        assert "SELECT col" in _cleaned(result)
        assert _removed(result) >= 1

    def test_proc_dash_outside_exec_sql_preserved(
        self, remover: CommentRemover
    ) -> None:
        """EXEC SQL 블록 밖의 -- 는 코드로 유지된다."""
        code = "int x = a--;\n"
        result = remover.execute(content=code, language="proc")
        assert result.success is True
        assert "a--" in _cleaned(result)
        assert _removed(result) == 0


# ──────────────────────────────────────────────
# SQL 테스트
# ──────────────────────────────────────────────


class TestSQL:
    def test_sql_dash_line_comment_removed(self, remover: CommentRemover) -> None:
        """SQL -- 한 줄 주석이 제거된다."""
        code = "SELECT * FROM tbl -- 전체 조회\nWHERE id = 1;\n"
        result = remover.execute(content=code, language="sql")
        assert result.success is True
        assert "전체 조회" not in _cleaned(result)
        assert "SELECT * FROM tbl" in _cleaned(result)
        assert _removed(result) == 1

    def test_sql_block_comment_removed(self, remover: CommentRemover) -> None:
        """SQL /* */ 블록 주석이 제거된다."""
        code = "/* 쿼리 설명 */\nSELECT 1;\n"
        result = remover.execute(content=code, language="sql")
        assert result.success is True
        assert "쿼리 설명" not in _cleaned(result)
        assert "SELECT 1;" in _cleaned(result)
        assert _removed(result) == 1

    def test_sql_dash_in_string_preserved(self, remover: CommentRemover) -> None:
        """SQL 문자열 내 -- 는 주석으로 처리되지 않는다."""
        code = "SELECT 'value -- not comment' FROM dual;\n"
        result = remover.execute(content=code, language="sql")
        assert result.success is True
        assert "value -- not comment" in _cleaned(result)
        assert _removed(result) == 0

    def test_sql_escaped_single_quote(self, remover: CommentRemover) -> None:
        """SQL 이스케이프된 홑따옴표 ('') 가 올바르게 처리된다."""
        code = "SELECT 'it''s fine' FROM dual;\n"
        result = remover.execute(content=code, language="sql")
        assert result.success is True
        assert "it''s fine" in _cleaned(result)
        assert _removed(result) == 0


# ──────────────────────────────────────────────
# XML 테스트
# ──────────────────────────────────────────────


class TestXML:
    def test_xml_comment_removed(self, remover: CommentRemover) -> None:
        """XML <!-- --> 주석이 제거된다."""
        code = "<root><!-- XML 주석 --><child/></root>\n"
        result = remover.execute(content=code, language="xml")
        assert result.success is True
        assert "XML 주석" not in _cleaned(result)
        assert "<root>" in _cleaned(result)
        assert "<child/>" in _cleaned(result)
        assert _removed(result) == 1

    def test_xml_multiline_comment_preserves_line_numbers(
        self, remover: CommentRemover
    ) -> None:
        """여러 줄 XML 주석 제거 후 줄 수가 보존된다."""
        code = "<root>\n<!--\n  여러 줄\n  XML 주석\n-->\n<child/>\n</root>\n"
        result = remover.execute(content=code, language="xml")
        assert result.success is True
        assert code.count("\n") == _cleaned(result).count("\n")
        assert "여러 줄" not in _cleaned(result)
        assert "<child/>" in _cleaned(result)

    def test_xml_cdata_comment_like_preserved(self, remover: CommentRemover) -> None:
        """CDATA 섹션 내 <!-- --> 는 주석으로 처리되지 않는다."""
        code = "<root><![CDATA[<!-- CDATA 내부 -->]]></root>\n"
        result = remover.execute(content=code, language="xml")
        assert result.success is True
        assert "<!-- CDATA 내부 -->" in _cleaned(result)
        assert _removed(result) == 0


# ──────────────────────────────────────────────
# 공통 테스트
# ──────────────────────────────────────────────


class TestCommon:
    def test_empty_string_input(self, remover: CommentRemover) -> None:
        """빈 문자열 입력 시 성공 결과와 빈 콘텐츠를 반환한다."""
        result = remover.execute(content="", language="javascript")
        assert result.success is True
        assert _cleaned(result) == ""
        assert _removed(result) == 0

    def test_whitespace_only_input(self, remover: CommentRemover) -> None:
        """공백만 있는 입력도 그대로 반환한다."""
        result = remover.execute(content="   \n  \n", language="c")
        assert result.success is True
        assert _removed(result) == 0

    def test_no_comments_code_unchanged(self, remover: CommentRemover) -> None:
        """주석이 없는 코드는 변경되지 않는다."""
        code = "int x = 1;\nint y = 2;\nreturn x + y;\n"
        result = remover.execute(content=code, language="c")
        assert result.success is True
        assert _cleaned(result) == code
        assert _removed(result) == 0

    @pytest.mark.parametrize("language", ["javascript", "c", "proc", "sql", "xml"])
    def test_line_count_preserved_after_multiline_block_comment(
        self, remover: CommentRemover, language: str
    ) -> None:
        """각 언어에서 여러 줄 블록 주석 제거 후 줄 수가 보존된다."""
        if language in ("javascript", "c", "proc"):
            code = "line1;\n/*\ncomment line\n*/\nline2;\n"
        elif language == "sql":
            code = "SELECT 1;\n/*\nSQL comment\n*/\nSELECT 2;\n"
        else:  # xml
            code = "<a/>\n<!--\ncomment\n-->\n<b/>\n"

        result = remover.execute(content=code, language=language)
        assert result.success is True
        assert code.count("\n") == _cleaned(result).count("\n"), (
            f"줄 수 불일치 (language={language}): "
            f"원본={code.count(chr(10))}, 결과={_cleaned(result).count(chr(10))}"
        )

    def test_unsupported_language_raises_error(self, remover: CommentRemover) -> None:
        """지원하지 않는 언어는 ToolExecutionError를 발생시킨다."""
        with pytest.raises(ToolExecutionError, match="지원하지 않는 언어"):
            remover.execute(content="some code", language="python")

    def test_unsupported_language_error_tool_name(
        self, remover: CommentRemover
    ) -> None:
        """ToolExecutionError의 tool_name이 comment_remover 이다."""
        with pytest.raises(ToolExecutionError) as exc_info:
            remover.execute(content="some code", language="java")
        assert exc_info.value.tool_name == "comment_remover"

    def test_result_is_tool_result_instance(self, remover: CommentRemover) -> None:
        """반환값이 ToolResult 인스턴스임을 확인한다."""
        result = remover.execute(content="var x = 1;\n", language="javascript")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert "content" in result.data
        assert "removed_count" in result.data


# ──────────────────────────────────────────────
# 엣지 케이스 테스트 (버그 수정 검증)
# ──────────────────────────────────────────────


class TestShebang:
    """Shebang(#!) 처리 테스트."""

    def test_shebang_line_preserved_and_comment_removed(
        self, remover: CommentRemover
    ) -> None:
        """Shebang 줄은 보존되고 이후 주석은 제거된다."""
        code = "#!/usr/bin/env node\n// Author: Kim (사번:12345)\nvar x = 1;\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "#!/usr/bin/env node" in cleaned
        assert "Author" not in cleaned
        assert "var x = 1;" in cleaned
        assert _removed(result) >= 1

    def test_shebang_only_file(self, remover: CommentRemover) -> None:
        """Shebang만 있는 파일은 그대로 반환된다."""
        code = "#!/usr/bin/env node"
        result = remover.execute(content=code, language="javascript")
        assert _cleaned(result) == code
        assert _removed(result) == 0

    def test_shebang_with_block_comment(self, remover: CommentRemover) -> None:
        """Shebang 이후 블록 주석이 정상 제거된다."""
        code = "#!/usr/bin/env node\n/* block comment */\nvar x;\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "#!/usr/bin/env node" in cleaned
        assert "block comment" not in cleaned
        assert "var x;" in cleaned

    def test_shebang_not_applied_to_c(self, remover: CommentRemover) -> None:
        """C 언어에서 #!는 Shebang 처리하지 않는다 (정규식 미지원)."""
        code = "#!invalid\n// comment\nint x;\n"
        result = remover.execute(content=code, language="c")
        assert "// comment" not in _cleaned(result)
        assert _removed(result) >= 1


class TestCRLF:
    """CRLF 줄바꿈 보존 테스트."""

    def test_crlf_preserved_in_js_line_comment(
        self, remover: CommentRemover
    ) -> None:
        """JS 한 줄 주석 제거 시 CRLF가 보존된다."""
        code = "var x = 1; // comment\r\nvar y = 2;\r\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "// comment" not in cleaned
        assert cleaned.count("\r\n") == code.count("\r\n")

    def test_crlf_preserved_in_sql_line_comment(
        self, remover: CommentRemover
    ) -> None:
        """SQL 한 줄 주석 제거 시 CRLF가 보존된다."""
        code = "SELECT 1; -- comment\r\nSELECT 2;\r\n"
        result = remover.execute(content=code, language="sql")
        cleaned = _cleaned(result)
        assert "comment" not in cleaned
        assert cleaned.count("\r\n") == code.count("\r\n")

    def test_crlf_preserved_in_block_comment(
        self, remover: CommentRemover
    ) -> None:
        """블록 주석 내 CRLF가 보존된다."""
        code = "/*\r\nblock\r\ncomment\r\n*/\r\nvar x;\r\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "block" not in cleaned
        assert cleaned.count("\r\n") == code.count("\r\n")

    def test_crlf_preserved_in_xml_comment(
        self, remover: CommentRemover
    ) -> None:
        """XML 주석 내 CRLF가 보존된다."""
        code = "<!--\r\nxml comment\r\n-->\r\n<root/>\r\n"
        result = remover.execute(content=code, language="xml")
        cleaned = _cleaned(result)
        assert "xml comment" not in cleaned
        assert cleaned.count("\r\n") == code.count("\r\n")


class TestTemplateLiteralNesting:
    """JS 템플릿 리터럴 ${} 중첩 테스트."""

    def test_nested_template_literal(self, remover: CommentRemover) -> None:
        """중첩된 템플릿 리터럴 이후 주석이 제거된다."""
        code = "var s = `outer ${`inner`} rest`;\n// comment\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "outer" in cleaned
        assert "inner" in cleaned
        assert "rest" in cleaned
        assert "// comment" not in cleaned

    def test_template_with_braces_in_expression(
        self, remover: CommentRemover
    ) -> None:
        """템플릿 표현식 내 중괄호({}) 이후 주석이 제거된다."""
        code = "var s = `val: ${obj.fn({a: 1})} end`;\n// comment\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "val:" in cleaned
        assert "obj.fn" in cleaned
        assert "// comment" not in cleaned

    def test_template_with_comment_inside_expression(
        self, remover: CommentRemover
    ) -> None:
        """템플릿 표현식 내부의 주석이 제거된다."""
        code = "var s = `${x /* remove this */}`;\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "remove this" not in cleaned
        assert _removed(result) >= 1

    def test_simple_template_expression_preserved(
        self, remover: CommentRemover
    ) -> None:
        """단순 템플릿 표현식은 그대로 보존된다."""
        code = "var s = `hello ${name}!`;\n"
        result = remover.execute(content=code, language="javascript")
        assert _cleaned(result) == code
        assert _removed(result) == 0


class TestAdditionalEdgeCases:
    """추가 엣지 케이스."""

    def test_consecutive_block_comments(self, remover: CommentRemover) -> None:
        """연속된 블록 주석이 모두 제거된다."""
        code = "/* a *//* b */\nvar x;\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "a" not in cleaned.split("\n")[0]
        assert "b" not in cleaned.split("\n")[0]
        assert "var x;" in cleaned
        assert _removed(result) == 2

    def test_comment_after_regex(self, remover: CommentRemover) -> None:
        """정규식 리터럴 뒤 주석이 제거된다."""
        code = "var re = /pattern/g; // comment\n"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "/pattern/g" in cleaned
        assert "// comment" not in cleaned

    def test_file_ending_without_newline(self, remover: CommentRemover) -> None:
        """파일 끝에 줄바꿈 없이 끝나는 주석도 제거된다."""
        code = "var x = 1; // comment"
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "var x = 1;" in cleaned
        assert "// comment" not in cleaned

    def test_string_ending_with_backslash_then_comment(
        self, remover: CommentRemover
    ) -> None:
        r"""문자열 끝 이스케이프 백슬래시 후 주석이 제거된다."""
        code = 'var s = "path\\\\"; // comment\n'
        result = remover.execute(content=code, language="javascript")
        cleaned = _cleaned(result)
        assert "path" in cleaned
        assert "// comment" not in cleaned

    def test_define_inline_comment_removed(self, remover: CommentRemover) -> None:
        """C #define 뒤 인라인 주석이 제거된다."""
        code = "#define MAX 100 // max value\nint x;\n"
        result = remover.execute(content=code, language="c")
        cleaned = _cleaned(result)
        assert "#define MAX 100" in cleaned
        assert "max value" not in cleaned
