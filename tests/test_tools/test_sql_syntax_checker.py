"""SQLSyntaxChecker 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.sql_syntax_checker import SQLSyntaxChecker


class TestSQLSyntaxChecker:
    def setup_method(self):
        self.checker = SQLSyntaxChecker()

    # ── 기본 동작 ────────────────────────────────

    def test_empty_file_no_errors(self, tmp_path):
        """빈 파일은 오류 없음."""
        f = tmp_path / "empty.sql"
        f.write_text("")
        result = self.checker.execute(file=str(f))
        assert result.success is True
        assert result.data["syntax_errors"] == []
        assert result.data["warnings"] == []

    def test_valid_sql_no_errors(self, tmp_path):
        """유효한 SQL은 오류 없음."""
        f = tmp_path / "valid.sql"
        f.write_text("SELECT id, name FROM users WHERE id = 1;\n")
        result = self.checker.execute(file=str(f))
        assert result.success is True
        assert result.data["syntax_errors"] == []

    def test_missing_file_param(self):
        """file 파라미터 누락 시 ToolExecutionError."""
        with pytest.raises(ToolExecutionError):
            self.checker.execute()

    def test_nonexistent_file(self):
        """존재하지 않는 파일 시 ToolExecutionError."""
        with pytest.raises(ToolExecutionError):
            self.checker.execute(file="/nonexistent/query.sql")

    # ── 괄호 불일치 ──────────────────────────────

    def test_unmatched_open_paren(self, tmp_path):
        """여는 괄호만 있으면 오류."""
        f = tmp_path / "paren.sql"
        f.write_text("SELECT COUNT( FROM orders;\n")
        result = self.checker.execute(file=str(f))
        errors = result.data["syntax_errors"]
        assert any(e["rule"] == "unmatched_paren" for e in errors)

    def test_unmatched_close_paren(self, tmp_path):
        """닫는 괄호만 있으면 오류."""
        f = tmp_path / "paren2.sql"
        f.write_text("SELECT id) FROM orders;\n")
        result = self.checker.execute(file=str(f))
        errors = result.data["syntax_errors"]
        assert any(e["rule"] == "unmatched_paren" for e in errors)

    def test_matched_parens_no_error(self, tmp_path):
        """괄호가 정상 매칭이면 오류 없음."""
        f = tmp_path / "ok.sql"
        f.write_text("SELECT COUNT(*) FROM orders WHERE id IN (1, 2, 3);\n")
        result = self.checker.execute(file=str(f))
        paren_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "unmatched_paren"]
        assert paren_errors == []

    def test_parens_in_string_ignored(self, tmp_path):
        """문자열 내 괄호는 무시."""
        f = tmp_path / "str.sql"
        f.write_text("SELECT '(' FROM dual;\n")
        result = self.checker.execute(file=str(f))
        paren_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "unmatched_paren"]
        assert paren_errors == []

    def test_parens_in_comment_ignored(self, tmp_path):
        """주석 내 괄호는 무시."""
        f = tmp_path / "comment.sql"
        f.write_text("-- SELECT (\nSELECT 1 FROM dual;\n")
        result = self.checker.execute(file=str(f))
        paren_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "unmatched_paren"]
        assert paren_errors == []

    def test_parens_in_block_comment_ignored(self, tmp_path):
        """블록 주석 내 괄호는 무시."""
        f = tmp_path / "block.sql"
        f.write_text("/* ( */ SELECT 1 FROM dual;\n")
        result = self.checker.execute(file=str(f))
        paren_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "unmatched_paren"]
        assert paren_errors == []

    # ── 따옴표 미닫힘 ────────────────────────────

    def test_unclosed_single_quote(self, tmp_path):
        """작은따옴표 미닫힘."""
        f = tmp_path / "quote.sql"
        f.write_text("SELECT * FROM orders WHERE name = 'hello;\n")
        result = self.checker.execute(file=str(f))
        errors = result.data["syntax_errors"]
        assert any(e["rule"] == "unclosed_quote" for e in errors)

    def test_escaped_quotes_ok(self, tmp_path):
        """이스케이프된 따옴표 '' 는 오류 아님."""
        f = tmp_path / "esc.sql"
        f.write_text("SELECT * FROM orders WHERE name = 'it''s ok';\n")
        result = self.checker.execute(file=str(f))
        quote_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "unclosed_quote"]
        assert quote_errors == []

    # ── SELECT without FROM ──────────────────────

    def test_select_without_from(self, tmp_path):
        """SELECT에 FROM이 없으면 오류."""
        f = tmp_path / "nofrom.sql"
        f.write_text("SELECT id, name;\n")
        result = self.checker.execute(file=str(f))
        errors = result.data["syntax_errors"]
        assert any(e["rule"] == "missing_from" for e in errors)

    def test_select_sysdate_no_from_ok(self, tmp_path):
        """SELECT SYSDATE는 FROM 없어도 정상."""
        f = tmp_path / "sysdate.sql"
        f.write_text("SELECT SYSDATE;\n")
        result = self.checker.execute(file=str(f))
        from_errors = [e for e in result.data["syntax_errors"] if e["rule"] == "missing_from"]
        assert from_errors == []

    # ── UPDATE/DELETE without WHERE ──────────────

    def test_update_without_where_warning(self, tmp_path):
        """UPDATE에 WHERE 없으면 경고."""
        f = tmp_path / "upd.sql"
        f.write_text("UPDATE orders SET status = 'DONE';\n")
        result = self.checker.execute(file=str(f))
        warnings = result.data["warnings"]
        assert any(w["rule"] == "update_without_where" for w in warnings)

    def test_delete_without_where_warning(self, tmp_path):
        """DELETE에 WHERE 없으면 경고."""
        f = tmp_path / "del.sql"
        f.write_text("DELETE FROM orders;\n")
        result = self.checker.execute(file=str(f))
        warnings = result.data["warnings"]
        assert any(w["rule"] == "delete_without_where" for w in warnings)

    def test_update_with_where_no_warning(self, tmp_path):
        """UPDATE에 WHERE 있으면 경고 없음."""
        f = tmp_path / "upd_ok.sql"
        f.write_text("UPDATE orders SET status = 'DONE' WHERE id = 1;\n")
        result = self.checker.execute(file=str(f))
        update_warnings = [w for w in result.data["warnings"] if w["rule"] == "update_without_where"]
        assert update_warnings == []
